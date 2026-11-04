"""Step 4: the deterministic controller loop (sequential, one task at a time).

Each pass picks work in this order: EXECUTED (verify it), FAILED (decide retry
or block), then the next PENDING task. Everything is derived from the Store, so
a restart just calls store.recover() and continues.

  PENDING  -> run_attempt (fresh worktree, prompt = task + last feedback)
  EXECUTED -> verify() -> PASS: re-fingerprint, preserve, commit the verified
              tree to refs/loop/accepted/<run>/<task>, VERIFIED(sha)
                          FAIL: write feedback, FAILED -> retry or BLOCKED
                          ERROR: re-verify (verify_retries), then stop the loop
                                 and leave the task EXECUTED (resumable)
  FAILED   -> PENDING if attempt_seq < max_attempts else BLOCKED

The agent never decides anything: test commands, allowed/protected paths,
prompts and the approved sha256 of the trusted test files (`test_files`, checked
before every attempt and every verification) come from the controller-owned
config, not the worktree. A mismatch stops the loop (trusted_tests_modified).
If a failed attempt cannot be preserved, the task stays EXECUTED and the loop stops. An agent that
cannot be launched (infrastructure) stops the loop instead of burning retries.

Limits (accepted for now):
- max_runtime_s applies per controller invocation, not across restarts.
- Every started attempt (including interrupted ones) counts toward max_attempts.
- The store's FAILED counters also count infrastructure launch errors as attempts.
- Tasks with depends_on start from the last dependency's accepted commit; there
  is no merge of independent tasks yet.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loop.preserve import IDENT, _git, _out  # noqa: E402
from loop.runner import docker_agent_argv, run_attempt  # noqa: E402
from loop.state import Store  # noqa: E402
from loop.verify import ERROR, FAIL, PASS, snapshot_tree, verify  # noqa: E402

BUDGETS = dict(max_attempts=3, max_runtime_s=3600, agent_timeout_s=900, verify_timeout_s=300, verify_retries=1)
TAIL = 3000  # characters of log put in feedback


def sha256_path(path):
    """sha256 of a file, or of a directory (sorted relative names + contents)."""
    path, h = Path(path), hashlib.sha256()
    files = sorted(q for q in path.rglob("*") if q.is_file()) if path.is_dir() else [path]
    for q in files:
        h.update((q.relative_to(path).as_posix() if path.is_dir() else "").encode() + b"\0" + q.read_bytes() + b"\0")
    return h.hexdigest()


def _tail(path):
    try:
        return Path(path).read_bytes()[-TAIL:].decode(errors="replace")
    except OSError:
        return "(log not available)"


class Controller:
    def __init__(self, store, cfg):
        self.store, self.cfg, self.root = store, cfg, store.root
        self.b = dict(BUDGETS, **cfg.get("budgets", {}))
        self.run_id = store.state["run_id"]
        self.base_sha = cfg.get("base_sha") or _out(self.root, "rev-parse", "HEAD")
        self.deadline = time.monotonic() + self.b["max_runtime_s"]
        self.runs = self.root / "artifacts" / "runs" / self.run_id

    # ---- small helpers -------------------------------------------------
    def task(self, tid):
        return self.store.state["tasks"][tid]

    def remaining(self):
        return self.deadline - time.monotonic()

    def note(self, etype, tid, **fields):
        """Journal a controller event (replay ignores events without a transition)."""
        self.store._commit(etype, task_id=tid, **fields)

    def trusted_problem(self, tid):
        """Frozen trusted tests: every `test_files` path must still hash to its approved value. -> problem or None."""
        for path, want in self.cfg["tasks"][tid]["test_files"].items():
            try:
                got = sha256_path(path)
            except OSError as e:
                return "%s: %s" % (path, e)
            if got != want:
                return "%s: hash %s != approved %s" % (path, got[:12], want[:12])
        return None

    def base_for(self, t):
        deps = t["depends_on"]
        return self.task(deps[-1])["verified_sha"] if deps else self.base_sha

    def latest_feedback(self, tid):
        files = list((self.runs / "feedback").glob("%s-a*.md" % tid))
        return max(files, key=lambda p: int(p.stem.rsplit("-a", 1)[1])) if files else None

    def prompt_for(self, t):
        spec = self.cfg["tasks"][t["id"]]
        fb = self.latest_feedback(t["id"])
        if not fb:
            return spec["prompt"]
        return ("%s\n\n--- Feedback from the previous attempt (it failed independent verification) ---\n%s\n"
                "Start from the original files; fix what the feedback names. Allowed paths: %s. Do not touch: %s.\n"
                "Full artifacts of earlier attempts are under %s"
                % (spec["prompt"], fb.read_text(), ", ".join(spec["allowed"]), ", ".join(spec["protected"]) or "(none)", self.runs))

    def after_failure(self, tid, reason, text, infra=False):
        """Task is FAILED. Save feedback, then retry, block, or (infra) stop."""
        n = self.task(tid)["attempt_seq"]
        fb = self.runs / "feedback" / ("%s-a%d.md" % (tid, n))
        fb.parent.mkdir(parents=True, exist_ok=True)
        fb.write_text("Attempt %d failed: %s\n%s\n" % (n, reason, text))
        if infra:
            self.note("decision", tid, attempt=n, action="stop", reason=reason, feedback=str(fb))
            return "infra_error"
        return self.retry_or_block(tid, reason, fb)

    def retry_or_block(self, tid, reason, fb=None):
        n = self.task(tid)["attempt_seq"]
        if n < self.b["max_attempts"]:
            self.store.transition(tid, "PENDING", reason="retry:" + reason)
            self.note("decision", tid, attempt=n, action="retry", reason=reason, feedback=str(fb) if fb else None)
        else:
            self.store.transition(tid, "BLOCKED", reason="attempt_budget_exhausted:" + reason)
            self.note("decision", tid, attempt=n, action="block", reason="attempt_budget_exhausted:" + reason)

    # ---- one attempt ---------------------------------------------------
    def attempt(self, tid):
        a = self.cfg["agent"]
        name = self.cfg["tasks"][tid].get("agent") or a.get("name")  # Step 5: one Docker Agent agent per role
        build = docker_agent_argv(a["config"], self.prompt_for(self.task(tid)), name, a.get("binary", "docker-agent"))
        r = run_attempt(self.store, tid, self.base_for(self.task(tid)), build, self.b["agent_timeout_s"], repo=self.root)
        if r["outcome"] == "completed":
            return self.finish(tid)
        text = "The agent run ended with %s (exit code %s). Log tail:\n%s\nPreserved work: %s" % (
            r["classification"], r["exit_code"], _tail(r["log"]), r.get("preserved_dir"))
        return self.after_failure(tid, "agent_" + r["classification"], text, infra=r["classification"] == "launch_error")

    def accept(self, tid, rec, wt, base):
        """-> (commit, None) or (None, why). The worktree must still match the verified fingerprint."""
        if snapshot_tree(wt, base) != rec["fingerprint"]:
            return None, "stale_at_accept"
        pm = self.store.preserve_attempt(tid)
        if pm["status"] != "preserved":
            return None, "preserve_failed"
        n = self.task(tid)["attempt_seq"]
        commit = _out(self.root, "commit-tree", rec["fingerprint"], "-p", base, "-m", "loop accepted %s a%d" % (tid, n), env=IDENT)
        _git(self.root, "update-ref", "refs/loop/accepted/%s/%s" % (self.run_id, tid), commit)
        return commit, None

    def finish(self, tid):
        """Verify an EXECUTED task. -> None, or 'infra_error' when no valid result could be obtained."""
        t = self.task(tid)
        spec, n, cur = self.cfg["tasks"][tid], t["attempt_seq"], t["current"]
        wt, base = Path(cur["worktree"]), cur["base_sha"]
        if not wt.is_dir():
            self.store.transition(tid, "FAILED", reason="worktree_missing")
            return self.after_failure(tid, "worktree_missing", "The attempt's worktree no longer exists.")
        for i in range(self.b["verify_retries"] + 1):
            ev_dir = self.runs / "verify" / ("%s-a%d-v%d" % (tid, n, i))
            rec = verify(wt, base, spec["allowed"], spec["protected"], spec["test_argv"], ev_dir,
                         min(self.b["verify_timeout_s"], max(self.remaining(), 1)))
            commit = None
            if rec["decision"] == PASS:
                commit, why = self.accept(tid, rec, wt, base)
                if commit is None:
                    rec = dict(rec, decision=ERROR, classification=why)
            self.note("verification", tid, attempt=n, verify_try=i, decision=rec["decision"],
                      classification=rec["classification"], exit_code=rec["exit_code"], fingerprint=rec["fingerprint"],
                      fingerprint_after=rec["fingerprint_after"], evidence=str(ev_dir), accepted_sha=commit,
                      violations=[v["path"] for v in rec["violations"]])
            if rec["decision"] != ERROR:
                break
            last = i == self.b["verify_retries"]
            self.note("decision", tid, attempt=n, action="stop" if last else "reverify", reason=rec["classification"])
        if rec["decision"] == ERROR:
            return "infra_error"  # task stays EXECUTED; the next run re-verifies without re-running the agent
        if rec["decision"] == PASS:
            self.store.transition(tid, "VERIFIED", reason="verified", sha=commit)
            self.note("decision", tid, attempt=n, action="accept", sha=commit)
            return None
        pm = self.store.preserve_attempt(tid)
        if pm["status"] != "preserved":  # never discard failed work we could not save: stay EXECUTED, stop, retry next run
            self.note("decision", tid, attempt=n, action="stop", reason="preserve_failed", error=pm.get("error") or pm["status"])
            return "infra_error"
        lines = ["Independent verification: %s (%s)" % (rec["decision"], rec["classification"])]
        if rec["violations"]:
            lines.append("Out-of-scope changes (status path reason):")
            lines += ["  %s %s %s" % (v["status"], v["path"], v["reason"]) for v in rec["violations"]]
        else:
            lines.append("The trusted test command exited %s. Output tail:\n%s" % (rec["exit_code"], _tail(rec["log"])))
        lines.append("Evidence: %s\nPreserved work: %s" % (ev_dir, pm["dir"]))
        self.store.transition(tid, "FAILED", reason="verify_fail:" + rec["classification"])
        return self.after_failure(tid, "verify_fail:" + rec["classification"], "\n".join(lines))

    # ---- the loop ------------------------------------------------------
    def pick(self):
        tasks = self.store.state["tasks"].values()
        for status in ("EXECUTED", "FAILED"):
            for t in tasks:
                if t["status"] == status:
                    return t
        nxt = self.store.next_task()
        return self.task(nxt) if nxt else None

    def run(self):
        for tid, spec in self.cfg["tasks"].items():
            if not spec.get("test_files"):
                raise ValueError("task %s needs `test_files`: {path: sha256} of the frozen trusted tests" % tid)
        report = self.store.recover()
        for tid, spec in self.cfg["tasks"].items():
            if tid not in self.store.state["tasks"]:
                self.store.add_task(tid, spec.get("title") or spec["prompt"].splitlines()[0][:80],
                                    spec.get("role", "coder"), spec.get("depends_on", ()))
        self.note("controller_started", None, base_sha=self.base_sha, budgets=self.b, recovery=report)
        stop = None
        while stop is None:
            t = self.pick()
            if t is None:
                break
            tid, status = t["id"], t["status"]
            if status == "FAILED":
                self.retry_or_block(tid, "resumed")
            elif status == "PENDING" and t["attempt_seq"] >= self.b["max_attempts"]:
                self.store.transition(tid, "BLOCKED", reason="attempt_budget_exhausted")
                self.note("decision", tid, attempt=t["attempt_seq"], action="block", reason="attempt_budget_exhausted")
            elif self.remaining() <= (self.b["agent_timeout_s"] if status == "PENDING" else 0):
                stop = "budget_exhausted"
            elif (bad := self.trusted_problem(tid)):
                self.note("decision", tid, attempt=t["attempt_seq"], action="stop", reason="trusted_tests_modified", detail=bad)
                stop = "trusted_tests_modified"
            else:
                stop = self.finish(tid) if status == "EXECUTED" else self.attempt(tid)
        states = {k: v["status"] for k, v in self.store.state["tasks"].items()}
        if stop:
            status = stop
        elif all(s == "VERIFIED" for s in states.values()):
            status = "complete"
        else:
            status = "quarantined" if "QUARANTINED" in states.values() else "blocked"
        self.note("controller_stopped", None, status=status, tasks=states)
        return dict(status=status, tasks=states)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the autonomous loop from a controller-owned config.")
    ap.add_argument("config", help="JSON file: base_sha?, agent{config,name?,binary?}, budgets?, tasks{id:{prompt,allowed,protected,test_argv,test_files{path:sha256}}}")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text())
    store = Store(args.root)
    try:
        result = Controller(store, cfg).run()
    finally:
        store.close()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
