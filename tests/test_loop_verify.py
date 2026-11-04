import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from loop.runner import create_worktree  # noqa: E402
from loop.verify import ERROR, FAIL, PASS, verify  # noqa: E402

IDENT = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}

# Controller-owned test: lives outside the repo, runs in the exported copy (cwd).
CHECK = """
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
from calc import add
assert add(2, 3) == 5, "add(2, 3) != 5"
if len(sys.argv) > 1:  # simulate the source changing while verification runs
    with open(sys.argv[1], "a") as f:
        f.write("# changed during verification\\n")
"""
ALLOWED = ["src"]
PROTECTED = ["tests_trusted"]


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, env=dict(os.environ, **IDENT))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


class VerifyBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        main = tmp / "main"
        (main / "src").mkdir(parents=True)
        (main / "tests_trusted").mkdir()
        (main / "docs").mkdir()
        (main / "src" / "calc.py").write_text("def add(a, b):\n    return 0  # broken\n")
        (main / "tests_trusted" / "accept.md").write_text("criteria v1\n")
        (main / "docs" / "notes.txt").write_text("notes\n")
        (main / ".gitignore").write_text("ignored_*.py\n")
        git(main, "init", "-q")
        git(main, "add", "-A")
        git(main, "commit", "-qm", "base")
        self.base = git(main, "rev-parse", "HEAD")
        self.wt = tmp / "wt"
        create_worktree(main, self.wt, self.base)
        self.check = tmp / "check.py"
        self.check.write_text(CHECK)
        self.ev = tmp / "evidence"

    def fix(self, ok=True):
        body = "def add(a, b):\n    return a + b\n" if ok else "def add(a, b):\n    return a - b\n"
        (self.wt / "src" / "calc.py").write_text(body)

    def run_verify(self, argv=None):
        argv = argv or [sys.executable, str(self.check)]
        return verify(self.wt, self.base, ALLOWED, PROTECTED, argv, self.ev, timeout_s=60)


class VerifyTests(VerifyBase):
    def test_1_authorized_change_passes(self):
        self.fix()
        (self.wt / "src" / "helper.py").write_text("X = 1\n")  # untracked, but inside allowed
        (self.wt / "src" / "ignored_ok.py").write_text("X = 1\n")  # ignored, inside allowed: fine
        r = self.run_verify()
        self.assertEqual((r["decision"], r["classification"], r["exit_code"]), (PASS, "tests_passed", 0))
        self.assertEqual(r["fingerprint"], r["fingerprint_after"])
        self.assertEqual({c["path"] for c in r["changed"]}, {"src/calc.py", "src/helper.py"})
        self.assertEqual(json.loads((self.ev / "verify.json").read_text())["decision"], PASS)
        self.assertTrue((self.ev / "verify.log").exists())

    def test_2_unauthorized_change_fails_and_tests_do_not_run(self):
        self.fix()
        (self.wt / "tests_trusted" / "accept.md").write_text("criteria weakened\n")  # tracked, protected
        (self.wt / "docs" / "notes.txt").unlink()                                   # deleted, outside allowed
        (self.wt / "evil.py").write_text("x = 1\n")                                 # untracked, outside allowed
        (self.wt / "tests_trusted" / "ignored_a.py").write_text("x = 1\n")         # ignored, protected
        (self.wt / "ignored_b.py").write_text("x = 1\n")                           # ignored, outside allowed
        r = self.run_verify()
        self.assertEqual((r["decision"], r["classification"]), (FAIL, "scope_violation"))
        self.assertIsNone(r["exit_code"])
        got = {(v["status"], v["path"], v["reason"]) for v in r["violations"]}
        self.assertEqual(got, {("M", "tests_trusted/accept.md", "protected"),
                               ("D", "docs/notes.txt", "outside_allowed"),
                               ("A", "evil.py", "outside_allowed"),
                               ("I", "tests_trusted/ignored_a.py", "protected"),
                               ("I", "ignored_b.py", "outside_allowed")})

    def test_3_failing_test_fails(self):
        self.fix(ok=False)
        r = self.run_verify()
        self.assertEqual((r["decision"], r["classification"]), (FAIL, "test_failed"))
        self.assertEqual(r["exit_code"], 1)
        self.assertIn("add(2, 3) != 5", (self.ev / "verify.log").read_text())

    def test_4_source_changed_after_testing_invalidates(self):
        self.fix()
        r = self.run_verify([sys.executable, str(self.check), str(self.wt / "src" / "calc.py")])
        self.assertEqual(r["exit_code"], 0)  # the tests themselves passed
        self.assertEqual((r["decision"], r["classification"]), (ERROR, "stale_source"))
        self.assertNotEqual(r["fingerprint"], r["fingerprint_after"])

    def test_5_missing_test_command_is_error_not_fail(self):
        self.fix()
        r = self.run_verify(["/nonexistent/test-runner"])
        self.assertEqual((r["decision"], r["classification"]), (ERROR, "test_launch_error"))


if __name__ == "__main__":
    unittest.main()
