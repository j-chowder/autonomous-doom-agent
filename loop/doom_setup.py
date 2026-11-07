#!/usr/bin/env python3
"""DOOM acceptance setup (controller-owned; no agent logic).

  lint     check QA's tests against tests/doom/ACCEPTANCE.md (used as the QA-authoring task's verification command)
  freeze   lint, then record sha256 of the contract and every trusted file in tests/doom/frozen.json (needs --approved-by)
  verify   recompute the hashes and compare with frozen.json
  qa-config  write the controller config for the QA authoring task
  policy   write the coordinator policy for the real DOOM run (profiles = cumulative gates, run in the sandbox container)
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from loop.controller import sha256_path  # noqa: E402  (the controller's own file hash; test_files must use it)
CONTRACT, TRUSTED, FROZEN = "tests/doom/ACCEPTANCE.md", "tests/doom/trusted", "tests/doom/frozen.json"
GATES = ["build", "boot", "m1", "video", "time", "input", "audio", "final"]  # cumulative: each includes the one before
GATE_TEXT = {
    "build": "BUILD-01: clean build.sh -> dist/doom, valid wasm, hook exports",
    "boot": "build + LOAD-01 (engine starts, canvas not blank) + WAD-01 (E1M1 player start)",
    "m1": "boot + STABILITY-01 (short) + DEPLOY-01 (static /doom/ subpath, wasm MIME). MILESTONE 1",
    "video": "m1 + VIDEO-01 (opaque alpha, WAD-derived colors, frames change)",
    "time": "video + TIME-01 (35 tics/s +-25%, 10 s window) + TIME-02 (heartbeat, watchdog)",
    "input": "time + INPUT-01/02/03 (move, turn, fire, run, key release, no page side effects)",
    "audio": "input + AUDIO-01 (audio context running, non-silent output after firing)",
    "final": "audio + 60 s timing window + 60 s soak + all negative controls. Completion gate",
}
IN_CONTAINER_S = {"build": 600, "boot": 600, "m1": 900, "video": 1000, "time": 1200, "input": 1500, "audio": 1500, "final": 3000}
OBJECTIVE = ("Port the supplied doomgeneric engine to WebAssembly so DOOM Episode 1 runs in a browser from a static website, using the supplied "
             "shareware WAD. READ tests/doom/ACCEPTANCE.md FIRST: it defines the build and serve interface (build.sh -> dist/doom/), the six "
             "read-only test hooks (platform/dg_test_hooks.c), the criteria and the cumulative verification gates. Order the plan by gate: "
             "build, boot, m1, video, time, input, audio, final. A task's profile is the gate that proves it; later gates include earlier ones. "
             "Milestone 1 is gate m1. There are 8 gates but at most 6 tasks per plan: COMBINE adjacent gate stages into one task (for example "
             "build+boot, or time+input) and give the task the HIGHEST gate it covers as its profile. Keep tasks in gate order; never skip ahead. "
             "The controller runs gate final itself after the last task, so no task needs profile final; use it only for a task that repairs "
             "something only that gate finds.")
CONSTRAINTS = ("Never change third_party, assets, loop, tests or artifacts. Do not add test-only exports beyond the six DG_TEST_ getters. "
               "Keep the browser runtime separate from any agent tooling. build.sh must work with no network inside the Emscripten 4.0.18 image.")
ALLOWED = ["platform", "web", "build.sh", "build", "dist"]
PROTECTED = ["third_party", "assets", "loop", "tests", "artifacts", "AGENTS.md", "SANDBOX.md", "README.md", "Dockerfile", ".gitignore"]
ROLES = {"wasm_platform": {"agent": "coder", "allowed_roots": ["platform", "build.sh", "build", "dist"]},
         "web_frontend": {"agent": "coder", "allowed_roots": ["web", "build.sh", "dist"]},
         "integrator": {"agent": "coder", "allowed_roots": ALLOWED}}  # for tasks that must change both the C platform layer and the page
QA_PROMPT = """You are the independent QA author for the DOOM WebAssembly port. No coder has written any port code yet and you must not wait for one.
Read tests/doom/ACCEPTANCE.md completely. It is the contract. Write the trusted acceptance tests under tests/doom/trusted/ and nowhere else.

