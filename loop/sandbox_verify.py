"""Tier-1 sandbox: run ONE controller-owned test command inside the course toolchain container.

Use it as the `test_argv` of a task (the controller/verify.py code is unchanged):

    ["python3", "/abs/loop/sandbox_verify.py", "run", "--trusted", "/abs/tests/sandbox_smoke",
     "--timeout", "240", "--", "bash", "/trusted/toolchain_smoke.sh"]

verify.py exports the candidate fingerprint to a scratch directory and starts this
script with cwd = that directory. This script mounts:

    cwd (the candidate copy)   -> /workspace   read-only
    --trusted DIR              -> /trusted     read-only   (controller-owned tests)
    <out-root>/<run>/          -> /out         read-write  (evidence; under artifacts/)

and runs CMD there with `--network none`, all capabilities dropped (except
DAC_OVERRIDE, so root can read the 0700 scratch copy), no host environment, no
host credentials and nothing from $HOME. Nothing here is a credential path: the
agent itself is NOT run in this container (see loop/sandbox.md).

Exit codes are translated so verify.py classifies them correctly:
    0 -> 0 (PASS)   other nonzero from the test -> unchanged (FAIL)
    docker failed (125), in-container timeout (124), killed/OOM (137), missing
    docker or image, bad arguments -> 70 (ERROR: no valid result, never FAIL)

`build` builds the supplied root Dockerfile and records the image ID.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ERROR_EXIT = 70  # must equal loop.verify.ERROR_EXIT (tests/test_sandbox_verify.py checks it)
DEFAULT_IMAGE = "doom-capstone-toolchain"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _docker(*args, timeout=60):
    return subprocess.run(["docker"] + list(args), capture_output=True, text=True, timeout=timeout)


def image_id(image):
    """Content ID of a local image, or None if it does not exist / docker is unusable."""
    try:
        p = _docker("image", "inspect", "--format", "{{.Id}}", image)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = p.stdout.strip()
    return out if p.returncode == 0 and out else None


def map_exit(rc):
    """Container/docker exit code -> an exit code verify.py classifies correctly."""
    if rc is None or rc < 0 or rc in (124, 125, 137):
        return ERROR_EXIT
    return rc


def docker_argv(name, iid, candidate, trusted, out, timeout_s, memory, pids, cmd):
    """The full `docker run` command. Pure function: easy to test and to read."""
    def mount(src, dst, ro):
        return "type=bind,source=%s,target=%s%s" % (src, dst, ",readonly" if ro else "")
    return [
        "docker", "run", "--rm", "--init", "--name", name,
        "--network", "none",
        "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(pids), "--memory", memory, "--shm-size", "1g",
        "--mount", mount(candidate, "/workspace", True),
        "--mount", mount(trusted, "/trusted", True),
        "--mount", mount(out, "/out", False),
        "-w", "/workspace",
        iid,                                   # run by image ID, not tag: exactly what was inspected
        "timeout", "-k", "5", str(timeout_s),  # in-container limit; verify.py's own timeout is the outer one
    ] + list(cmd)


def _fail(msg):
    print("sandbox_verify: %s" % msg, file=sys.stderr, flush=True)
    return ERROR_EXIT


def cmd_run(a):
    cmd = list(a.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        return _fail("no command given after --")
    if a.timeout < 1:
        return _fail("--timeout must be >= 1")
    candidate = os.path.realpath(os.getcwd())
    trusted = os.path.realpath(a.trusted)
    out_root = os.path.realpath(a.out_root)
    for p in (candidate, trusted, out_root):
        if "," in p or "\n" in p:
            return _fail("path not usable in a docker --mount: %r" % p)
    if not os.path.isdir(trusted):
        return _fail("--trusted is not a directory: %s" % trusted)
    if shutil.which("docker") is None:
        return _fail("docker not found on PATH")
    iid = image_id(a.image)
    if iid is None:
        return _fail("image %r not found or docker not running (build it: sandbox_verify.py build)" % a.image)

    run_id = "%s-%s" % (_now(), uuid.uuid4().hex[:8])
    out = Path(out_root) / run_id
    out.mkdir(parents=True)
    os.chmod(str(out), 0o777)  # container root has no host uid; keep /out writable on any host
    name = "loop-verify-" + uuid.uuid4().hex[:12]
    argv = docker_argv(name, iid, candidate, trusted, str(out), a.timeout, a.memory, a.pids, cmd)

    state = {"proc": None}

    def stop(signum, frame):
        try:
            _docker("kill", name, timeout=15)  # killing the docker client alone leaves the container running
        except (OSError, subprocess.TimeoutExpired):
            pass
        proc = state["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
        sys.exit(ERROR_EXIT)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    start = time.monotonic()
    started_at = _now()
    try:
        state["proc"] = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
        raw = state["proc"].wait()
    except OSError as e:
        return _fail("could not start docker: %s" % e)
    mapped = map_exit(raw)

    try:
        client = _docker("--version", timeout=15).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        client = None
    rec = dict(image=a.image, image_id=iid, container=name, started_at=started_at,
               duration_s=round(time.monotonic() - start, 3), docker_client=client,
               network="none", candidate_mount="read-only", trusted_mount="read-only",
               trusted_dir=trusted, out_dir=str(out), command=cmd, in_container_timeout_s=a.timeout,
               memory=a.memory, pids_limit=a.pids, raw_exit=raw, exit=mapped, docker_argv=argv)
    (out / "sandbox.json").write_text(json.dumps(rec, indent=2) + "\n")
    print("SANDBOX_EVIDENCE %s image_id=%s raw_exit=%s exit=%s" % (out / "sandbox.json", iid, raw, mapped), flush=True)
    return mapped


def cmd_build(a):
    """Build the supplied root Dockerfile and record the image ID. Build-time network is allowed (and needed)."""
    dockerfile = REPO / "Dockerfile"
    if shutil.which("docker") is None:
        return _fail("docker not found on PATH")
    rc = subprocess.run(["docker", "build", "-t", a.image, str(REPO)]).returncode
    if rc != 0:
        return rc
    iid = image_id(a.image)
    if iid is None:
        return _fail("built image %r cannot be inspected" % a.image)
    try:
        emcc = _docker("run", "--rm", "--network", "none", iid, "emcc", "--version", timeout=120).stdout.strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        emcc = []
    rec = dict(tag=a.image, image_id=iid, built_at=_now(),
               dockerfile_sha256=hashlib.sha256(dockerfile.read_bytes()).hexdigest(),
               emcc_version=emcc[0] if emcc else None,
               docker_client=_docker("--version").stdout.strip(),
               note="build used the network (base image, apt, npm, Chromium); runtime does not")
    d = Path(a.record_dir)
    d.mkdir(parents=True, exist_ok=True)
    text = json.dumps(rec, indent=2) + "\n"
    (d / ("toolchain-image-%s.json" % rec["built_at"])).write_text(text)  # history is preserved
    (d / "toolchain-image.json").write_text(text)                          # latest
    print(text, end="")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    r = sub.add_parser("run", help="run a command in the toolchain container (cwd = candidate)")
    r.add_argument("--trusted", required=True, help="controller-owned test directory, mounted read-only at /trusted")
    r.add_argument("--out-root", default=str(REPO / "artifacts" / "sandbox-runs"))
    r.add_argument("--image", default=DEFAULT_IMAGE)
    r.add_argument("--timeout", type=int, default=240, help="in-container seconds; keep below the outer verify timeout")
    r.add_argument("--memory", default="4g")
    r.add_argument("--pids", type=int, default=1024)
    r.add_argument("cmd", nargs=argparse.REMAINDER)
    r.set_defaults(fn=cmd_run)
    b = sub.add_parser("build", help="docker build the root Dockerfile and record the image ID")
    b.add_argument("--image", default=DEFAULT_IMAGE)
    b.add_argument("--record-dir", default=str(REPO / "artifacts" / "sandbox"))
    b.set_defaults(fn=cmd_build)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
