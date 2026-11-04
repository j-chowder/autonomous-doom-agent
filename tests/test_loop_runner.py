import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
FAKE = str(REPO / "tests" / "fake_agent.py")
FAKE_CLI = [sys.executable, str(REPO / "tests" / "fake_docker_agent.py")]
sys.path.insert(0, str(REPO))

from loop.runner import PreservationFailed, docker_agent_argv, docker_agent_preflight, run_attempt  # noqa: E402
from loop.state import Store  # noqa: E402

IDENT = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}

CONTROLLER = """
import os, sys
sys.path.insert(0, sys.argv[1])
from loop.state import Store
from loop.runner import run_attempt
root, base, fake, hb = sys.argv[2:6]
s = Store(root)
run_attempt(s, "T1", base, [sys.executable, fake, "hang"], timeout_s=60, env=dict(os.environ, FAKE_HB=hb))
"""


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, env=dict(os.environ, **IDENT))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


class RunnerBase(unittest.TestCase):
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
        self.hb = self.tmp / "heartbeat"
        self.store = Store(self.main)
        self.addCleanup(self.store.close)
        self.store.add_task("T1", "make hello.txt", "build_coder")

    def agent(self, mode):
        return [sys.executable, FAKE, mode]

    def env(self, **extra):
        return dict(os.environ, FAKE_HB=str(self.hb), FAKE_JOURNAL=str(self.main / "artifacts" / "events.jsonl"), **extra)

    def run1(self, mode, timeout_s=20, **kw):
        return run_attempt(self.store, "T1", self.base, self.agent(mode), timeout_s, env=self.env(), **kw)

    def events(self, root=None):
        out = []
        for line in (Path(root or self.main) / "artifacts" / "events.jsonl").read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def assert_heartbeat_stopped(self):
        size = self.hb.stat().st_size
        self.assertGreater(size, 0, "heartbeat never started")
        time.sleep(0.4)
        self.assertEqual(self.hb.stat().st_size, size, "a child process survived")

    def snapshot_file(self, result, name):
        return git(self.main, "show", result["snapshot_commit"] + ":" + name)


class Success(RunnerBase):
    def test_intent_and_worktree_recorded_before_agent_runs(self):
        r = self.run1("ok")
        self.assertEqual((r["outcome"], r["classification"], r["exit_code"]), ("completed", "exit_0", 0))
        wt = Path(r["worktree"])
        self.assertEqual((wt / "hello.txt").read_text(), "hello\n")
        self.assertFalse((self.main / "hello.txt").exists())
        self.assertEqual(git(wt, "rev-parse", "HEAD"), self.base)
        seen = json.loads((wt / "observed.json").read_text())  # written by the agent at startup
        self.assertEqual(os.path.realpath(seen["intent_worktree"]), os.path.realpath(wt))
        self.assertFalse(seen["api_key_set"])
        t = self.events()
        types = [e["type"] for e in t if e["type"] in ("intent", "launched", "result")]
        self.assertEqual(types, ["intent", "launched", "result"])
        intent = [e for e in t if e["type"] == "intent"][0]
        self.assertEqual((intent["worktree"], intent["base_sha"]), (str(wt), self.base))
        self.assertEqual(self.store.state["tasks"]["T1"]["status"], "EXECUTED")
        self.assertIn("agent done", (self.main / r["log"].replace(str(self.main) + "/", "")).read_text())

    def test_api_key_is_not_passed_to_the_agent(self):
        r = run_attempt(self.store, "T1", self.base, self.agent("ok"), 20,
                        env=self.env(ANTHROPIC_API_KEY="sk-test", ANTHROPIC_AUTH_TOKEN="tok"))
        seen = json.loads((Path(r["worktree"]) / "observed.json").read_text())
        self.assertFalse(seen["api_key_set"])

    def test_leftover_child_processes_are_killed_after_normal_exit(self):
        r = self.run1("ok_leftover")
        self.assertEqual(r["outcome"], "completed")
        self.assert_heartbeat_stopped()

    def test_each_attempt_gets_a_fresh_worktree_from_base(self):
        r1 = self.run1("fail")
        self.store.transition("T1", "PENDING")
        r2 = self.run1("ok")
        self.assertNotEqual(r1["worktree"], r2["worktree"])
        self.assertTrue((Path(r1["worktree"]) / "partial.txt").exists())
        self.assertFalse((Path(r2["worktree"]) / "partial.txt").exists())
        self.assertEqual(r2["attempt"], 2)


