"""Step 5: goal -> plan -> coder / reviewer / tester -> bounded replanning, on top of the Step 4 controller.

There is NO second execution loop. Every agent invocation is a Step 4 task run by `Controller.run()`:

  planner   task PLAN-n         may change only handoff/PLAN-n.json
  coder     task <plan id>      may change only the plan's `allowed` paths; accepted by a controller-owned profile
  reviewer  task <id>.review    may change only handoff/<id>.review.json   (reads the accepted commit)
  tester    task <id>.test      may change only handoff/<id>.test.json and handoff/<id>.test.tests/
  fix       task <id>.fixN      a coder task started from <id>'s accepted commit, only after blocking review findings

Read-only roles are enforced by the existing scope check; their handoff files are validated by loop/handoff.py (the
task's trusted test command), so an invalid plan/review/test report is retried by Step 4 with the errors as feedback.

The coordinator is a pure function of (policy, journal, accepted commits): on restart it replays from the start;
tasks that are already VERIFIED or BLOCKED are skipped, handoffs are re-read from their accepted commits.

Decisions the coordinator makes (all deterministic, all journaled with a `key`):
  - plan admitted            (the validator already checked ids, paths, profiles, chain, task count)
  - replan                   only when a task is BLOCKED, or the chain is done but the final gate fails; at most max_replans
  - review-driven fix        at most review.max_fixes per task, only for blocking findings that cite a criterion and evidence
  - completion               the final gate profile (controller-owned tests) must PASS on the final accepted head
Tester proposals are EVIDENCE ONLY: they never gate acceptance and never become trusted tests without a human-approved,
hash-frozen promotion (loop criteria, section 4).

Limits (accepted for now): strictly sequential; the replay assumes the policy does not change between restarts; a
reviewer's finding is a claim the controller can check for shape, not for truth.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loop import handoff  # noqa: E402
from loop.controller import BUDGETS, Controller, sha256_path  # noqa: E402
from loop.preserve import _out  # noqa: E402
from loop.runner import DROP_ENV, create_worktree  # noqa: E402
from loop.state import Store, write_json_atomic  # noqa: E402
from loop.verify import ERROR, PASS, verify  # noqa: E402

COORD_BUDGETS = dict(max_tasks=6, max_replans=1, max_invocations=40, max_total_runtime_s=7200, proposal_timeout_s=120)
HANDOFF = str(Path(handoff.__file__).resolve())
MAX_PROMPT = 60000
CLEAN_NOTE = ("\nLEAVE NO STRAY FILES: running code creates caches (for example __pycache__, .pytest_cache, node_modules, build output). "
              "Run Python with PYTHONDONTWRITEBYTECODE=1 and delete anything you create outside your allowed paths before you finish. "
              "The controller rejects the run if any other file changed.")


class Stop(Exception):
    """The run must stop now; args[0] is the reason."""


def _tail(path, n=2500):
    try:
        return Path(path).read_bytes()[-n:].decode(errors="replace")
    except OSError:
        return "(not available)"


class Coordinator:
    def __init__(self, store, policy):
        self.store, self.p, self.root = store, policy, store.root
        self.b = {**BUDGETS, **COORD_BUDGETS, **policy.get("budgets", {})}
        self.run_id = store.state["run_id"]
        self.runs = self.root / "artifacts" / "runs" / self.run_id
        self.base_sha = policy.get("base_sha") or _out(self.root, "rev-parse", "HEAD")
        self.rv = {**dict(per_task="all", flagged_paths=[], final=True, max_fixes=1), **policy.get("review", {})}
        self.agents = {**{"planner": "planner", "reviewer": "reviewer", "tester": "tester"}, **policy.get("agents", {})}
        self.cfg = dict(base_sha=self.base_sha, agent=policy["agent"], tasks={})
        self.seen = {e["key"] for e in store.journal.read() if e.get("key")}
        self.t0 = time.monotonic()
        self.plan_ids, self.open_findings, self.warnings, self.head = [], [], [], None

    # ---- journal / budgets --------------------------------------------
    def emit(self, etype, key, **fields):
        """Journal a coordinator decision once (replays do not repeat it)."""
        if key in self.seen:
            return False
        self.seen.add(key)
        self.store._commit(etype, task_id=None, key=key, **fields)
        return True

    def invocations(self):
        return sum(t["attempt_seq"] for t in self.store.state["tasks"].values())

    def remaining(self):
        return self.b["max_total_runtime_s"] - (time.monotonic() - self.t0)

    def show(self, sha, path):
        return _out(self.root, "show", "%s:%s" % (sha, path))

    def save_handoff(self, name, text):
        d = self.runs / "handoffs"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text + "\n")

    # ---- one task through the Step 4 controller ------------------------
    def ensure(self, tid, spec):
        """-> ('VERIFIED', sha) or ('BLOCKED', None). Raises Stop when the controller or a budget stops the run."""
        t = self.store.state["tasks"].get(tid)
        if t and t["status"] in ("VERIFIED", "BLOCKED"):
            return t["status"], t["verified_sha"]
        if self.invocations() >= self.b["max_invocations"]:
            raise Stop("invocation_budget_exhausted")
        if self.remaining() <= self.b["agent_timeout_s"]:
            raise Stop("budget_exhausted")
        self.cfg["tasks"][tid] = spec
        budgets = {**self.b, "max_runtime_s": int(min(self.b["max_runtime_s"], self.remaining()))}
        res = Controller(self.store, dict(self.cfg, budgets=budgets)).run()
        t = self.store.state["tasks"][tid]
        if t["status"] in ("VERIFIED", "BLOCKED"):
            return t["status"], t["verified_sha"]
        raise Stop(res["status"] if res["status"] not in ("blocked", "complete") else "unfinished:" + tid)

    def role_spec(self, role, tid, depends_on, allowed, prompt, extra_files=None, check_args=()):
        out = "handoff/%s.json" % tid
        return dict(
            title="%s %s" % (role, tid), role=role, agent=self.agents[role], depends_on=list(depends_on),
            prompt=(prompt + CLEAN_NOTE)[:MAX_PROMPT], allowed=[out] + list(allowed), protected=list(self.p.get("protected", [])),
            test_argv=[sys.executable, HANDOFF, "check", role, out] + list(check_args),
            test_files={HANDOFF: sha256_path(HANDOFF), **(extra_files or {})})

    # ---- prompts (controller-owned; the barrier between roles is what they leave out) ----
    def objective_block(self):
        return "OBJECTIVE: %s\nCONSTRAINTS: %s" % (self.p["objective"], self.p.get("constraints") or "(none)")

    def coder_prompt(self, pt, extra="", tid=None):
        prot = ", ".join(self.p.get("protected", [])) or "(none)"
        return ("ROLE: coder\nTASK_ID: %s\n%s\nTASK: %s\nINSTRUCTIONS: %s\nACCEPTANCE: %s\n"
                "ALLOWED PATHS (change only these): %s\nDO NOT TOUCH: %s\n"
                "After you finish, the controller runs its own verification (profile %s), then separate reviewer and tester "
                "agents inspect your result. Do not edit the tests the controller owns.%s\n"
                % (tid or pt["id"], self.objective_block(), pt["title"], pt["instructions"], pt["acceptance"],
                   ", ".join(pt["allowed"]), prot, pt["profile"], extra))

    def coder_spec(self, pt, dep, extra="", tid=None):
        prof = self.p["profiles"][pt["profile"]]
        role = pt.get("coder_role", "coder")
        return dict(title=pt["title"], role=role, agent=self.p["coder_roles"][role].get("agent", role),
                    depends_on=[dep] if dep else [], prompt=(self.coder_prompt(pt, extra, tid) + CLEAN_NOTE)[:MAX_PROMPT],
                    allowed=list(pt["allowed"]), protected=list(self.p.get("protected", [])),
                    test_argv=list(prof["test_argv"]), test_files=dict(prof["test_files"]))

    # ---- planning ------------------------------------------------------
    def failure_text(self, kind, ref):
        if kind == "task_blocked":
            fb = sorted((self.runs / "feedback").glob("%s-a*.md" % ref), key=lambda p: int(p.stem.rsplit("-a", 1)[1]))
            return "Task %s was BLOCKED after %d attempts. Latest failure evidence:\n%s" % (
                ref, self.store.state["tasks"][ref]["attempt_seq"], fb[-1].read_text()[-2500:] if fb else "(none)")
        return "All planned tasks were accepted, but the final acceptance gate FAILED:\n%s" % _tail(ref)

    def plan(self, n, failure):
        pid = "PLAN-%d" % n
        out = "handoff/%s.json" % pid
        pol = self.runs / "policies" / (pid + ".json")
        if not pol.exists():  # written once: its hash is frozen into the planner task's test_files
            pol.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(pol, dict(
                max_tasks=self.b["max_tasks"], allowed_roots=self.p["allowed_roots"], protected=self.p.get("protected", []),
                profiles=sorted(self.p["profiles"]), used_ids=sorted(set(self.store.state["tasks"]) | set(self.plan_ids)),
                coder_roles={k: {"allowed_roots": v.get("allowed_roots", self.p["allowed_roots"])} for k, v in self.p["coder_roles"].items()}))
        ref = self.head[1] if self.head else self.base_sha
        files = _out(self.root, "ls-tree", "-r", "--name-only", ref).splitlines()
        lines = ["ROLE: planner. You plan; you do not implement. Edit NOTHING except the output file.",
                 "TASK_ID: " + pid, "OUTPUT_FILE: " + out, self.objective_block(),
                 "ALLOWED_ROOTS (every task's `allowed` paths must be inside these): " + ", ".join(self.p["allowed_roots"]),
                 "PROTECTED (never allowed): " + (", ".join(self.p.get("protected", [])) or "(none)"),
                 "VERIFICATION PROFILES (the controller owns these tests; choose the id that checks each task):"]
        lines += ["  %s: %s" % (k, v.get("description", "")) for k, v in sorted(self.p["profiles"].items())]
        lines += ["MAX_TASKS: %d" % self.b["max_tasks"],
                  "USED_IDS (do not reuse): " + (", ".join(json.loads(pol.read_text())["used_ids"]) or "(none)"),
                  "REPOSITORY FILES at the starting commit (first 200 of %d):" % len(files)] + ["  " + f for f in files[:200]]
        if failure:
            kind, ref2, dropped = failure
            done = [(i, t) for i, t in self.store.state["tasks"].items() if t["status"] == "VERIFIED" and "." not in i and not i.startswith("PLAN")]
            lines += ["", "THIS IS A REPLAN (replan %d of %d). Accepted work is preserved and is the starting point. Plan only the REMAINING work."
                      % (n - 1, self.b["max_replans"]),
                      "ACCEPTED TASKS: " + ("; ".join("%s (%s)" % (i, t["title"]) for i, t in done) or "(none)"),
                      "SUPERSEDED (never started): " + (", ".join(dropped) or "(none)"), self.failure_text(kind, ref2),
                      "Fix the cause (for example a wrong `allowed` path, a task that is too large, or a wrong profile). Do not repeat accepted work."]
        lines += ["", "Write OUTPUT_FILE as JSON in exactly this shape:",
                  '{"tasks": [{"id": "T1", "title": "...", "instructions": "what to implement, concrete", "acceptance": "observable criteria",'
                  ' "allowed": ["src"], "profile": "<profile id>", "coder_role": "coder", "depends_on": []}], "notes": "..."}',
                  "Tasks run in order as a linear chain: depends_on is [] for the first task and [\"<previous id>\"] for every other task.",
                  "Ids use letters, digits, _ and - only (no dots) and are never reused. Keep each task small. Do not plan reviews or tests: "
                  "separate reviewer and tester agents run after each task. The controller validates your plan and returns errors if it is invalid."]
        spec = self.role_spec("planner", pid, [self.head[0]] if self.head else [], [], "\n".join(lines),
                              extra_files={str(pol): sha256_path(pol)}, check_args=["--policy", str(pol)])
        st, sha = self.ensure(pid, spec)
        if st != "VERIFIED":
            raise Stop("plan_failed:" + pid)
        text = self.show(sha, out)
        tasks = json.loads(text)["tasks"]
        self.plan_ids += [t["id"] for t in tasks]
        self.save_handoff(pid + ".json", text)
        self.emit("plan_admitted", "plan:" + pid, plan=pid, version=n, tasks=[{k: t[k] for k in ("id", "title", "allowed", "profile")} for t in tasks],
                  replan=bool(failure))
        return tasks

    # ---- reviewer / tester ---------------------------------------------
    def changed(self, sha):
        return _out(self.root, "diff", "--name-only", sha + "^", sha).splitlines()

    def wants_review(self, changed):
        m = self.rv["per_task"]
        return m == "all" or (m == "flagged" and any(handoff.under(c, [handoff.norm(x) for x in self.rv["flagged_paths"]]) for c in changed))

    def run_review(self, subject, pt, sha, diff_cmd):
        rid = subject + ".review"
        prompt = ("ROLE: reviewer. You are independent: you did not write this code. Do NOT modify any code or any file except OUTPUT_FILE.\n"
                  "TASK_ID: %s\nOUTPUT_FILE: handoff/%s.json\nREVIEWING: %s\n%s\nTASK: %s\nINSTRUCTIONS: %s\nACCEPTANCE: %s\n"
                  "CHANGED FILES: %s\nSEE THE CHANGE WITH: %s\n"
                  "The controller already ran its verification (profile %s) on this commit and it PASSED. Do not re-run builds unless you need to confirm a finding.\n"
                  "Look for requirement gaps, wrong behaviour, and risky changes against the ACCEPTANCE text.\n"
                  'Write OUTPUT_FILE as JSON: {"verdict": "pass" | "changes_requested", "summary": "...", "findings": [{"id": "F1", '
                  '"severity": "blocking" | "minor", "criterion": "the requirement it violates", "file": "path", '
                  '"evidence": "file:line or command output that shows it", "summary": "..."}]}\n'
                  'Use "blocking" only when you can cite the violated criterion AND concrete evidence; otherwise use "minor". '
                  'verdict "pass" means no blocking findings.\n'
                  % (rid, rid, subject, self.objective_block(), pt["title"], pt["instructions"], pt["acceptance"],
                     ", ".join(self.changed(sha)) or "(none)", diff_cmd, pt["profile"]))
        st, rsha = self.ensure(rid, self.role_spec("reviewer", rid, [subject], [], prompt))
        if st != "VERIFIED":
            self.warnings.append("review_unavailable:" + subject)
            self.emit("review_unavailable", "rev:" + rid, subject=subject)
            return None
        text = self.show(rsha, "handoff/%s.json" % rid)
        doc = json.loads(text)
        self.save_handoff(rid + ".json", text)
        self.emit("review_done", "rev:" + rid, subject=subject, verdict=doc["verdict"], reviewer_task=rid,
                  findings=[{k: f.get(k) for k in ("id", "severity", "criterion", "evidence")} for f in doc["findings"]])
        return doc

    def run_tester(self, subject, pt, sha):
        sid = subject + ".test"
        out = "handoff/%s.json" % sid
        prompt = ("ROLE: tester. You are independent: you did not write this code. Do NOT modify any code or any file except OUTPUT_FILE and TESTS_DIR.\n"
                  "TASK_ID: %s\nOUTPUT_FILE: %s\nTESTS_DIR: handoff/%s.tests/\nTESTING: %s\n%s\nTASK: %s\nACCEPTANCE: %s\nCHANGED FILES: %s\n"
                  "Write up to 5 small, runnable test files into TESTS_DIR that check the ACCEPTANCE text and look for regressions or edge cases. "
                  "Your tests are proposals: the controller runs them as evidence but they do not decide acceptance.\n"
                  'Write OUTPUT_FILE as JSON: {"summary": "...", "tests": [{"file": "handoff/%s.tests/test_x.py", "purpose": "...", "criterion": "..."}]}\n'
                  % (sid, out, sid, subject, self.objective_block(), pt["title"], pt["acceptance"], ", ".join(self.changed(sha)) or "(none)", sid))
        st, tsha = self.ensure(sid, self.role_spec("tester", sid, [subject], ["handoff/%s.tests" % sid], prompt))
        if st != "VERIFIED":
            self.warnings.append("tester_unavailable:" + subject)
            self.emit("tester_unavailable", "tst:" + sid, subject=subject)
            return
        text = self.show(tsha, out)
        doc = json.loads(text)
        self.save_handoff(sid + ".json", text)
        results = self.run_proposals(tsha, doc)
        self.emit("tester_evidence", "tst:" + sid, subject=subject, tester_task=sid, proposals=results)
        for r in results:
            if r["result"] not in ("pass", "not_run"):  # not_run = the policy deliberately gives no command (DOOM: proposals are recorded, never executed on the host)
                self.warnings.append("proposed_test_%s:%s" % (r["result"], r["file"]))

    def run_proposals(self, tsha, doc):
        """Run the tester's proposed tests against the subject's code (evidence only). Not run if the policy gives no command."""
        argv_t = self.p.get("proposed_test_argv")
        if not argv_t:
            return [dict(file=t["file"], result="not_run") for t in doc["tests"]]
        env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            tar = subprocess.run(["git", "-C", str(self.root), "archive", "--format=tar", tsha], capture_output=True, check=True).stdout
            tarfile.open(fileobj=io.BytesIO(tar)).extractall(tmp)
            for t in doc["tests"]:
                argv = [a.replace("{file}", t["file"]) for a in argv_t]
                try:
                    p = subprocess.run(argv, cwd=tmp, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                       timeout=self.b["proposal_timeout_s"])
                    res, tail = ("pass" if p.returncode == 0 else "fail"), (p.stdout + p.stderr)[-600:]
                except (OSError, subprocess.TimeoutExpired) as e:
                    res, tail = "error", str(e)
                results.append(dict(file=t["file"], purpose=t.get("purpose"), result=res, output_tail=tail))
        return results

    def review_cycle(self, pt, tid, sha):
        """Independent review + test of an accepted task, at most review.max_fixes review-driven fixes. -> (head_id, head_sha)"""
        cur, cur_sha = tid, sha
        for k in range(self.rv["max_fixes"] + 1):
            doc = self.run_review(cur, pt, cur_sha, "git diff HEAD~1 HEAD")
            if k == 0:
                self.run_tester(cur, pt, cur_sha)
            blocking = [f for f in (doc or {}).get("findings", []) if f["severity"] == "blocking"]
            if not blocking:
                break
            if k == self.rv["max_fixes"]:
                self.open_findings += [dict(task=cur, **f) for f in blocking]
                self.emit("review_unresolved", "unres:" + cur, task=cur, findings=[f["id"] for f in blocking])
                break
            fid = "%s.fix%d" % (tid, k + 1)
            extra = ("\nINDEPENDENT REVIEW requested changes. Blocking findings:\n" +
                     "\n".join("- %s: %s | criterion: %s | evidence: %s" % (f["id"], f["summary"], f["criterion"], f["evidence"]) for f in blocking) +
                     "\nFix only these findings. Everything the controller verifies must keep passing.")
            st, fsha = self.ensure(fid, dict(self.coder_spec(pt, cur, extra, fid), title="fix " + tid))
            if st != "VERIFIED":
                self.open_findings += [dict(task=cur, **f) for f in blocking]
                self.warnings.append("review_fix_failed:" + tid)
                self.emit("review_fix_failed", "fixfail:" + fid, task=tid)
                break
            self.emit("review_fix_accepted", "fix:" + fid, task=tid, fix=fid, sha=fsha)
            cur, cur_sha = fid, fsha
        return cur, cur_sha

    # ---- one planned task ----------------------------------------------
    def work(self, pt):
        """-> ('VERIFIED', (head_id, head_sha)) or ('BLOCKED', None)."""
        tid = pt["id"]
        st, sha = self.ensure(tid, self.coder_spec(pt, self.head[0] if self.head else None))
        if st != "VERIFIED":
            return st, None
        self.emit("task_accepted", "acc:" + tid, task=tid, sha=sha, profile=pt["profile"])
        if self.wants_review(self.changed(sha)):
            return "VERIFIED", self.review_cycle(pt, tid, sha)
        return "VERIFIED", (tid, sha)

    def final_gate(self, k):
        prof = self.p["profiles"][self.p["final_gate"]]
        for path, want in prof["test_files"].items():
            if sha256_path(path) != want:
                raise Stop("trusted_tests_modified")
        sha = self.head[1] if self.head else self.base_sha
        wt = self.root / "build" / "worktrees" / ("%s-final-%d-%s" % (self.run_id, k, uuid.uuid4().hex[:6]))
        create_worktree(self.root, wt, sha)
        ev = self.runs / "verify" / ("FINAL-%d" % k)
        rec = verify(wt, sha, [], list(self.p.get("protected", [])), prof["test_argv"], ev, int(min(self.b["verify_timeout_s"], max(self.remaining(), 1))))
        self.emit("final_gate", "final:%d" % k, gate=self.p["final_gate"], decision=rec["decision"], classification=rec["classification"],
                  head_sha=sha, evidence=str(ev))
        return rec

    # ---- the run -------------------------------------------------------
    def run(self):
        os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")  # agents and trusted tests inherit it: no .pyc written into worktrees
        self.store.recover()
        self.emit("coordinator_started", "start:" + self.run_id, objective=self.p["objective"], base_sha=self.base_sha, budgets=self.b,
                  mode=self.p.get("mode", "real"))
        try:
            reason = self._run()
        except Stop as s:
            reason = str(s.args[0])
        return self.finish(reason)

    def _run(self):
        n, failure = 1, None
        while True:
            tasks = self.plan(n, failure)
            blocked = None
            for i, pt in enumerate(tasks):
                st, res = self.work(pt)
                if st == "VERIFIED":
                    self.head = res
                else:
                    blocked = (pt["id"], [t["id"] for t in tasks[i + 1:]])
                    break
            if blocked:
                kind, ref, dropped = "task_blocked", blocked[0], blocked[1]
            else:
                rec = self.final_gate(n)
                if rec["decision"] == PASS:
                    break
                if rec["decision"] == ERROR:
                    raise Stop("final_gate_error:" + rec["classification"])
                kind, ref, dropped = "final_gate_failed", rec["log"], []
            if n > self.b["max_replans"]:
                raise Stop("replan_budget_exhausted:%s:%s" % (kind, ref if kind == "task_blocked" else "final_gate"))
            self.emit("replan_triggered", "replan:%d" % n, trigger=kind, ref=ref if kind == "task_blocked" else "final_gate",
                      superseded=([ref] if kind == "task_blocked" else []) + dropped, replan_number=n, of=self.b["max_replans"])
            for d in dropped:
                self.emit("task_dropped", "drop:" + d, task=d, reason="superseded_by_replan")
            failure, n = (kind, ref, dropped), n + 1
        if self.rv["final"] and self.head:
            self.final_review()
        return "complete"

    def final_review(self):
        """One cumulative review of base..head by the reviewer role. Findings are recorded, never auto-fixed."""
        rid = "FINAL.review"
        st, rsha = self.ensure(rid, self.role_spec("reviewer", rid, [self.head[0]], [], self._final_prompt()))
        if st != "VERIFIED":
            self.warnings.append("review_unavailable:FINAL")
            return
        text = self.show(rsha, "handoff/%s.json" % rid)
        doc = json.loads(text)
        self.save_handoff(rid + ".json", text)
        blocking = [f for f in doc["findings"] if f["severity"] == "blocking"]
        self.emit("review_done", "rev:" + rid, subject="FINAL", verdict=doc["verdict"], reviewer_task=rid,
                  findings=[{k: f.get(k) for k in ("id", "severity", "criterion", "evidence")} for f in doc["findings"]])
        if blocking:
            self.open_findings += [dict(task="FINAL", **f) for f in blocking]
            self.emit("review_unresolved", "unres:FINAL", task="FINAL", findings=[f["id"] for f in blocking])

    def _final_prompt(self):
        return ("ROLE: reviewer. You are independent. Do NOT modify any code or any file except OUTPUT_FILE.\n"
                "TASK_ID: FINAL.review\nOUTPUT_FILE: handoff/FINAL.review.json\nREVIEWING: the whole run (cumulative change)\n%s\n"
                "SEE THE CHANGE WITH: git diff %s HEAD\nThe controller's final acceptance gate (%s) already PASSED on this commit.\n"
                'Write OUTPUT_FILE as JSON: {"verdict": "pass" | "changes_requested", "summary": "...", "findings": [{"id": "F1", '
                '"severity": "blocking" | "minor", "criterion": "...", "file": "path", "evidence": "file:line or command output", "summary": "..."}]}\n'
                'Use "blocking" only with a violated criterion AND concrete evidence.\n' % (self.objective_block(), self.base_sha, self.p["final_gate"]))

    # ---- evidence ------------------------------------------------------
    def finish(self, reason):
        complete = reason == "complete"
        status = ("complete" if not (self.open_findings or self.warnings) else "complete_with_open_findings") if complete else reason
        tasks = self.store.state["tasks"]
        events = [e for e in self.store.journal.read() if e.get("key") or e["type"] in ("verification", "decision")]
        roles = {}
        for tid, t in tasks.items():
            r = roles.setdefault(t["role"], dict(tasks=0, attempts=0, agent=(self.cfg["tasks"].get(tid) or {}).get("agent")))
            r["tasks"] += 1
            r["attempts"] += t["attempt_seq"]
        refs = _out(self.root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/loop/accepted/" + self.run_id).splitlines()
        report = dict(run_id=self.run_id, status=status, reason=reason, objective=self.p["objective"], mode=self.p.get("mode", "real"),
                      invocations=self.invocations(), roles=roles, open_findings=self.open_findings, warnings=self.warnings,
                      tasks={i: dict(role=t["role"], status=t["status"], attempts=t["attempt_seq"], verified_sha=t["verified_sha"]) for i, t in tasks.items()},
                      accepted_refs=refs, final_head=self.head[1] if self.head else None, events=events)
        self.runs.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.runs / "report.json", report)
        lines = ["# Run %s: %s" % (self.run_id, status), "", "Objective: " + self.p["objective"], "Reason: " + reason, "Mode: " + report["mode"], "",
                 "| task | role | status | attempts | accepted sha |", "|---|---|---|---|---|"]
        lines += ["| %s | %s | %s | %d | %s |" % (i, v["role"], v["status"], v["attempts"], (v["verified_sha"] or "")[:12]) for i, v in report["tasks"].items()]
        lines += ["", "Open findings: %d. Warnings: %s" % (len(self.open_findings), ", ".join(self.warnings) or "none"),
                  "Completion requires the controller's final gate profile `%s` to PASS on the final head; no agent assertion counts." % self.p.get("final_gate")]
        (self.runs / "summary.md").write_text("\n".join(lines) + "\n")
        self.store._commit("coordinator_stopped", task_id=None, status=status, reason=reason, report=str(self.runs / "report.json"))
        return dict(status=status, reason=reason, report=str(self.runs / "report.json"), tasks={i: v["status"] for i, v in report["tasks"].items()})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the autonomous goal loop from a controller-owned policy.")
    ap.add_argument("policy", help="policy JSON (see loop/coordinator.py and tests/step5_fixture.py)")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args(argv)
    policy = json.loads(Path(args.policy).read_text())
    store = Store(args.root)
    try:
        result = Coordinator(store, policy).run()
    finally:
        store.close()
    print(json.dumps(result, indent=2))
    return 0 if result["status"].startswith("complete") else 1


if __name__ == "__main__":
    sys.exit(main())
