import copy
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from loop.state import Journal, JournalCorrupt, Store  # noqa: E402

IDENT = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}

CHILD = """
import os, signal, subprocess, sys
sys.path.insert(0, sys.argv[1])
from loop.state import Store
root, wt, base = sys.argv[2:5]
def git(*a): subprocess.run(["git", "-C", wt] + list(a), check=True, capture_output=True)
s = Store(root)
s.begin_attempt("T1", worktree=wt, base_sha=base)
open(os.path.join(wt, "committed.txt"), "w").write("committed\\n")
git("add", "committed.txt"); git("commit", "-m", "agent commit")
open(os.path.join(wt, "staged.txt"), "w").write("staged\\n"); git("add", "staged.txt")
open(os.path.join(wt, "a.txt"), "a").write("edited\\n")
open(os.path.join(wt, "new.txt"), "w").write("untracked\\n")
os.kill(os.getpid(), signal.SIGKILL)
"""

ORPHAN_CHILD = """
import os, signal, sys
sys.path.insert(0, sys.argv[1])
from loop.state import Store
s = Store(sys.argv[2])
s.journal.append("intent", task_id="T1", attempt=1, worktree=None, base_sha=None, **{"from": "PENDING", "to": "EXECUTING"})
os.kill(os.getpid(), signal.SIGKILL)
"""

# Runs `setup`, then kills itself inside the first state save of `target`:
# the target's event is in the journal but state.json does not have it.
BOUNDARY_CHILD = """
import os, signal, sys
sys.path.insert(0, sys.argv[1])
from loop.state import Store
root, setup, target = sys.argv[2:5]
s = Store(root)
exec(setup)
Store._save = lambda self, ev: os.kill(os.getpid(), signal.SIGKILL)
exec(target)
"""

STARTUP_CHILD = """
import os, signal, sys
sys.path.insert(0, sys.argv[1])
from loop.state import Store
Store._save = lambda self, ev: os.kill(os.getpid(), signal.SIGKILL)
Store(sys.argv[2])
"""


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, env=dict(os.environ, **IDENT))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.main = self.tmp / "main"
        self.main.mkdir()
        git(self.main, "init", "-q")
        (self.main / "a.txt").write_text("base\n")
        git(self.main, "add", "a.txt")
        git(self.main, "commit", "-qm", "base")
        self.base = git(self.main, "rev-parse", "HEAD")

    def store(self, root=None):
        s = Store(root or self.main)
        self.addCleanup(s.close)
        return s

    def worktree(self, name="wt1"):
        wt = self.tmp / name
        git(self.main, "worktree", "add", "-q", "-b", name, str(wt))
        return wt

    def run_child(self, code, *args):
        return subprocess.run([sys.executable, "-c", code, str(REPO)] + [str(a) for a in args],
                              env=dict(os.environ, **IDENT, PYTHONDONTWRITEBYTECODE="1"),
                              capture_output=True, text=True)

    def events(self, root=None):
        path = Path(root or self.main) / "artifacts" / "events.jsonl"
        out = []
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out


