"""Tests for loop/sandbox_verify.py with a FAKE `docker` on PATH. Nothing here starts a real container.

What is real: the wrapper, verify.py, git worktrees, exit-code classification, signal handling.
What is mocked: docker itself. The real container run is tests/demo_sandbox_verify.py (needs Docker).
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from loop import sandbox_verify as sv  # noqa: E402
from loop.runner import create_worktree  # noqa: E402
from loop.verify import ERROR, ERROR_EXIT, FAIL, PASS, verify  # noqa: E402

WRAPPER = str(REPO / "loop" / "sandbox_verify.py")
IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
IMAGE_ID = "sha256:" + "ab" * 32

FAKE_DOCKER = """#!%s
import json, os, sys, time
a = sys.argv[1:]
log = os.environ.get("FAKE_DOCKER_LOG")
def rec():
    if log:
        open(log, "a").write(json.dumps(a) + "\\n")
if a[:1] == ["--version"]:
    print("Docker version fake"); sys.exit(0)
if a[:2] == ["image", "inspect"]:
    if os.environ.get("FAKE_NO_IMAGE"):
        sys.exit(1)
    print("%s"); sys.exit(0)
if a[:1] == ["kill"]:
    rec(); sys.exit(0)
if a[:1] == ["build"]:
    rec(); sys.exit(int(os.environ.get("FAKE_BUILD_RC", "0")))
if a[:1] == ["run"]:
    rec()
    if os.environ.get("FAKE_SLEEP"):
        time.sleep(float(os.environ["FAKE_SLEEP"]))
    sys.exit(int(os.environ.get("FAKE_RC", "0")))