class Failures(RunnerBase):
    def test_nonzero_exit_is_failed_and_work_is_preserved(self):
        r = self.run1("fail")
        self.assertEqual((r["outcome"], r["classification"], r["exit_code"]), ("failed", "nonzero_exit", 3))
        t = self.store.state["tasks"]["T1"]
        self.assertEqual((t["status"], t["attempts"]), ("FAILED", {"coding": 1, "interrupt": 0, "timeout": 0}))
        self.assertEqual(self.snapshot_file(r, "partial.txt"), "half")
        self.assertTrue((Path(r["worktree"]) / "partial.txt").exists())  # worktree kept
        self.assertIn("boom", Path(r["log"]).read_text())
        res = [e for e in self.events() if e["type"] == "result"][0]
        self.assertEqual((res["classification"], res["preserved_dir"].split("/")[-1].startswith("T1-a1")), ("nonzero_exit", True))

    def test_death_by_signal(self):
        r = self.run1("die")
        self.assertEqual((r["outcome"], r["classification"], r["exit_code"]), ("failed", "killed_by_signal_9", -9))

    def test_timeout_kills_the_whole_process_group(self):
        r = self.run1("hang", timeout_s=1, grace_s=1)
        self.assertEqual((r["outcome"], r["classification"]), ("timeout", "timeout"))
        self.assertLess(r["duration_s"], 6)
        t = self.store.state["tasks"]["T1"]
        self.assertEqual(t["attempts"], {"coding": 1, "interrupt": 0, "timeout": 1})
        self.assertEqual(self.snapshot_file(r, "partial.txt"), "half")
        self.assert_heartbeat_stopped()

    def test_sigterm_ignored_escalates_to_sigkill(self):
        r = self.run1("stubborn", timeout_s=1, grace_s=1)
        self.assertEqual((r["classification"], r["exit_code"]), ("timeout", -9))
        self.assert_heartbeat_stopped()

    def test_launch_error_is_failed_without_a_launched_event(self):
        r = run_attempt(self.store, "T1", self.base, ["/nonexistent/agent"], 5, env=self.env())
        self.assertEqual((r["outcome"], r["classification"], r["exit_code"]), ("failed", "launch_error", None))
        self.assertNotIn("launched", [e["type"] for e in self.events()])
        self.assertIn("launch error", Path(r["log"]).read_text())
        self.assertEqual(self.store.state["tasks"]["T1"]["status"], "FAILED")

    def test_preservation_failure_leaves_task_executing(self):
        with mock.patch.object(Store, "preserve_attempt", return_value={"status": "error", "error": "disk full", "dir": "x"}):
            with self.assertRaisesRegex(PreservationFailed, "disk full"):
                self.run1("fail")
        self.assertEqual(self.store.state["tasks"]["T1"]["status"], "EXECUTING")
        self.assertNotIn("result", [e["type"] for e in self.events()])
        self.assertEqual(self.store.recover()["interrupted"], ["T1"])  # real preservation now succeeds


class Inputs(RunnerBase):
    def test_base_sha_must_be_a_full_existing_commit_sha(self):
        before = len(self.events())
        for bad in ("HEAD", "main", self.base[:10], "0" * 40, self.base.upper(), None):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    run_attempt(self.store, "T1", bad, self.agent("ok"), 5, env=self.env())
        self.assertEqual(len(self.events()), before)
        self.assertFalse((self.main / "build").exists())
        self.assertEqual(self.store.state["tasks"]["T1"]["status"], "PENDING")

    def test_task_must_be_pending(self):
        self.run1("ok")
        with self.assertRaisesRegex(ValueError, "not PENDING"):
            self.run1("ok")

    def test_docker_agent_argv_shape_and_validation(self):
        cfg = self.main / "loop" / "agents.yaml"
        cfg.parent.mkdir()
        cfg.write_text("agents: {}\n")
        wt = self.tmp / "wt"
        wt.mkdir()
        build = docker_agent_argv(cfg, "do it", agent="coder")
        self.assertEqual(build(wt), ["docker-agent", "run", "--exec", str(cfg.resolve()), "-a", "coder",
                                     "--working-dir", str(wt.resolve()), "do it"])
        self.assertEqual(docker_agent_argv(cfg, "p", binary=["x", "y"])(wt)[:3], ["x", "y", "run"])
        self.assertNotIn("-a", docker_agent_argv(cfg, "p")(wt))
        with self.assertRaises(ValueError):
            docker_agent_argv(cfg, "--yolo")
        with self.assertRaises(ValueError):
            docker_agent_argv("loop/agents.yaml", "p")  # relative
        with self.assertRaises(ValueError):
            docker_agent_argv(self.main / "nope.yaml", "p")  # missing
        with self.assertRaisesRegex(ValueError, "inside the worktree"):
            docker_agent_argv(cfg, "p")(self.main)  # config lives under this "worktree"

    def test_preflight_uses_the_installed_cli(self):
        cfg = self.main / "agents.yaml"
        cfg.write_text("agents: {}\n")
        self.assertEqual(docker_agent_preflight(FAKE_CLI, cfg), [])
        with mock.patch.dict(os.environ, {"FAKE_CLI_HELP_OMIT": "--working-dir"}):
            self.assertEqual(docker_agent_preflight(FAKE_CLI, cfg), ["`run --help` does not mention --working-dir"])
        with mock.patch.dict(os.environ, {"FAKE_CLI_DOCTOR_FAIL": "1"}):
            self.assertRegex(docker_agent_preflight(FAKE_CLI, cfg)[0], "doctor .* exited 1: not logged in")
        self.assertRegex(docker_agent_preflight(["/nonexistent/docker-agent"], cfg)[0], "cannot run")