class KillAndResume(Base):
    def test_sigkill_mid_execution_preserves_changes_and_resumes(self):
        s = self.store()
        s.add_task("T1", "first", "build_coder")
        s.add_task("T2", "second", "engine_coder", depends_on=["T1"])
        s.close()
        wt = self.worktree()

        p = self.run_child(CHILD, self.main, wt, self.base)
        self.assertEqual(p.returncode, -signal.SIGKILL, p.stderr)

        s = self.store()
        self.assertEqual(s.state["tasks"]["T1"]["status"], "EXECUTING")
        report = s.recover()
        self.assertEqual(report["interrupted"], ["T1"])
        self.assertEqual(report["quarantined"], [])
        t1 = s.state["tasks"]["T1"]
        self.assertEqual(t1["status"], "PENDING")
        self.assertEqual(t1["attempts"], {"coding": 0, "interrupt": 1, "timeout": 0})

        rec = [e for e in self.events() if e["type"] == "recovered"][0]
        self.assertEqual(rec["preserved_status"], "preserved")
        pdir = self.main / rec["preserved_dir"]
        m = json.loads((pdir / "manifest.json").read_text())
        snap = m["snapshot_commit"]
        self.assertEqual(len(m["commits_since_base"]), 1)
        self.assertEqual(git(self.main, "show", snap + ":new.txt"), "untracked")
        self.assertEqual(git(self.main, "show", snap + ":staged.txt"), "staged")
        self.assertEqual(git(self.main, "show", snap + ":committed.txt"), "committed")
        self.assertTrue(git(self.main, "show", snap + ":a.txt").endswith("edited"))
        self.assertTrue((wt / "new.txt").exists())  # worktree left untouched

        fresh = self.tmp / "fresh"
        subprocess.run(["git", "clone", "-q", str(self.main), str(fresh)], check=True, capture_output=True)
        git(fresh, "fetch", "-q", str(pdir / "snapshot.bundle"), m["ref"] + ":refs/restored")
        self.assertEqual(git(fresh, "rev-parse", "refs/restored"), snap)
        self.assertEqual(git(fresh, "show", "refs/restored:new.txt"), "untracked")

        self.assertEqual(s.next_task(), "T1")
        self.assertEqual(s.begin_attempt("T1", worktree=self.worktree("wt2"), base_sha=self.base), 2)
        s.record_result("T1", "completed")
        s.transition("T1", "VERIFIED", sha=self.base)
        self.assertEqual(s.next_task(), "T2")
        self.assertEqual(s.begin_attempt("T2"), 1)
        s.record_result("T2", "completed")
        s.transition("T2", "VERIFIED", sha=self.base)
        self.assertIsNone(s.next_task())

        evs = self.events()
        self.assertEqual([e["seq"] for e in evs], list(range(1, len(evs) + 1)))
        self.assertEqual(s.state["last_seq"], len(evs))
        closed = {(e["task_id"], e["attempt"]) for e in evs if e["type"] in ("result", "recovered")}
        intents = {(e["task_id"], e["attempt"]) for e in evs if e["type"] == "intent"}
        self.assertEqual(intents, closed)

    def test_no_worktree_recorded_is_nothing_to_preserve(self):
        s = self.store()
        s.add_task("T1", "t", "r")
        s.begin_attempt("T1")
        s.close()
        s = self.store()
        self.assertEqual(s.recover()["interrupted"], ["T1"])
        self.assertEqual(s.state["tasks"]["T1"]["status"], "PENDING")
        rec = [e for e in self.events() if e["type"] == "recovered"][0]
        self.assertEqual(rec["preserved_status"], "no_worktree")

    def test_timeout_counts_as_coding_attempt_interrupt_does_not(self):
        s = self.store()
        s.add_task("T1", "t", "r")
        s.begin_attempt("T1")
        s.record_result("T1", "timeout")
        self.assertEqual(s.state["tasks"]["T1"]["attempts"], {"coding": 1, "interrupt": 0, "timeout": 1})
        s.transition("T1", "PENDING")
        s.begin_attempt("T1")
        s.close()
        s = self.store()
        s.recover()
        self.assertEqual(s.state["tasks"]["T1"]["attempts"], {"coding": 1, "interrupt": 1, "timeout": 1})


