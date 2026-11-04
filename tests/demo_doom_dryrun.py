"""DOOM dry run with MOCK agents and a STUB gate. Proves the DOOM policy wiring; proves nothing about DOOM itself.

  python3 tests/demo_doom_dryrun.py

Real here: doom_setup lint/freeze/policy, the Coordinator, Controller, worktrees, scope checks (allowed roots incl. build.sh, gitignored dist/),
flagged review, final review, final gate, frozen-test hash check, the sandbox wrapper (with a FAKE docker, only to prove the argv is accepted).
Mocked: every agent, the gate itself (tests/doom_dryrun_gate.py), docker.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
from loop import doom_setup as ds  # noqa: E402
from loop.coordinator import Coordinator  # noqa: E402
from loop.state import Store  # noqa: E402
from step5_fixture import IDENT, git  # noqa: E402

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print("%s  %s %s" % ("PASS" if cond else "FAIL", name, detail))


def make_repo(tmp):
    r = Path(tmp) / "main"
    for p, t in {"platform/doomgeneric_wasm.c": "// stub\n", "web/index.html": "<html></html>\n", "third_party/doomgeneric/x.c": "x\n",
                 "assets/doom1.wad": "WAD\n", "loop/keep": "x\n", "AGENTS.md": "a\n", ".gitignore": "artifacts/\nbuild/\ndist/\n"}.items():
        (r / p).parent.mkdir(parents=True, exist_ok=True)
        (r / p).write_text(t)
    (r / "tests/doom/trusted").mkdir(parents=True)
    (r / ds.CONTRACT).write_text((REPO / ds.CONTRACT).read_text())
    ids = " ".join(ds.assertion_ids((REPO / ds.CONTRACT).read_text()))
    (r / ds.TRUSTED / "run.sh").write_text("#!/bin/bash\n# gates: %s controls\n# ids: %s\n" % (" ".join(ds.GATES), ids))
    (r / ds.TRUSTED / "README.md").write_text("stub thresholds\n")
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    return r


def main():
    tmp = Path(tempfile.mkdtemp(prefix="loop-doom-dry-"))
    root = make_repo(tmp)
    print("MOCK agents, STUB gate. Temp repo: %s\n" % root)
    check("lint passes on a stub that lists every id and gate", ds.lint(root) == [])
    (root / ds.TRUSTED / "run.sh").write_text("#!/bin/bash\n")
    check("lint fails when ids and gates are missing", len(ds.lint(root)) > 10)
    ids = " ".join(ds.assertion_ids((root / ds.CONTRACT).read_text()))
    (root / ds.TRUSTED / "run.sh").write_text("#!/bin/bash\n# gates: %s controls\n# ids: %s\n" % (" ".join(ds.GATES), ids))
    check("freeze", ds.main(["freeze", "--root", str(root), "--approved-by", "dry-run"]) == 0)
    check("verify intact", ds.main(["verify", "--root", str(root)]) == 0)
    (root / ds.TRUSTED / "README.md").write_text("changed\n")
    check("verify detects an edit", ds.main(["verify", "--root", str(root)]) == 1)
    (root / ds.TRUSTED / "README.md").write_text("stub thresholds\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "freeze")

    # the REAL policy (sandboxed profiles) must be accepted by verify.py through the wrapper with a FAKE docker
    bin_ = tmp / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text("#!%s\nimport sys\na=sys.argv[1:]\nif a[:2]==['image','inspect']: print('sha256:'+'ab'*32)\nsys.exit(0)\n" % sys.executable)
    (bin_ / "docker").chmod(0o755)
    real = ds.build_policy(root)
    check("real policy has all gates", sorted(real["profiles"]) == sorted(ds.GATES))
    check("real policy: final gate, flagged review", real["final_gate"] == "final" and real["review"]["per_task"] == "flagged")
    from loop.runner import create_worktree  # noqa: E402
    from loop.verify import PASS, verify  # noqa: E402
    wt = tmp / "wt"
    create_worktree(root, wt, git(root, "rev-parse", "HEAD"))
    os.environ["PATH"] = "%s%s%s" % (bin_, os.pathsep, os.environ["PATH"])
    prof = real["profiles"]["m1"]
    ev = tmp / "ev"
    ev.mkdir()
    res = verify(str(wt), git(root, "rev-parse", "HEAD"), ["platform"], real["protected"], prof["test_argv"], str(ev), timeout_s=60)
    check("sandbox wrapper argv accepted by verify.py (fake docker)", res.get("decision") == PASS, json.dumps(res)[:300])

    # stub gate in place of run.sh, same everything else
    gate = str(REPO / "tests" / "doom_dryrun_gate.py")
    pol = ds.build_policy(root, gate_argv=lambda g: ([sys.executable, gate, g] if g in ("build", "m1", "final") else [sys.executable, "-c", "pass"],
                                                     {gate: ds.sha256_path(gate)}),
                          binary=[sys.executable, str(REPO / "tests" / "fake_doom_cli.py")], mode="mock",
                          budgets=dict(max_attempts=2, agent_timeout_s=60, verify_timeout_s=60, max_runtime_s=600, max_total_runtime_s=1200, verify_retries=1))
    (tmp / "fake").mkdir()
    os.environ["FAKE_DIR"] = str(tmp / "fake")
    store = Store(root)
    try:
        Coordinator(store, pol).run()
    finally:
        store.close()
    report = json.loads(next((root / "artifacts" / "runs").glob("*/report.json")).read_text())
    calls = [json.loads(x) for x in (tmp / "fake" / "calls.log").read_text().splitlines()]
    seq = [(c["agent"], c["task"]) for c in calls]
    check("status complete", report["status"] == "complete", report["status"] + " " + report.get("reason", ""))
    check("three coder tasks, one plan", [t for a, t in seq if a == "coder"] == ["D1", "D2", "D3"] and seq.count(("planner", "PLAN-1")) == 1)
    check("flagged review only for tasks touching build.sh / the hooks file, plus the final review",
          [t for a, t in seq if a in ("reviewer", "tester")] == ["D1.review", "D1.test", "D2.review", "D2.test", "FINAL.review"], str(seq))
    check("third_party/assets/loop untouched", git(root, "diff", "--stat", "HEAD", "--", "third_party", "assets", "loop") == "")
    print("\n%s" % ("DRY RUN OK" if ok else "DRY RUN FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
