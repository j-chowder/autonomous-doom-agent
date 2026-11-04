"""RED BASELINE (needs Docker + the built toolchain image). Run the trusted DOOM tests against the committed HEAD, which has no port.

  python3 tests/demo_doom_baseline.py [--gates build,m1]

Expected, and checked here: the sandbox container ran fine (no infrastructure ERROR), `emcc` reports 4.0.18 (BUILD-01.b PASS),
and BUILD-01.a FAILS because build.sh is missing. Every assertion that needs a build is not PASS. Exit code 0 only if all of that holds.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print("%s  %s %s" % ("PASS" if cond else "FAIL", name, detail))


def run_gate(cand, gate, out_root):
    cmd = [sys.executable, str(REPO / "loop" / "sandbox_verify.py"), "run", "--trusted", str(REPO / "tests" / "doom" / "trusted"),
           "--out-root", str(out_root), "--timeout", "900", "--", "bash", "/trusted/run.sh", gate]
    p = subprocess.run(cmd, cwd=cand, capture_output=True, text=True, timeout=1200)
    print(p.stdout[-1500:], p.stderr[-800:])
    m = re.search(r"SANDBOX_EVIDENCE (\S+) image_id=(\S+) raw_exit=(\S+) exit=(\S+)", p.stdout)
    if not m:
        return None, None, p.returncode
    sandbox = json.loads(Path(m.group(1)).read_text())
    res_file = Path(sandbox["out_dir"]) / "results.json"
    return (json.loads(res_file.read_text()) if res_file.exists() else None), sandbox, p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gates", default="build,m1")
    gates = ap.parse_args().gates.split(",")
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    out_root = REPO / "artifacts" / "doom" / "baseline"
    print("candidate = git HEAD %s (no port); trusted tests = tests/doom/trusted (working copy)\n" % sha[:10])
    for gate in gates:
        with tempfile.TemporaryDirectory(prefix="doom-baseline-") as tmp:
            tar = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", sha], capture_output=True, check=True).stdout
            subprocess.run(["tar", "xf", "-", "-C", tmp], input=tar, check=True)
            res, sb, rc = run_gate(tmp, gate, out_root)
        print("--- gate %s ---" % gate)
        check("%s: container ran and returned results.json" % gate, res is not None and sb is not None)
        if res is None:
            continue
        st = {a["id"]: a for a in res["assertions"] if not a["id"].startswith("CONTROL-")}
        check("%s: wrapper exit is FAIL (1), not ERROR (70)" % gate, rc == 1, "exit=%s raw=%s" % (rc, sb.get("raw_exit")))
        a = st.get("BUILD-01.a", {})
        check("%s: BUILD-01.a FAIL because build.sh is missing" % gate, a.get("status") == "FAIL" and "build.sh not found" in a.get("reason", ""), a.get("reason", ""))
        b = st.get("BUILD-01.b", {})
        check("%s: BUILD-01.b PASS (emcc 4.0.18 works in the container)" % gate, b.get("status") == "PASS", b.get("reason", ""))
        late = [i for i, x in st.items() if i not in ("BUILD-01.a", "BUILD-01.b") and x["status"] == "PASS"]
        check("%s: nothing downstream of the build passes" % gate, not late, str(late))
        # An ERROR is expected only if the missing build explains it: "not run: BUILD-01.a FAIL" (main.js), or
        # "skipped: <ID> failed|did not pass" whose blocker is BUILD-01.a or itself an expected ERROR. Anything else is infrastructure.
        expected, grew = set(), True
        while grew:
            grew = False
            for i, x in st.items():
                if x["status"] != "ERROR" or i in expected:
                    continue
                r = x.get("reason", "")
                m = re.match(r"skipped: ([A-Z]+-\d\d\.[a-z]) (?:failed|did not pass)", r)
                if re.fullmatch(r"not run: BUILD-01\.a FAIL", r) or (m and (m.group(1) == "BUILD-01.a" or m.group(1) in expected)):
                    expected.add(i)
                    grew = True
        infra = {i: x.get("reason", "") for i, x in st.items() if x["status"] == "ERROR" and i not in expected}
        check("%s: every ERROR is explained by the missing build (%d blocked)" % (gate, len(expected)), not infra, str(infra))
    print("\n%s" % ("RED BASELINE OK" if ok else "RED BASELINE NOT OK: read the FAIL lines above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
