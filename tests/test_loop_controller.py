"""Step 4 tests. MOCKED agent: tests/fake_coder_cli.py stands in for docker-agent + Claude Code.
Everything else (state, worktrees, runner, verifier, git) is the real code."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from loop.controller import Controller  # noqa: E402
from loop.state import Store  # noqa: E402
from step4_fixture import git, make_cfg, make_repo  # noqa: E402


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.main, self.base, self.check = make_repo(self.tmp)
        self.fdir = self.tmp / "fake"
        self.fdir.mkdir()

    def cfg(self, **kw):
        return make_cfg(self.tmp, self.base, self.check, **kw)

    def run_ctrl(self, mode, cfg=None):
        with mock.patch.dict(os.environ, FAKE_MODE=mode, FAKE_DIR=str(self.fdir)):
            store = Store(self.main)
            try:
                return Controller(store, cfg or self.cfg()).run()
            finally:
                store.close()

    def events(self, etype=None):
        store = Store(self.main)
        try:
            return [e for e in store.journal.read() if etype is None or e["type"] == etype]
        finally:
            store.close()

    def task(self):
        store = Store(self.main)
        try:
            return store.state["run_id"], store.state["tasks"]["T1"], store.replayed
        finally:
            store.close()

    def prompts(self):
        p = self.fdir / "prompts.log"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def test_1_success_is_accepted_and_preserved(self):
        res = self.run_ctrl("good")
        run_id, t, replayed = self.task()
        self.assertEqual((res["status"], t["status"], t["attempt_seq"]), ("complete", "VERIFIED", 1))
        ver = self.events("verification")
        self.assertEqual([(e["decision"], e["classification"]) for e in ver], [("PASS", "tests_passed")])
        self.assertEqual(git(self.main, "rev-parse", t["verified_sha"] + "^{tree}"), ver[0]["fingerprint"])  # accepted == verified
        self.assertEqual(git(self.main, "rev-parse", "refs/loop/accepted/%s/T1" % run_id), t["verified_sha"])
        self.assertEqual(git(self.main, "show", "%s:src/calc.py" % t["verified_sha"]), "def add(a, b):\n    return a + b")
        self.assertTrue((self.main / "artifacts" / "runs" / run_id / "preserved" / "T1-a1" / "snapshot.bundle").exists())
        self.assertEqual([e["action"] for e in self.events("decision")], ["accept"])
        self.assertEqual(replayed, [])  # state.json already matches the journal

    def test_2_failed_attempt_is_repaired_with_feedback(self):
        res = self.run_ctrl("repair")
        run_id, t, _ = self.task()
        self.assertEqual((res["status"], t["status"], t["attempt_seq"]), ("complete", "VERIFIED", 2))
        self.assertEqual([e["classification"] for e in self.events("verification")], ["test_failed", "tests_passed"])
        self.assertEqual([e["action"] for e in self.events("decision")], ["retry", "accept"])
        first, second = self.prompts()
        self.assertNotIn("Feedback from the previous attempt", first)
        self.assertIn("Feedback from the previous attempt", second)
        self.assertIn("add(2, 3) != 5", second)  # the failing assertion reached the next agent
        wts = [e["worktree"] for e in self.events("intent")]
        self.assertEqual(len(set(wts)), 2)  # fresh worktree per attempt
        self.assertTrue((self.main / "artifacts" / "runs" / run_id / "preserved" / "T1-a1").is_dir())  # attempt 1 kept

    def test_3_scope_violation_feedback_names_the_file(self):
        res = self.run_ctrl("scope_repair")
        self.assertEqual(res["status"], "complete")
        ver = self.events("verification")
        self.assertEqual([e["classification"] for e in ver], ["scope_violation", "tests_passed"])
        self.assertEqual(ver[0]["violations"], ["evil.py"])
        self.assertIn("evil.py", self.prompts()[1])

    def test_4_attempt_budget_exhausted_blocks(self):
        res = self.run_ctrl("always_bad", self.cfg(max_attempts=2))
        run_id, t, _ = self.task()
        self.assertEqual((res["status"], t["status"], t["attempt_seq"]), ("blocked", "BLOCKED", 2))
        self.assertEqual([e["action"] for e in self.events("decision")], ["retry", "block"])
        self.assertEqual(len(self.prompts()), 2)  # no third attempt
        self.assertEqual(git(self.main, "for-each-ref", "refs/loop/accepted"), "")  # nothing accepted

    def test_5_infra_error_stops_and_resume_does_not_rerun_agent(self):
        broken = self.cfg()
        broken["tasks"]["T1"]["test_argv"] = ["/nonexistent/test-runner"]
        res = self.run_ctrl("good", broken)
        _, t, _ = self.task()
        self.assertEqual((res["status"], t["status"]), ("infra_error", "EXECUTED"))  # not accepted, not failed
        self.assertEqual([e["classification"] for e in self.events("verification")], ["test_launch_error"] * 2)
        self.assertEqual(len(self.prompts()), 1)
        res = self.run_ctrl("good")  # infrastructure fixed
        _, t, _ = self.task()
        self.assertEqual((res["status"], t["status"]), ("complete", "VERIFIED"))
        self.assertEqual(len(self.prompts()), 1)  # same attempt re-verified; agent not run again

    def test_6_agent_launch_error_stops_the_loop(self):
        res = self.run_ctrl("good", self.cfg(binary=["/nonexistent/docker-agent"]))
        _, t, _ = self.task()
        self.assertEqual((res["status"], t["status"]), ("infra_error", "FAILED"))
        self.assertEqual(self.events("verification"), [])

    def test_7_runtime_budget_stops_before_starting_an_attempt(self):
        res = self.run_ctrl("good", self.cfg(max_runtime_s=5, agent_timeout_s=10))
        _, t, _ = self.task()
        self.assertEqual((res["status"], t["status"], self.prompts()), ("budget_exhausted", "PENDING", []))

    def test_8_interrupted_attempt_is_recovered_not_accepted(self):
        store = Store(self.main)
        store.add_task("T1", "t", "coder")
        store.begin_attempt("T1", None, self.base)  # simulate a crash mid-attempt
        store.close()
        res = self.run_ctrl("good")
        _, t, _ = self.task()
        self.assertEqual((res["status"], t["status"], t["attempt_seq"]), ("complete", "VERIFIED", 2))
        self.assertEqual(len(self.events("recovered")), 1)

    def test_9_failed_work_that_cannot_be_preserved_is_not_discarded(self):
        lost = {"status": "error", "error": "disk full", "dir": "artifacts/x"}
        with mock.patch.object(Store, "preserve_attempt", return_value=lost):
            res = self.run_ctrl("repair")
        _, t, _ = self.task()
        self.assertEqual((res["status"], t["status"], len(self.prompts())), ("infra_error", "EXECUTED", 1))  # not FAILED, no retry
        self.assertEqual(self.events("decision")[-1]["reason"], "preserve_failed")
        res = self.run_ctrl("repair")  # preservation works again: same attempt re-verified, then repaired
        self.assertEqual(res["status"], "complete")

    def test_10_modified_trusted_test_stops_before_any_agent_runs(self):
        c = self.cfg()
        self.check.write_text(self.check.read_text() + "\n# weakened\n")  # differs from the approved hash
        res = self.run_ctrl("good", c)
        _, t, _ = self.task()
        self.assertEqual((res["status"], t["status"], self.prompts()), ("trusted_tests_modified", "PENDING", []))
        self.assertEqual(self.events("decision")[-1]["reason"], "trusted_tests_modified")


if __name__ == "__main__":
    unittest.main()
