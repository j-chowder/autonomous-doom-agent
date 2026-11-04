"""Step 5 tests. MOCK agents (tests/fake_role_cli.py): no model, no Claude Code. What is REAL: the Coordinator, the Step 4
controller, git worktrees, verify.py scope/fingerprint/tests, handoff validation, accepted refs, the journal.
The real-agent run is tests/demo_step5.py --docker (needs docker-agent + Claude Code on your Mac)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from loop import handoff  # noqa: E402
from loop.coordinator import Coordinator  # noqa: E402
from loop.state import Store  # noqa: E402
from step5_fixture import git, make_policy, make_repo  # noqa: E402


class Goal(unittest.TestCase):
    def run_goal(self, env=None, again=False, **budgets):
        if not hasattr(self, "tmp"):
            self.tmp = Path(tempfile.mkdtemp(prefix="loop-step5-"))
            self.addCleanup(shutil.rmtree, str(self.tmp), True)
            self.main, self.base = make_repo(self.tmp)
            (self.tmp / "fake").mkdir()
            self.policy = make_policy(self.tmp, self.base, **budgets)
        with mock.patch.dict(os.environ, dict({"FAKE_DIR": str(self.tmp / "fake")}, **(env or {}))):
            store = Store(self.main)
            try:
                return Coordinator(store, self.policy).run()
            finally:
                store.close()

    def calls(self):
        p = self.tmp / "fake" / "calls.log"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def journal(self):
        store = Store(self.main)
        try:
            return store.journal.read()
        finally:
            store.close()

    def ev(self, etype):
        return [e for e in self.journal() if e["type"] == etype]

    def report(self, result):
        return json.loads(Path(result["report"]).read_text())

    def refs(self):
        """{task id: accepted sha} for every task accepted in this repo."""
        lines = git(self.main, "for-each-ref", "--format=%(refname) %(objectname)", "refs/loop/accepted").splitlines()
        return {l.split(" ")[0].split("/")[-1]: l.split(" ")[1] for l in lines}

    def coder_refs(self):
        return {k: v for k, v in self.refs().items() if "." not in k and not k.startswith("PLAN")}


class HappyPath(Goal):
    def test_1_goal_to_complete_with_four_distinct_roles(self):
        r = self.run_goal()
        self.assertEqual(r["status"], "complete", r)
        rep = self.report(r)
        self.assertEqual({k: v["status"] for k, v in rep["tasks"].items() if "." not in k and k != "PLAN-1"}, {"T1": "VERIFIED", "T2": "VERIFIED", "T3": "VERIFIED"})
        agents = {c["agent"] for c in self.calls()}
        self.assertEqual(agents, {"planner", "coder", "reviewer", "tester"})
        self.assertEqual({k: v["tasks"] for k, v in rep["roles"].items()}, {"planner": 1, "coder": 3, "reviewer": 4, "tester": 3})
        self.assertEqual(len(self.ev("plan_admitted")), 1)
        self.assertEqual(self.ev("final_gate")[0]["decision"], "PASS")
        self.assertEqual(len(self.ev("tester_evidence")), 3)
        self.assertTrue(all(p["result"] == "pass" for e in self.ev("tester_evidence") for p in e["proposals"]))
        self.assertEqual(set(self.coder_refs()), {"T1", "T2", "T3"})
        self.assertTrue((Path(r["report"]).parent / "summary.md").is_file())
        self.assertTrue((Path(r["report"]).parent / "handoffs" / "PLAN-1.json").is_file())

    def test_2_reviewer_and_tester_start_from_the_accepted_commit_without_coder_logs(self):
        self.run_goal()
        rev = [c for c in self.calls() if c["agent"] == "reviewer"][0]["prompt"]
        self.assertIn("ROLE: reviewer", rev)
        self.assertIn("CHANGED FILES: src/calc.py", rev)
        self.assertNotIn("fake coder done", rev)  # nothing from the coder's own run reaches the reviewer

    def test_3_restart_replays_without_new_agent_calls_or_duplicate_decisions(self):
        self.run_goal()
        n_calls, n_events = len(self.calls()), len(self.journal())
        r = self.run_goal()
        self.assertEqual(r["status"], "complete")
        self.assertEqual(len(self.calls()), n_calls)          # nothing was re-run
        self.assertEqual(len(self.ev("plan_admitted")), 1)    # decisions are journaled once
        self.assertEqual(len(self.ev("final_gate")), 1)
        self.assertLessEqual(len(self.journal()) - n_events, 4)  # only controller/coordinator start+stop markers


class Failures(Goal):
    def test_4_coding_regression_is_repaired_from_feedback(self):
        r = self.run_goal({"FAKE_CODER": "regress_mul"})
        self.assertEqual(r["status"], "complete", r)
        t2 = [c for c in self.calls() if c["agent"] == "coder" and c["task"] == "T2"]
        self.assertEqual(len(t2), 2)
        self.assertNotIn("Feedback from the previous attempt", t2[0]["prompt"])
        self.assertIn("Feedback from the previous attempt", t2[1]["prompt"])
        decisions = [(e["decision"], e["classification"]) for e in self.ev("verification") if e["task_id"] == "T2"]
        self.assertEqual(decisions, [("FAIL", "test_failed"), ("PASS", "tests_passed")])
        self.assertEqual(self.ev("replan_triggered"), [])     # a coding failure is NOT a replan

    def test_5_blocked_task_triggers_one_bounded_replan_and_keeps_accepted_work(self):
        r = self.run_goal({"FAKE_PLAN": "blocked_once"})
        self.assertEqual(r["status"], "complete", r)
        rep = self.report(r)
        self.assertEqual(rep["tasks"]["T3"]["status"], "BLOCKED")
        self.assertEqual(rep["tasks"]["T4"]["status"], "VERIFIED")
        rp = self.ev("replan_triggered")
        self.assertEqual(len(rp), 1)
        self.assertEqual((rp[0]["trigger"], rp[0]["superseded"]), ("task_blocked", ["T3"]))
        self.assertEqual(len([c for c in self.calls() if c["agent"] == "planner"]), 2)
        plan2 = [c for c in self.calls() if c["task"] == "PLAN-2"][0]["prompt"]
        self.assertIn("THIS IS A REPLAN", plan2)
        self.assertIn("T1 (add)", plan2)                      # accepted history reaches the planner
        self.assertIn("outside_allowed", plan2)               # so does the failure evidence
        self.assertEqual(set(self.coder_refs()), {"T1", "T2", "T4"})   # T3 was never accepted; T1/T2 are still there
        self.assertEqual(self.coder_refs()["T1"], rep["tasks"]["T1"]["verified_sha"])

    def test_6_replan_budget_exhausted_stops_with_a_clear_reason(self):
        r = self.run_goal({"FAKE_PLAN": "blocked_once"}, max_replans=0)
        self.assertEqual(r["status"], "replan_budget_exhausted:task_blocked:T3", r)
        self.assertEqual(self.ev("final_gate"), [])           # never declared complete
        self.assertEqual(self.report(r)["tasks"]["T2"]["status"], "VERIFIED")  # accepted work is still there

    def test_7_invalid_plan_is_returned_with_errors_and_retried(self):
        r = self.run_goal({"FAKE_PLAN": "invalid_once"})
        self.assertEqual(r["status"], "complete", r)
        p1 = [c for c in self.calls() if c["task"] == "PLAN-1"]
        self.assertEqual(len(p1), 2)
        self.assertIn("no_such_profile", p1[1]["prompt"])      # the validator's errors are the retry feedback
        self.assertIn("controller-owned verification profile", p1[1]["prompt"])

    def test_8_reviewer_that_edits_code_is_rejected_and_cannot_block_acceptance(self):
        r = self.run_goal({"FAKE_REVIEW": "rogue"})
        rep = self.report(r)
        self.assertEqual(r["status"], "complete_with_open_findings")
        self.assertEqual(rep["tasks"]["T1.review"]["status"], "BLOCKED")
        self.assertEqual(rep["tasks"]["T1"]["status"], "VERIFIED")
        self.assertIn("review_unavailable:T1", rep["warnings"])
        self.assertTrue(any(e["classification"] == "scope_violation" for e in self.ev("verification") if e["task_id"] == "T1.review"))


class ReviewAndTests(Goal):
    def test_9_blocking_finding_causes_one_fix_then_a_second_review(self):
        r = self.run_goal({"FAKE_REVIEW": "block_once"})
        self.assertEqual(r["status"], "complete", r)
        rep = self.report(r)
        self.assertEqual(rep["tasks"]["T1.fix1"]["status"], "VERIFIED")
        self.assertEqual(rep["tasks"]["T1.fix1.review"]["status"], "VERIFIED")
        fix = [c for c in self.calls() if c["task"] == "T1.fix1"][0]["prompt"]
        self.assertIn("src/calc.py:1 def add has no docstring", fix)   # the finding's evidence reaches the coder
        self.assertEqual(rep["open_findings"], [])
        self.assertIn('"""Return a + b."""', git(self.main, "show", "%s:src/calc.py" % rep["tasks"]["T1.fix1"]["verified_sha"]))
        self.assertEqual(len(self.ev("review_unresolved")), 0)

    def test_10_failing_tester_proposal_is_evidence_only(self):
        r = self.run_goal({"FAKE_TESTER": "bad_test"})
        rep = self.report(r)
        self.assertEqual(r["status"], "complete_with_open_findings")      # surfaced, never hidden
        self.assertTrue(any(w.startswith("proposed_test_fail:") for w in rep["warnings"]))
        self.assertEqual(self.ev("final_gate")[0]["decision"], "PASS")    # but it did not gate acceptance
        self.assertTrue(all(v["status"] == "VERIFIED" for k, v in rep["tasks"].items() if k in ("T1", "T2", "T3")))


class Restart(Goal):
    def test_13_sigkill_mid_agent_run_then_resume(self):
        """Kill the whole coordinator while the coder is working on T2, restart it, and check nothing is lost or repeated."""
        import signal
        import subprocess
        self.tmp = Path(tempfile.mkdtemp(prefix="loop-step5-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.main, self.base = make_repo(self.tmp)
        (self.tmp / "fake").mkdir()
        pol = self.tmp / "policy.json"
        pol.write_text(json.dumps(make_policy(self.tmp, self.base)))
        cmd = [sys.executable, "-m", "loop.coordinator", str(pol), "--root", str(self.main)]
        env = dict(os.environ, FAKE_DIR=str(self.tmp / "fake"), FAKE_KILL_PARENT="coder:T2", PYTHONPATH=str(REPO))
        a = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(a.returncode, -signal.SIGKILL, a.stderr[-500:])           # the coordinator really died mid-run
        t1_before = self.coder_refs()["T1"]
        self.assertNotIn("T2", self.refs())                                         # T2 was not accepted when it died
        b = subprocess.run(cmd, cwd=str(REPO), env=dict(env, FAKE_KILL_PARENT=""), capture_output=True, text=True, timeout=240)
        self.assertEqual(b.returncode, 0, b.stdout[-800:] + b.stderr[-800:])
        rep = json.loads(Path(json.loads(b.stdout)["report"]).read_text())
        self.assertEqual(rep["status"], "complete")
        self.assertEqual(self.coder_refs()["T1"], t1_before)                        # accepted work untouched
        who = [c["task"] for c in self.calls() if c["agent"] == "coder"]
        self.assertEqual(who.count("T1"), 1)                                        # T1 was not redone
        self.assertEqual(who.count("T2"), 2)                                        # the interrupted attempt + one resumed attempt
        self.assertEqual(len(self.ev("plan_admitted")), 1)                          # no second plan
        self.assertEqual(len(self.ev("task_accepted")), 3)                          # each task accepted once
        self.assertEqual(len([c for c in self.calls() if c["agent"] == "planner"]), 1)
        t2 = rep["tasks"]["T2"]
        self.assertEqual((t2["status"], t2["attempts"]), ("VERIFIED", 2))
        self.assertTrue(any(e["type"] in ("recovered", "recovery_failed") for e in self.journal()))  # recovery ran and was journaled


class Validators(unittest.TestCase):
    POL = dict(max_tasks=3, allowed_roots=["src", "docs"], protected=["tests_trusted"], profiles=["add", "full"], used_ids=["T0"],
               coder_roles={"coder": {"allowed_roots": ["src", "docs"]}})

    def task(self, **kw):
        t = dict(id="T1", title="t", instructions="do it", acceptance="ok", allowed=["src"], profile="add", coder_role="coder", depends_on=[])
        t.update(kw)
        return t

    def errs(self, *tasks):
        return handoff.check_plan({"tasks": list(tasks)}, self.POL)

    def test_11_plan_validation(self):
        self.assertEqual(self.errs(self.task()), [])
        self.assertEqual(self.errs(self.task(), self.task(id="T2", depends_on=["T1"])), [])
        for bad, needle in [
            (self.task(id="T.1"), "no dots"), (self.task(id="T0"), "already used"), (self.task(allowed=["../x"]), "unusable path"),
            (self.task(allowed=["/etc"]), "unusable path"), (self.task(allowed=["tests_trusted/a"]), "protected"),
            (self.task(allowed=["web"]), "outside the approved paths"), (self.task(profile="nope"), "verification profile"),
            (self.task(coder_role="x"), "coder_role"), (self.task(depends_on=["T9"]), "linear chain"), (self.task(title=""), "title"),
        ]:
            self.assertTrue(any(needle in e for e in self.errs(bad)), (needle, self.errs(bad)))
        self.assertTrue(any("repeated" in e for e in self.errs(self.task(), self.task(depends_on=["T1"]))))
        self.assertTrue(any("too many" in e for e in self.errs(*[self.task(id="A%d" % i, depends_on=["A%d" % (i - 1)] if i else []) for i in range(4)])))

    def test_12_review_validation(self):
        ok = {"verdict": "pass", "summary": "s", "findings": []}
        self.assertEqual(handoff.check_review(ok), [])
        blocking = {"id": "F1", "severity": "blocking", "criterion": "c", "evidence": "e", "summary": "s"}
        self.assertEqual(handoff.check_review(dict(ok, verdict="changes_requested", findings=[blocking])), [])
        self.assertTrue(handoff.check_review(dict(ok, findings=[blocking])))                          # pass + blocking
        self.assertTrue(handoff.check_review(dict(ok, verdict="changes_requested")))                  # no blocking finding
        self.assertTrue(handoff.check_review(dict(ok, verdict="changes_requested", findings=[dict(blocking, evidence="")])))  # unsupported claim


if __name__ == "__main__":
    unittest.main()