class StandaloneCli(RunnerBase):
    """End to end through argv syntax: config from the main checkout, agent confined by --working-dir."""

    def test_uncommitted_main_config_and_working_dir(self):
        cfg = self.main / "loop" / "agents.yaml"  # uncommitted, so a worktree at base_sha does not have it
        cfg.parent.mkdir()
        cfg.write_text("agents: {}\n")
        self.assertEqual(git(self.main, "status", "--porcelain", "--", "loop"), "?? loop/")
        r = run_attempt(self.store, "T1", self.base, docker_agent_argv(cfg, "ok", agent="coder", binary=FAKE_CLI),
                        20, env=self.env())
        self.assertEqual((r["outcome"], r["classification"]), ("completed", "exit_0"))
        wt = Path(r["worktree"])
        self.assertEqual((wt / "hello.txt").read_text(), "hello\n")
        self.assertFalse((wt / "loop" / "agents.yaml").exists())
        self.assertFalse((self.main / "hello.txt").exists())
        self.assertEqual(git(self.main, "status", "--porcelain").replace("?? loop/", "").replace("?? artifacts/", "")
                         .replace("?? build/", "").strip(), "")
        launched = [e for e in self.events() if e["type"] == "launched"][0]
        self.assertEqual(launched["argv"][-5:-1], ["-a", "coder", "--working-dir", os.path.realpath(wt)][:4])
        self.assertEqual(launched["argv"][launched["argv"].index("--exec") + 1], str(cfg.resolve()))

    def test_cli_syntax_error_is_a_failed_attempt_not_a_silent_success(self):
        cfg = self.main / "agents.yaml"
        cfg.write_text("agents: {}\n")
        bad = lambda wt: FAKE_CLI + ["run", cfg.as_posix(), "ok"]  # missing --exec/--working-dir
        r = run_attempt(self.store, "T1", self.base, bad, 20, env=self.env())
        self.assertEqual(r["outcome"], "failed")
        self.assertEqual(r["classification"], "nonzero_exit")


class ControllerDies(RunnerBase):
    def start_controller(self):
        self.store.close()
        self.child = subprocess.Popen([sys.executable, "-c", CONTROLLER, str(REPO), str(self.main), self.base, FAKE, str(self.hb)],
                                      env=dict(os.environ, **IDENT, PYTHONDONTWRITEBYTECODE="1"),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.child.kill)
        launched = None
        deadline = time.time() + 15
        while time.time() < deadline and not (launched and self.hb.exists() and self.hb.stat().st_size):
            launched = next((e for e in self.events() if e["type"] == "launched"), None)
            time.sleep(0.05)
        self.assertIsNotNone(launched, "agent never launched")
        self.addCleanup(self._kill_group, launched["pgid"])
        return launched

    @staticmethod
    def _kill_group(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_sigterm_stops_the_agent_and_recovery_preserves_its_work(self):
        self.start_controller()
        self.child.send_signal(signal.SIGTERM)
        self.assertNotEqual(self.child.wait(timeout=20), 0)
        self.assert_heartbeat_stopped()
        s = Store(self.main)
        self.addCleanup(s.close)
        self.assertEqual(s.state["tasks"]["T1"]["status"], "EXECUTING")
        report = s.recover()
        self.assertEqual((report["interrupted"], report["quarantined"]), (["T1"], []))
        self.assertEqual(s.state["tasks"]["T1"]["status"], "PENDING")
        rec = [e for e in self.events() if e["type"] == "recovered"][0]
        self.assertEqual(git(self.main, "show", rec["snapshot_commit"] + ":partial.txt"), "half")

    def test_sigkill_orphans_the_agent_and_recovery_quarantines_instead_of_preserving(self):
        launched = self.start_controller()
        self.child.kill()
        self.child.wait()
        os.killpg(launched["pgid"], 0)  # orphan is still alive
        s = Store(self.main)
        self.addCleanup(s.close)
        report = s.recover()
        self.assertEqual((report["interrupted"], report["quarantined"]), ([], ["T1"]))
        ev = [e for e in self.events() if e["type"] == "recovery_failed"][0]
        self.assertEqual(ev["preserved_status"], "agent_running")
        self.assertIn("may still be running", ev["error"])
        wt = s.state["tasks"]["T1"]["current"]["worktree"]
        self.assertTrue(s.is_quarantined(wt))
        self.assertTrue((Path(wt) / "partial.txt").exists())
        self._kill_group(launched["pgid"])
        s.release("T1", "preserved_manually", "killed orphaned agent")
        self.assertEqual(s.state["tasks"]["T1"]["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
