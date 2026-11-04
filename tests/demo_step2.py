"""Step 2 demo: one trivial coding task in a disposable worktree.

  python3 tests/demo_step2.py                 scripted stand-in CLI (no Docker Agent needed)
  python3 tests/demo_step2.py --docker        real standalone `docker-agent` + Claude Code harness
  python3 tests/demo_step2.py --docker --binary /path/to/docker-agent

The agent works in a throwaway git repo under the system temp dir, using the
capstone's loop/agents.yaml by absolute path. The demo, not the agent, checks the
result, and prints the raw evidence (doctor output, journal, log, git state,
file hashes) before the PASS/FAIL lines. Everything printed is also saved to
<throwaway repo>/demo_evidence.txt.
"""
import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from loop.runner import DROP_ENV, docker_agent_argv, docker_agent_preflight, run_attempt  # noqa: E402
from loop.state import Store  # noqa: E402

PROMPT = "Create a file named hello.txt in the current directory containing exactly: hello from the loop. Change nothing else."
IDENT = dict(GIT_AUTHOR_NAME="demo", GIT_AUTHOR_EMAIL="d@d", GIT_COMMITTER_NAME="demo", GIT_COMMITTER_EMAIL="d@d")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args), check=True, capture_output=True, text=True,
                          env=dict(os.environ, **IDENT)).stdout.strip()


def loop_hashes():
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((REPO / "loop").rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docker", action="store_true")
    ap.add_argument("--binary", default="docker-agent")
    ap.add_argument("--timeout", type=float, default=300)
    args = ap.parse_args()

    out = []

    def say(*a):
        line = " ".join(str(x) for x in a)
        out.append(line)
        print(line)

    config = REPO / "loop" / "agents.yaml"
    binary = args.binary if args.docker else [sys.executable, str(REPO / "tests" / "fake_docker_agent.py")]
    bin_argv = shlex.split(binary) if isinstance(binary, str) else list(binary)
    say("mode          :", "REAL docker-agent" if args.docker else "FAKE stand-in CLI (tests/fake_docker_agent.py)")
    problems = docker_agent_preflight(binary, config)
    say("preflight     :", "ok" if not problems else problems)
    if problems:
        sys.exit(2)
    doctor = subprocess.run(bin_argv + ["doctor", str(config)], capture_output=True, text=True, timeout=120)
    say("doctor output :")
    for line in (doctor.stdout + doctor.stderr).strip().splitlines()[-15:]:
        say("    " + line[:200])
    say("API key vars set in your shell (the runner strips them from the agent's env):",
        {k: k in os.environ for k in DROP_ENV})

    work = Path(tempfile.mkdtemp(prefix="loop-demo-"))
    git(work, "init", "-q")
    (work / "README.md").write_text("demo repo\n")
    (work / ".gitignore").write_text("build/\nartifacts/\ndemo_evidence.txt\n")
    git(work, "add", ".")
    git(work, "commit", "-qm", "base")
    base = git(work, "rev-parse", "HEAD")
    capstone_before = git(REPO, "status", "--porcelain")
    loop_before = loop_hashes()

    store = Store(work)
    store.add_task("hello", "create hello.txt", "coder")
    build = docker_agent_argv(config, PROMPT if args.docker else "ok", agent="coder", binary=binary)
    r = run_attempt(store, "hello", base, build, args.timeout)
    wt = Path(r["worktree"])
    got = (wt / "hello.txt").read_text().strip() if (wt / "hello.txt").exists() else None
    events = [e for e in store.journal.read() if e.get("task_id") == "hello"]
    launched = [e for e in events if e["type"] == "launched"]
    log_lines = Path(r["log"]).read_text(errors="replace").splitlines()
    loop_after = loop_hashes()

    say("demo repo     :", work, "(base %s)" % base[:12])
    say("worktree      :", wt)
    say("result        : outcome=%(outcome)s classification=%(classification)s exit=%(exit_code)s %(duration_s)ss" % r)
    say("journal (task 'hello'):")
    for e in events:
        keys = ("from", "to", "worktree", "base_sha", "pid", "pgid", "outcome", "classification", "exit_code", "duration_s", "log")
        say("  #%d %-9s %s" % (e["seq"], e["type"], json.dumps({k: e[k] for k in keys if k in e})))
    if launched:
        say("launched argv :", " ".join(launched[0]["argv"][:-1]), "<prompt>")
    say("agent log (%d lines, first 25) %s:" % (len(log_lines), r["log"]))
    for line in log_lines[:25]:
        say("    " + line[:200])
    say("worktree      : HEAD=%s status=%s" % (git(wt, "rev-parse", "HEAD")[:12], git(wt, "status", "--porcelain").splitlines()))
    say("hello.txt     :", repr(got))
    say("demo repo main checkout status:", git(work, "status", "--porcelain").splitlines() or "clean")
    say("capstone git status unchanged :", git(REPO, "status", "--porcelain") == capstone_before)
    say("capstone loop/ sha256 (%d files) unchanged :" % len(loop_before), loop_before == loop_after)

    checks = {
        "run completed": r["outcome"] == "completed",
        "launch recorded with pid/pgid": bool(launched) and "pgid" in launched[0],
        "log captured": len(log_lines) > 0,
        "worktree is at the base SHA": git(wt, "rev-parse", "HEAD") == base,
        "hello.txt is in the designated worktree": got is not None and got.startswith("hello"),
        "worktree contains only hello.txt as a change": git(wt, "status", "--porcelain").splitlines() == ["?? hello.txt"],
        "throwaway repo checkout untouched": git(work, "status", "--porcelain") == "" and not (work / "hello.txt").exists(),
        "capstone repo status unchanged": git(REPO, "status", "--porcelain") == capstone_before,
        "capstone loop/ files unchanged": loop_before == loop_after,
        "config path is the capstone's main copy, outside the worktree":
            bool(launched) and launched[0]["argv"][launched[0]["argv"].index("--exec") + 1] == str(config.resolve())
            and wt not in config.resolve().parents,
        "--working-dir is the worktree":
            bool(launched) and launched[0]["argv"][launched[0]["argv"].index("--working-dir") + 1] == str(wt.resolve()),
    }
    for name, ok in checks.items():
        say("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    ok = all(checks.values())
    say("DEMO CHECK    :", "PASS" if ok else "FAIL")
    (work / "demo_evidence.txt").write_text("\n".join(out) + "\n")
    print("evidence saved:", work / "demo_evidence.txt")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
