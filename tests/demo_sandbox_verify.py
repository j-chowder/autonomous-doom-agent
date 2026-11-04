"""Tier-1 sandbox demo (needs Docker). Runs the REAL toolchain container through verify.py on the trivial fixture.

  python3 tests/demo_sandbox_verify.py --build   build the root Dockerfile, record the image ID, then run the scenarios
  python3 tests/demo_sandbox_verify.py           run the scenarios against the already-built image

Scenarios (each prints the verify.py decision and the sandbox evidence path):
  1 toolchain_smoke.sh   expect PASS  (emcc 4.0.18, wasm validity, Chromium, read-only mounts, no network, no credentials)
  2 `sh -c "exit 1"`     expect FAIL  (a real test failure stays a FAIL)
  3 missing image tag    expect ERROR (an unusable sandbox is never a FAIL)
Not the agent sandbox: Docker Agent + Claude Code still run on the host in a worktree (loop/sandbox.md).
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from loop.runner import create_worktree  # noqa: E402
from loop.verify import verify  # noqa: E402
from step4_fixture import make_repo  # noqa: E402

WRAP = str(REPO / "loop" / "sandbox_verify.py")
TRUSTED = str(REPO / "tests" / "sandbox_smoke")
OUT_ROOT = REPO / "artifacts" / "sandbox-runs"


def scenario(title, expect, extra, cmd, tmp, main, base, n):
    wt = Path(tmp) / ("wt%d" % n)
    create_worktree(main, wt, base)
    argv = [sys.executable, WRAP, "run", "--trusted", TRUSTED, "--out-root", str(OUT_ROOT), "--timeout", "600"] + extra + ["--"] + cmd
    rec = verify(wt, base, ["src"], ["tests_trusted"], argv, Path(tmp) / ("ev%d" % n), timeout_s=900)
    print("\n=== %s ===\n   expected %s, got %s (%s)" % (title, expect, rec["decision"], rec["classification"]))
    print("   verify log:", rec["log"])
    for line in Path(rec["log"]).read_text().splitlines()[-12:]:
        print("   |", line)
    return rec["decision"] == expect


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    if ap.parse_args().build:
        rc = subprocess.run([sys.executable, WRAP, "build"]).returncode
        if rc:
            sys.exit("build failed (exit %d)" % rc)
    tmp = tempfile.mkdtemp(prefix="loop-sandbox-")
    main, base, _ = make_repo(tmp)
    ok = [
        scenario("1 toolchain + isolation smoke", "PASS", [], ["bash", "/trusted/toolchain_smoke.sh"], tmp, main, base, 1),
        scenario("2 real test failure", "FAIL", [], ["sh", "-c", "exit 1"], tmp, main, base, 2),
        scenario("3 missing image", "ERROR", ["--image", "no-such-image-xyz"], ["true"], tmp, main, base, 3),
    ]
    img = REPO / "artifacts" / "sandbox" / "toolchain-image.json"
    print("\nimage record:", json.loads(img.read_text()) if img.exists() else "(none; run with --build)")
    print("ALL AS EXPECTED" if all(ok) else "UNEXPECTED RESULT")
    sys.exit(0 if all(ok) else 1)