class CrashBoundaries(Base):
    """Issue 1: a crash after the journal append but before the state save."""

    def crash(self, name, setup, target):
        root = self.tmp / ("root_" + name)
        root.mkdir()
        p = self.run_child(BOUNDARY_CHILD, root, setup, target)
        self.assertEqual(p.returncode, -signal.SIGKILL, p.stderr)
        s = self.store(root)
        self.assertEqual(len(s.replayed), 1)
        self.assertEqual(s.state["last_seq"], s.journal.seq)
        snapshot = json.loads(json.dumps(s.state))
        s.close()
        again = Store(root)
        self.addCleanup(again.close)
        self.assertEqual(again.replayed, [])  # replay is idempotent
        self.assertEqual(again.state, snapshot)
        return again

    def test_first_run_started(self):
        root = self.tmp / "root_start"
        root.mkdir()
        p = self.run_child(STARTUP_CHILD, root)
        self.assertEqual(p.returncode, -signal.SIGKILL, p.stderr)
        s = self.store(root)
        evs = self.events(root)
        self.assertEqual([e["type"] for e in evs], ["run_started"])
        self.assertEqual(s.state["run_id"], evs[0]["run_id"])
        self.assertEqual(s.replayed, [1])

    def test_task_creation(self):
        s = self.crash("add", "", "s.add_task('T1', 't', 'r')")
        self.assertEqual(s.state["tasks"]["T1"]["status"], "PENDING")

    def test_attempt_initiation(self):
        wt = self.worktree()
        s = self.crash("begin", "s.add_task('T1', 't', 'r')",
                       "s.begin_attempt('T1', worktree=%r, base_sha=%r)" % (str(wt), self.base))
        t = s.state["tasks"]["T1"]
        self.assertEqual((t["status"], t["attempt_seq"]), ("EXECUTING", 1))
        self.assertEqual((t["current"]["worktree"], t["current"]["base_sha"]), (str(wt), self.base))
        self.assertEqual(s.recover()["interrupted"], ["T1"])

    def test_result_recording(self):
        s = self.crash("result", "s.add_task('T1', 't', 'r'); s.begin_attempt('T1')",
                       "s.record_result('T1', 'timeout')")
        t = s.state["tasks"]["T1"]
        self.assertEqual(t["status"], "FAILED")
        self.assertEqual(t["attempts"], {"coding": 1, "interrupt": 0, "timeout": 1})

    def test_verification_transition(self):
        s = self.crash("verify",
                       "s.add_task('T1', 't', 'r'); s.begin_attempt('T1'); s.record_result('T1', 'completed')",
                       "s.transition('T1', 'VERIFIED', sha='abc123')")
        t = s.state["tasks"]["T1"]
        self.assertEqual((t["status"], t["verified_sha"]), ("VERIFIED", "abc123"))

    def test_recovery_success(self):
        wt = self.worktree()
        s = self.crash("recovered", "s.add_task('T1', 't', 'r'); s.begin_attempt('T1', worktree=%r, base_sha=%r)"
                       % (str(wt), self.base), "s.recover()")
        t = s.state["tasks"]["T1"]
        self.assertEqual((t["status"], t["attempts"]["interrupt"]), ("PENDING", 1))

    def test_recovery_failure(self):
        gone = self.tmp / "gone"
        s = self.crash("recfail", "s.add_task('T1', 't', 'r'); s.begin_attempt('T1', worktree=%r)" % str(gone),
                       "s.recover()")
        self.assertEqual(s.state["tasks"]["T1"]["status"], "QUARANTINED")
        self.assertTrue(s.is_quarantined(gone))

    def test_orphan_intent_is_replayed_then_recovered(self):
        s = self.store()
        s.add_task("T1", "t", "r")
        s.close()
        p = self.run_child(ORPHAN_CHILD, self.main)
        self.assertEqual(p.returncode, -signal.SIGKILL, p.stderr)
        s = self.store()
        self.assertEqual(s.state["tasks"]["T1"]["status"], "EXECUTING")
        report = s.recover()
        self.assertEqual(len(report["replayed"]), 1)
        self.assertEqual(report["interrupted"], ["T1"])
        self.assertEqual(s.begin_attempt("T1"), 2)

    def test_state_write_failure_is_replayed_on_reopen(self):
        s = self.store()
        s.add_task("T1", "t", "r")
        before = s.state_path.read_bytes()
        with mock.patch("loop.state.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                s.add_task("T2", "t", "r")
        self.assertEqual(s.state_path.read_bytes(), before)
        s.close()
        s = self.store()
        self.assertEqual(s.state["tasks"]["T2"]["status"], "PENDING")
        self.assertEqual(len(s.recover()["replayed"]), 1)

    def test_replay_stops_at_unappliable_event_without_advancing(self):
        bad_events = {
            "wrong_from": dict(task_id="T1", attempt=1, outcome="completed", **{"from": "PENDING", "to": "EXECUTED"}),
            "unknown_task": dict(task_id="ghost", attempt=1, outcome="completed", **{"from": "EXECUTING", "to": "EXECUTED"}),
        }
        for name, bad in bad_events.items():
            with self.subTest(name):
                root = self.tmp / ("root_" + name)
                root.mkdir()
                s = Store(root)
                s.add_task("T1", "t", "r")
                s.close()
                j = Journal(root / "artifacts" / "events.jsonl")
                good = j.append("intent", task_id="T1", attempt=1, worktree=None, base_sha=None,
                                **{"from": "PENDING", "to": "EXECUTING"})
                j.append("result", **bad)
                for _ in range(2):  # fails the same way every time, and releases the lock
                    with self.assertRaisesRegex(RuntimeError, "replay stopped at seq %d" % (good["seq"] + 1)):
                        Store(root)
                saved = json.loads((root / "artifacts" / "state" / "state.json").read_text())
                self.assertEqual(saved["last_seq"], good["seq"])
                self.assertEqual(saved["tasks"]["T1"]["status"], "EXECUTING")


class RecoveryFailure(Base):
    """Issue 2: preservation failure must not look like a successful recovery."""

    def interrupted(self, worktree):
        s = self.store()
        s.add_task("T1", "t", "r")
        s.begin_attempt("T1", worktree=worktree, base_sha=self.base)
        s.close()
        s = self.store()
        return s, s.recover()

    def assert_quarantined(self, s, report, worktree):
        self.assertEqual(report["quarantined"], ["T1"])
        self.assertEqual(report["interrupted"], [])
        t = s.state["tasks"]["T1"]
        self.assertEqual(t["status"], "QUARANTINED")
        self.assertEqual(t["attempts"]["interrupt"], 1)
        self.assertIsNone(s.next_task())
        self.assertTrue(s.is_quarantined(worktree))
        self.assertEqual([e for e in self.events() if e["type"] == "recovered"], [])

    def test_missing_worktree_is_quarantined_and_blocks_reuse(self):
        gone = self.tmp / "gone"
        s, report = self.interrupted(gone)
        self.assert_quarantined(s, report, gone)
        ev = [e for e in self.events() if e["type"] == "recovery_failed"][0]
        self.assertEqual((ev["preserved_status"], ev["worktree"]), ("worktree_missing", str(gone)))
        with self.assertRaises(ValueError):
            s.begin_attempt("T1")
        s.add_task("T2", "t", "r")
        with self.assertRaisesRegex(ValueError, "quarantined"):
            s.begin_attempt("T2", worktree=gone)
        with self.assertRaises(ValueError):
            s.transition("T1", "PENDING")
        self.assertEqual(s.recover()["quarantined"], [])  # not re-processed
        s.close()
        s = self.store()  # survives restart
        self.assertEqual(s.state["tasks"]["T1"]["status"], "QUARANTINED")
        self.assertTrue(s.is_quarantined(gone))

    def test_git_failure_is_quarantined_and_worktree_untouched(self):
        notgit = self.tmp / "notgit"
        notgit.mkdir()
        (notgit / "work.txt").write_text("keep me\n")
        s, report = self.interrupted(notgit)
        self.assert_quarantined(s, report, notgit)
        ev = [e for e in self.events() if e["type"] == "recovery_failed"][0]
        self.assertEqual(ev["preserved_status"], "error")
        self.assertTrue(ev["error"])
        self.assertEqual((notgit / "work.txt").read_text(), "keep me\n")

    def test_release_needs_classification_and_note(self):
        gone = self.tmp / "gone"
        s, _ = self.interrupted(gone)
        for args in [("bogus", "note"), ("nothing_to_preserve", "")]:
            with self.assertRaises(ValueError):
                s.release("T1", *args)
        s.release("T1", "nothing_to_preserve", "agent died before creating the worktree")
        self.assertEqual(s.state["tasks"]["T1"]["status"], "PENDING")
        self.assertFalse(s.is_quarantined(gone))
        rel = [e for e in self.events() if e["type"] == "released"][0]
        self.assertEqual(rel["classification"], "nothing_to_preserve")
        with self.assertRaises(ValueError):
            s.release("T1", "nothing_to_preserve", "not quarantined any more")
        self.assertEqual(s.begin_attempt("T1", worktree=gone), 2)

    def test_blocking_keeps_quarantine(self):
        gone = self.tmp / "gone"
        s, _ = self.interrupted(gone)
        s.transition("T1", "BLOCKED", reason="needs human")
        self.assertTrue(s.is_quarantined(gone))


class JournalAndState(Base):
    def test_second_controller_is_refused(self):
        self.store()
        with self.assertRaises(RuntimeError):
            Store(self.main)

    def test_illegal_transitions_rejected(self):
        s = self.store()
        s.add_task("T1", "t", "r")
        with self.assertRaises(ValueError):
            s.transition("T1", "EXECUTED", sha="x")
        s.begin_attempt("T1")
        with self.assertRaises(ValueError):
            s.begin_attempt("T1")
        with self.assertRaises(ValueError):
            s.transition("T1", "BLOCKED")
        with self.assertRaises(ValueError):
            s.add_task("T1", "dup", "r")
        with self.assertRaises(ValueError):
            s.add_task("bad id!", "t", "r")

    def test_verified_requires_source_sha(self):
        s = self.store()
        s.add_task("T1", "t", "r")
        s.begin_attempt("T1")
        s.record_result("T1", "completed")
        for sha in (None, ""):
            with self.assertRaisesRegex(ValueError, "source sha"):
                s.transition("T1", "VERIFIED", sha=sha)
        self.assertEqual(s.state["tasks"]["T1"]["status"], "EXECUTED")
        self.assertNotIn("VERIFIED", [e.get("to") for e in self.events()])
        s.transition("T1", "VERIFIED", sha="abc123")
        self.assertEqual(s.state["tasks"]["T1"]["verified_sha"], "abc123")

    def test_dependencies_gate_next_task(self):
        s = self.store()
        s.add_task("A", "a", "r")
        s.add_task("B", "b", "r", depends_on=["A"])
        self.assertEqual(s.next_task(), "A")
        s.begin_attempt("A")
        self.assertIsNone(s.next_task())
        s.record_result("A", "completed")
        self.assertIsNone(s.next_task())
        s.transition("A", "VERIFIED", sha="x")
        self.assertEqual(s.next_task(), "B")


class AtomicEvents(Base):
    """A failed event must leave state exactly as it was."""

    def store_in(self, status, name):
        root = self.tmp / ("root_" + name)
        root.mkdir()
        s = self.store(root)
        s.add_task("T1", "t", "r")
        if status != "PENDING":
            s.begin_attempt("T1")
        if status == "EXECUTED":
            s.record_result("T1", "completed")
        return s

    MALFORMED = {
        # each changes status (and sometimes counters) before the missing key is read in the old reducer
        "intent_missing_worktree": ("PENDING", dict(type="intent", task_id="T1", attempt=1, base_sha=None,
                                                    **{"from": "PENDING", "to": "EXECUTING"})),
        "result_missing_outcome": ("EXECUTING", dict(type="result", task_id="T1", attempt=1,
                                                     **{"from": "EXECUTING", "to": "EXECUTED"})),
        "recovery_failed_missing_worktree": ("EXECUTING", dict(type="recovery_failed", task_id="T1", attempt=1,
                                                               **{"from": "EXECUTING", "to": "QUARANTINED"})),
        "verified_missing_sha": ("EXECUTED", dict(type="transition", task_id="T1", attempt=1,
                                                  **{"from": "EXECUTED", "to": "VERIFIED"})),
    }

    def test_reduce_failure_leaves_state_unchanged(self):
        for name, (status, ev) in self.MALFORMED.items():
            with self.subTest(name):
                s = self.store_in(status, name)
                before = copy.deepcopy(s.state)
                with self.assertRaises((KeyError, ValueError)):
                    s._reduce(dict(ev, seq=99, ts="x"))
                self.assertEqual(s.state, before)

    def test_replay_does_not_persist_a_half_applied_event(self):
        s = self.store()
        s.add_task("T1", "t", "r")
        s.close()
        j = Journal(self.main / "artifacts" / "events.jsonl")
        good = j.append("intent", task_id="T1", attempt=1, worktree=None, base_sha=None,
                        **{"from": "PENDING", "to": "EXECUTING"})
        j.append("result", task_id="T1", attempt=1, **{"from": "EXECUTING", "to": "EXECUTED"})  # no outcome
        with self.assertRaisesRegex(RuntimeError, "replay stopped at seq %d" % (good["seq"] + 1)):
            Store(self.main)
        saved = json.loads((self.main / "artifacts" / "state" / "state.json").read_text())
        self.assertEqual(saved["last_seq"], good["seq"])
        self.assertEqual(saved["tasks"]["T1"]["status"], "EXECUTING")
        self.assertEqual(saved["tasks"]["T1"]["attempts"], {"coding": 0, "interrupt": 0, "timeout": 0})


class JournalIntegrity(Base):
    def journal_path(self):
        return self.main / "artifacts" / "events.jsonl"

    def seeded(self, n=3):
        s = self.store()
        for i in range(n - 1):
            s.add_task("T%d" % i, "t", "r")
        s.close()
        return self.journal_path().read_bytes()

    def assert_corrupt(self, data, pattern):
        jp = self.journal_path()
        jp.write_bytes(data)
        for _ in range(2):  # same error every time, so the lock is released
            with self.assertRaisesRegex(JournalCorrupt, pattern):
                Store(self.main)
        self.assertEqual(jp.read_bytes(), data)  # nothing modified
        self.assertEqual(list(jp.parent.glob("events.jsonl.torn-*")), [])

    def test_torn_tail_is_moved_aside_and_logged(self):
        good = self.seeded()
        jp = self.journal_path()
        jp.write_bytes(good + b'{"seq": 4, "ty')
        s = self.store()
        side = jp.parent / ("events.jsonl.torn-%d" % len(good))
        self.assertEqual(side.read_bytes(), b'{"seq": 4, "ty')
        self.assertEqual(jp.read_bytes(), good)
        self.assertEqual(s.journal.torn_tail, side)
        self.assertEqual(s.recover()["torn_tail"], side.name)
        s.add_task("T9", "t", "r")
        self.assertEqual([e["seq"] for e in s.journal.read()], [1, 2, 3, 4, 5])
        self.assertEqual([e["type"] for e in s.journal.read()][3], "journal_tail_discarded")

    def test_valid_final_record_without_newline_is_kept(self):
        good = self.seeded()
        self.journal_path().write_bytes(good.rstrip(b"\n"))
        s = self.store()
        self.assertIsNone(s.journal.torn_tail)
        s.add_task("T9", "t", "r")
        self.assertEqual([e["seq"] for e in s.journal.read()], [1, 2, 3, 4])

    def test_malformed_line_mid_file_is_corruption(self):
        lines = self.seeded().splitlines(True)
        self.assert_corrupt(lines[0] + b"not json\n" + lines[1] + lines[2], "line 2 is malformed")

    def test_blank_line_mid_file_is_corruption(self):
        lines = self.seeded().splitlines(True)
        self.assert_corrupt(lines[0] + b"\n" + lines[1] + lines[2], "line 2 is malformed")

    def test_malformed_last_line_with_newline_is_corruption_not_a_torn_tail(self):
        good = self.seeded()
        self.assert_corrupt(good + b'{"seq": 4, "ty\n', "line 4 is malformed")

    def test_sequence_gap_is_corruption(self):
        lines = self.seeded(4).splitlines(True)
        self.assert_corrupt(lines[0] + lines[2] + lines[3], "line 2: seq 3 after seq 1")

    def test_repeated_seq_is_corruption(self):
        lines = self.seeded().splitlines(True)
        self.assert_corrupt(lines[0] + lines[1] + lines[1], "line 3: seq 2 after seq 2")

    def test_journal_not_starting_at_one_is_corruption(self):
        lines = self.seeded().splitlines(True)
        self.assert_corrupt(lines[1] + lines[2], "line 1: seq 2 after seq 0")


if __name__ == "__main__":
    unittest.main()