Deliver:
1. tests/doom/trusted/run.sh GATE -- the runner described in section 4 of the contract (gates build, boot, m1, video, time, input, audio, final, controls).
2. Node + Playwright test code (require('playwright'); the image provides it through NODE_PATH) for every assertion ID in section 5, with the exact IDs in the output lines.
3. The negative controls of section 6 as black-box fault injection (addInitScript, request interception, a disposable copy of dist/doom). Each control states the assertion it must break.
4. tests/doom/trusted/README.md: for every threshold you derived (movement distance, run ratio, pixel tolerance, silence level, E1M1 open position), the value and its source (engine file and line, WAD lump, or baseline procedure). Do not invent values silently. Use the frozen thresholds in section 1 exactly.

Rules:
- Write from the contract and the engine source in third_party/doomgeneric and assets/doom1.wad only. Do not guess at how a coder will structure code.
- You cannot run Chromium or the build here. Check only with `bash -n` and `node --check`. Keep the tests deterministic and bounded: every wait has a timeout.
- Use no network. Load the WAD from the candidate copy or from /workspace/assets/doom1.wad.
- A test that cannot set up its precondition prints ERROR, never PASS. A skipped assertion prints ERROR with the blocking ID.
- Do not edit tests/doom/ACCEPTANCE.md or anything outside tests/doom/trusted/.
The controller accepts your work only when `python3 loop/doom_setup.py lint` passes. Its messages come back to you if it fails.
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def assertion_ids(contract_text):
    ids = []
    for m in re.finditer(r"^\| ([A-Z]+-\d\d)\.([a-f])(?: to \.([a-f]))? \|", contract_text, re.M):
        last = m.group(3) or m.group(2)
        ids += ["%s.%s" % (m.group(1), chr(c)) for c in range(ord(m.group(2)), ord(last) + 1)]
    return ids


def trusted_files(root):
    d = Path(root) / TRUSTED
    return sorted(p for p in d.rglob("*") if p.is_file()) if d.is_dir() else []


def lint(root):
    root = Path(root)
    problems = []
    contract = root / CONTRACT
    if not contract.is_file():
        return ["missing %s" % CONTRACT]
    files = trusted_files(root)
    if not files:
        return ["no files under %s" % TRUSTED]
    run = root / TRUSTED / "run.sh"
    if not run.is_file():
        problems.append("missing %s/run.sh" % TRUSTED)
    text = "\n".join(p.read_text(errors="replace") for p in files)
    ids = assertion_ids(contract.read_text())
    if len(ids) < 30:
        problems.append("contract parse found only %d assertion ids" % len(ids))
    problems += ["assertion id %s does not appear in the tests" % i for i in ids if i not in text]
    if run.is_file():
        rtext = run.read_text()
        problems += ["run.sh does not handle gate %r" % g for g in GATES + ["controls"] if not re.search(r"\b%s\b" % g, rtext)]
    if not (root / TRUSTED / "README.md").is_file():
        problems.append("missing %s/README.md (derived thresholds and their sources)" % TRUSTED)
    for p in files:
        if p.stat().st_size > 400_000:
            problems.append("%s is larger than 400 KB" % p.relative_to(root))
        rel = str(p.relative_to(root))
        if p.suffix == ".sh":
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            if r.returncode:
                problems.append("bash -n %s: %s" % (rel, r.stderr.strip()[:300]))
        elif p.suffix in (".js", ".mjs", ".cjs") and shutil.which("node"):
            r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
            if r.returncode:
                problems.append("node --check %s: %s" % (rel, r.stderr.strip()[:300]))
    return problems


def snapshot(root):
    root = Path(root)
    files = {str(p.relative_to(root)): sha256(p) for p in trusted_files(root)}
    files[CONTRACT] = sha256(root / CONTRACT)
    tree = hashlib.sha256("\n".join("%s %s" % (k, files[k]) for k in sorted(files)).encode()).hexdigest()
    return files, tree


def cmd_lint(a):
    problems = lint(a.root)
    for p in problems:
        print("LINT: " + p)
    print("lint: %s (%d files)" % ("FAIL" if problems else "ok", len(trusted_files(a.root))))
    return 1 if problems else 0


def cmd_freeze(a):
    problems = lint(a.root)
    if problems:
        print("\n".join("LINT: " + p for p in problems))
        return 1
    files, tree = snapshot(a.root)
    rec = dict(approved_by=a.approved_by, approved_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), tree_sha256=tree, files=files)
    (Path(a.root) / FROZEN).write_text(json.dumps(rec, indent=1) + "\n")
    print("frozen %d files, tree %s" % (len(files), tree[:16]))
    return 0


