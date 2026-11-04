"""Step 5 fixture: a tiny calc repo, controller-owned verification profiles, and a policy for the Coordinator."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loop.controller import sha256_path  # noqa: E402

HERE = Path(__file__).resolve().parent
IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
OBJECTIVE = "Make src/calc.py a correct calculator module with add(a, b), mul(a, b) and div(a, b)."

PRE = "import os, sys\nsys.path.insert(0, os.path.join(os.getcwd(), 'src'))\nfrom calc import *\n"
CHECKS = {
    "add": ("add(2, 3) == 5", "add(2,3)==5"),
    "mul": ("add(2, 3) == 5 and mul(2, 3) == 6", "mul(2,3)==6 and add still correct"),
    "div": ("add(2, 3) == 5 and mul(2, 3) == 6 and div(6, 3) == 2", "div(6,3)==2 and earlier functions still correct"),
    "full": ("add(2, 3) == 5 and mul(2, 3) == 6 and div(6, 3) == 2 and div(7, 2) == 3.5", "the whole objective"),
}


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, env=dict(os.environ, **IDENT))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def make_repo(tmp):
    main = Path(tmp) / "main"
    (main / "src").mkdir(parents=True)
    (main / "docs").mkdir()
    (main / "tests_trusted").mkdir()
    (main / "src" / "calc.py").write_text("def add(a, b):\n    return 0  # broken\n")
    (main / "docs" / "README.md").write_text("calc\n")
    (main / "tests_trusted" / "accept.md").write_text("acceptance: see profiles\n")
    (main / ".gitignore").write_text("artifacts/\nbuild/\n")
    git(main, "init", "-q")
    git(main, "add", "-A")
    git(main, "commit", "-qm", "base")
    return main, git(main, "rev-parse", "HEAD")


def make_profiles(tmp):
    d = Path(tmp) / "trusted"
    d.mkdir()
    profiles = {}
    for pid, (expr, desc) in CHECKS.items():
        f = d / ("check_%s.py" % pid)
        f.write_text(PRE + "assert %s, %r\n" % (expr, pid + " failed"))
        profiles[pid] = dict(description=desc, test_argv=[sys.executable, str(f)], test_files={str(f): sha256_path(f)})
    return profiles


def make_policy(tmp, base, binary=None, mode="mock", agent_config=None, **budgets):
    agents = Path(tmp) / "agents.yaml"
    agents.write_text("agents: {}\n")  # the stand-in CLI ignores it; the real runner needs an existing absolute path
    b = dict(max_attempts=2, max_runtime_s=600, agent_timeout_s=60, verify_timeout_s=60, verify_retries=1,
             max_replans=1, max_tasks=5, max_invocations=40, max_total_runtime_s=1200)
    b.update(budgets)
    return {
        "objective": OBJECTIVE, "constraints": "Python only. Change nothing outside the allowed paths.", "base_sha": base, "mode": mode,
        "agent": {"config": agent_config or str(agents), "binary": binary or [sys.executable, str(HERE / "fake_role_cli.py")]},
        "allowed_roots": ["src", "docs"], "protected": ["tests_trusted"],
        "coder_roles": {"coder": {"agent": "coder", "allowed_roots": ["src", "docs"]}},
        "profiles": make_profiles(tmp), "final_gate": "full",
        "review": {"per_task": "all", "final": True, "max_fixes": 1},
        "proposed_test_argv": [sys.executable, "{file}"],
        "budgets": b,
    }