sys.exit(0)
""" % (sys.executable, IMAGE_ID)


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, env=dict(os.environ, **IDENT))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "docker"
        fake.write_text(FAKE_DOCKER)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.log = self.tmp / "docker.log"
        self.trusted = self.tmp / "trusted"
        self.trusted.mkdir()
        self.cand = self.tmp / "cand"
        self.cand.mkdir()
        self.out_root = self.tmp / "out"
        self.env = dict(os.environ, PATH="%s%s%s" % (self.bin, os.pathsep, os.environ["PATH"]), FAKE_DOCKER_LOG=str(self.log))

    def argv(self, *extra, cmd=("true",)):
        return [sys.executable, WRAPPER, "run", "--trusted", str(self.trusted), "--out-root", str(self.out_root)] + list(extra) + ["--"] + list(cmd)

    def wrapper(self, rc=0, **env):
        e = dict(self.env, FAKE_RC=str(rc), **env)
        return subprocess.run(self.argv(), cwd=str(self.cand), env=e, capture_output=True, text=True, timeout=60)

    def logged(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


class WrapperTests(Base):
    def test_1_error_exit_matches_verify(self):
        self.assertEqual(sv.ERROR_EXIT, ERROR_EXIT)

    def test_2_docker_command_is_locked_down(self):
        p = self.wrapper(0)
        self.assertEqual(p.returncode, 0, p.stderr)
        run = [x for x in self.logged() if x[0] == "run"][0]
        joined = " ".join(run)
        self.assertEqual(run[run.index("--network") + 1], "none")
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("no-new-privileges", joined)
        mounts = [run[i + 1] for i, x in enumerate(run) if x == "--mount"]
        self.assertEqual(len(mounts), 3)
        by_target = {m.split("target=")[1].split(",")[0]: m for m in mounts}
        self.assertTrue(by_target["/workspace"].endswith(",readonly"))
        self.assertTrue(by_target["/trusted"].endswith(",readonly"))
        self.assertFalse(by_target["/out"].endswith("readonly"))
        self.assertIn("source=%s," % os.path.realpath(self.cand), by_target["/workspace"])
        self.assertIn(IMAGE_ID, run)                       # image by ID, not tag
        self.assertNotIn("-e", run)                        # no environment forwarded
        self.assertNotIn("--env", run)
        self.assertFalse([m for m in mounts if os.path.expanduser("~") == m.split("source=")[1].split(",")[0]])

    def test_3_exit_codes_are_translated(self):
        for raw, want in [(0, 0), (1, 1), (2, 2), (124, 70), (125, 70), (137, 70), (127, 127)]:
            self.assertEqual(self.wrapper(raw).returncode, want, "raw %d" % raw)

    def test_4_missing_docker_and_missing_image_are_error(self):
        p = subprocess.run(self.argv(), cwd=str(self.cand), env=dict(self.env, PATH=str(self.tmp / "empty")),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 70)
        self.assertIn("docker not found", p.stderr)
        p = self.wrapper(0, FAKE_NO_IMAGE="1")
        self.assertEqual(p.returncode, 70)
        self.assertIn("not found or docker not running", p.stderr)
        self.assertEqual([x for x in self.logged() if x[0] == "run"], [])  # nothing was started

    def test_5_evidence_records_image_id_and_exit(self):
        p = self.wrapper(1)
        self.assertIn("SANDBOX_EVIDENCE", p.stdout)
        rec = json.loads(next(self.out_root.glob("*/sandbox.json")).read_text())
        self.assertEqual(rec["image_id"], IMAGE_ID)
        self.assertEqual((rec["raw_exit"], rec["exit"], rec["network"]), (1, 1, "none"))
        self.assertTrue(os.access(str(next(self.out_root.iterdir())), os.W_OK))

    def test_6_build_records_image_id(self):
        d = self.tmp / "rec"
        p = subprocess.run([sys.executable, WRAPPER, "build", "--record-dir", str(d)], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        rec = json.loads((d / "toolchain-image.json").read_text())
        self.assertEqual(rec["image_id"], IMAGE_ID)
        self.assertEqual(len(rec["dockerfile_sha256"]), 64)
        self.assertEqual(len(list(d.glob("toolchain-image-*.json"))), 1)
        p = subprocess.run([sys.executable, WRAPPER, "build", "--record-dir", str(d)], env=dict(self.env, FAKE_BUILD_RC="1"),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1)  # a failed build is reported, not recorded as an image


class ThroughVerify(Base):
    """verify.py with the wrapper as test_argv: the real controller path, fake docker."""

    def setUp(self):
        super().setUp()
        main = self.tmp / "main"
        (main / "src").mkdir(parents=True)
        (main / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        git(main, "init", "-q")
        git(main, "add", "-A")
        git(main, "commit", "-qm", "base")
        self.base = git(main, "rev-parse", "HEAD")
        self.wt = self.tmp / "wt"
        create_worktree(main, self.wt, self.base)
        self.ev = self.tmp / "ev"

    def run_verify(self, rc=0, timeout_s=60, **env):
        e = dict(self.env, FAKE_RC=str(rc), **env)
        with mock.patch.dict(os.environ, e):
            return verify(self.wt, self.base, ["src"], ["tests_trusted"], self.argv(), self.ev, timeout_s=timeout_s)

    def test_7_pass_fail_and_docker_error_classification(self):
        r = self.run_verify(0)
        self.assertEqual((r["decision"], r["classification"]), (PASS, "tests_passed"))
        r = self.run_verify(1)
        self.assertEqual((r["decision"], r["classification"]), (FAIL, "test_failed"))
        r = self.run_verify(125)  # docker itself failed: must NOT look like a behavioural FAIL
        self.assertEqual((r["decision"], r["classification"]), (ERROR, "test_error_exit"))
        self.assertIn("SANDBOX_EVIDENCE", Path(r["log"]).read_text())  # evidence path travels with verify.json

    def test_8_timeout_kills_the_container_and_is_error(self):
        r = self.run_verify(0, timeout_s=2, FAKE_SLEEP="30")
        self.assertEqual((r["decision"], r["classification"]), (ERROR, "test_timeout"))
        runs = [x for x in self.logged() if x[0] == "run"]
        kills = [x for x in self.logged() if x[0] == "kill"]
        self.assertEqual(len(runs), 1)
        name = runs[0][runs[0].index("--name") + 1]
        self.assertEqual(kills, [["kill", name]])  # the container, not only the docker client, was stopped


if __name__ == "__main__":
    unittest.main()