def cmd_verify(a):
    rec = json.loads((Path(a.root) / FROZEN).read_text())
    files, tree = snapshot(a.root)
    if files != rec["files"]:
        bad = sorted(set(files) ^ set(rec["files"]) | {k for k in files if k in rec["files"] and files[k] != rec["files"][k]})
        print("MODIFIED/ADDED/REMOVED: " + ", ".join(bad))
        return 1
    print("frozen tests intact, tree %s" % tree[:16])
    return 0


def git_head(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def cmd_qa_config(a):
    root = Path(a.root).resolve()
    me = str(REPO / "loop" / "doom_setup.py")
    cfg = {"base_sha": git_head(root),
           "agent": {"config": str(REPO / "loop" / "agents.yaml"), "name": "tester", "binary": "docker-agent"},
           "budgets": dict(max_attempts=3, max_runtime_s=5400, agent_timeout_s=1800, verify_timeout_s=300, verify_retries=1),
           "tasks": {"QA-AUTHOR": {"title": "Author trusted DOOM acceptance tests", "role": "tester", "agent": "tester", "prompt": QA_PROMPT,
                                   "allowed": [TRUSTED], "protected": [x for x in PROTECTED if x != "tests"] + [CONTRACT, FROZEN],
                                   "test_argv": [sys.executable, me, "lint"], "test_files": {me: sha256_path(me), str(root / CONTRACT): sha256_path(root / CONTRACT)}}}}
    out = Path(a.out or root / "artifacts" / "doom" / "qa-author.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, indent=1) + "\n")
    print("wrote %s (base %s)" % (out, cfg["base_sha"][:10]))
    return 0


def build_policy(root, gate_argv=None, binary="docker-agent", agent_config=None, mode="real", base_sha=None, budgets=None):
    """gate_argv(gate) -> (argv, extra test_files). Default: the sandbox container running tests/doom/trusted/run.sh."""
    root = Path(root).resolve()
    rec = json.loads((root / FROZEN).read_text())
    files, tree = snapshot(root)
    if files != rec["files"]:
        raise SystemExit("frozen tests do not match %s; refusing to build a policy" % FROZEN)
    wrapper = str(REPO / "loop" / "sandbox_verify.py")
    held = {str(root / k): sha256_path(root / k) for k in rec["files"]}  # frozen.json holds plain sha256; the controller needs sha256_path

    def sandboxed(gate):
        return ([sys.executable, wrapper, "run", "--trusted", str(root / TRUSTED), "--timeout", str(IN_CONTAINER_S[gate]), "--", "bash", "/trusted/run.sh", gate],
                {wrapper: sha256_path(wrapper)})

    profiles = {}
    for g in GATES:
        argv, extra = (gate_argv or sandboxed)(g)
        profiles[g] = dict(description=GATE_TEXT[g], test_argv=argv, test_files={**held, **extra})
    b = dict(max_attempts=3, max_runtime_s=5400, agent_timeout_s=1800, verify_timeout_s=3300, verify_retries=2,
             max_replans=1, max_tasks=6, max_invocations=40, max_total_runtime_s=21600)
    b.update(budgets or {})
    return {"objective": OBJECTIVE, "constraints": CONSTRAINTS, "base_sha": base_sha or git_head(root), "mode": mode,
            "agent": {"config": agent_config or str(REPO / "loop" / "agents.yaml"), "binary": binary},
            "allowed_roots": ALLOWED, "protected": PROTECTED, "coder_roles": ROLES, "profiles": profiles, "final_gate": "final",
            "review": {"per_task": "flagged", "flagged_paths": ["platform/dg_test_hooks.c", "build.sh"], "final": True, "max_fixes": 1},
            "budgets": b}


def cmd_policy(a):
    pol = build_policy(a.root)
    out = Path(a.out or Path(a.root) / "artifacts" / "doom" / "policy.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pol, indent=1) + "\n")
    print("wrote %s (base %s, gates %s)" % (out, pol["base_sha"][:10], ",".join(GATES)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("lint", cmd_lint), ("freeze", cmd_freeze), ("verify", cmd_verify), ("qa-config", cmd_qa_config), ("policy", cmd_policy)):
        p = sub.add_parser(name)
        p.add_argument("--root", default=".", help="repository root (default: current directory)")
        if name in ("qa-config", "policy"):
            p.add_argument("--out")
        if name == "freeze":
            p.add_argument("--approved-by", required=True)
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
