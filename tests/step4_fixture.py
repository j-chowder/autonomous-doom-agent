"""Trivial fixture shared by the Step 4 tests and demo: a repo whose add() is broken, plus a trusted check."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loop.controller import sha256_path  # noqa: E402

IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
CHECK = """
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
from calc import add
assert add(2, 3) == 5, "add(2, 3) != 5"
"""
PROMPT = "Fix add() in src/calc.py so that add(2, 3) == 5. Change nothing outside src/."
HERE = Path(__file__).resolve().parent


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, env=dict(os.environ, **IDENT))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def make_repo(tmp):
    """-> (main repo path, base sha, trusted check script path outside the repo)."""
    tmp = Path(tmp)
    main = tmp / "main"
    (main / "src").mkdir(parents=True)
    (main / "tests_trusted").mkdir()
    (main / "src" / "calc.py").write_text("def add(a, b):\n    return 0  # broken\n")
    (main / "tests_trusted" / "accept.md").write_text("add(2, 3) must equal 5\n")
    (main / ".gitignore").write_text("artifacts/\nbuild/\n")
    git(main, "init", "-q")
    git(main, "add", "-A")
    git(main, "commit", "-qm", "base")
    check = tmp / "check.py"
    check.write_text(CHECK)
    return main, git(main, "rev-parse", "HEAD"), check


def make_cfg(tmp, base, check, binary=None, **budgets):
    agents = Path(tmp) / "agents.yaml"
    agents.write_text("agents: {}\n")  # the stand-in CLI ignores it; the real runner needs an existing absolute path
    return {
        "base_sha": base,
        "agent": {"config": str(agents), "binary": binary or [sys.executable, str(HERE / "fake_coder_cli.py")]},
        "budgets": dict(dict(max_attempts=3, max_runtime_s=300, agent_timeout_s=30, verify_timeout_s=30, verify_retries=1), **budgets),
        "tasks": {"T1": {"prompt": PROMPT, "allowed": ["src"], "protected": ["tests_trusted"],
                         "test_argv": [sys.executable, str(check)],
                         "test_files": {str(check): sha256_path(check)}}},
    }
