"""Step 2: run one coding-agent attempt in a fresh git worktree.

Order: validate base SHA -> create worktree at that SHA -> journal the intent
(with the worktree path) -> launch -> journal `launched` (pid/pgid) -> wait
with a timeout -> kill the whole process group -> classify -> preserve any
failed work -> journal the result.

Known limitations (accepted for now):
- A crash after the agent starts but before `launched` is journaled leaves a
  running agent with no recorded pgid, so recovery cannot see it.
- A crash after the worktree is created but before the intent is journaled
  leaves a clean, unrecorded worktree behind; nothing cleans it up.
- The worktree is for integrity, not security: a bypass-mode agent can still
  touch anything its OS user can. Scope enforcement is the gate's job.
"""
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

DROP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")  # keep the CLI on the subscription login
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class PreservationFailed(RuntimeError):
    """A failed attempt's work could not be saved. The task is left EXECUTING so recover() quarantines it."""


def _binary_argv(binary):
    return shlex.split(binary) if isinstance(binary, str) else list(binary)


def docker_agent_argv(config, prompt, agent=None, binary="docker-agent"):
    """Return build(worktree) -> argv for the standalone Docker Agent CLI:

        docker-agent run --exec CONFIG [-a AGENT] --working-dir WORKTREE PROMPT

    CONFIG must be an absolute path to an existing file (use the main checkout's
    copy) and must not live inside the worktree the agent will edit.
    """
    if prompt.startswith("-"):
        raise ValueError("prompt must not start with '-'")
    config = Path(config)
    if not config.is_absolute() or not config.is_file():
        raise ValueError("config must be an absolute path to an existing file: %s" % config)
    config = config.resolve()

    def build(worktree):
        wt = Path(worktree).resolve()
        if wt == config or wt in config.parents:
            raise ValueError("config must not live inside the worktree: %s" % config)
        argv = _binary_argv(binary) + ["run", "--exec", str(config)]
        if agent:
            argv += ["-a", agent]
        return argv + ["--working-dir", str(wt), prompt]

    return build


def docker_agent_preflight(binary, config):
    """Ask the installed CLI whether it has the flags we use and whether `doctor` passes. -> problems."""
    problems = []
    try:
        h = subprocess.run(_binary_argv(binary) + ["run", "--help"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return ["cannot run %s: %s" % (binary, e)]
    text = h.stdout + h.stderr
    if h.returncode != 0:
        problems.append("`run --help` exited %d" % h.returncode)
    problems += ["`run --help` does not mention %s" % f for f in ("--exec", "--agent", "--working-dir") if f not in text]
    try:
        d = subprocess.run(_binary_argv(binary) + ["doctor", str(config)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return problems + ["cannot run doctor: %s" % e]
    if d.returncode != 0:
        problems.append("`doctor %s` exited %d: %s" % (config, d.returncode, (d.stdout + d.stderr).strip()[-300:]))
    return problems


def _git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout.strip()


def create_worktree(repo, path, base_sha):
    if not isinstance(base_sha, str) or not SHA_RE.match(base_sha):
        raise ValueError("base_sha must be a full lowercase commit SHA, got %r" % (base_sha,))
    try:
        resolved = _git(repo, "rev-parse", "--verify", base_sha + "^{commit}")
    except RuntimeError as e:
        raise ValueError("unknown commit %s" % base_sha) from e
    if resolved != base_sha:
        raise ValueError("%s is not a commit SHA" % base_sha)
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "--detach", str(path), base_sha)
    if _git(path, "rev-parse", "HEAD") != base_sha or _git(path, "status", "--porcelain"):
        raise RuntimeError("new worktree is not clean at %s" % base_sha)


def _kill_group(proc, pgid, grace_s):
    try:
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=grace_s)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt("signal %d" % signum)


def run_attempt(store, task_id, base_sha, argv, timeout_s, repo=None, worktrees_root=None,
                env=None, grace_s=5.0):
    """Run an agent command once for `task_id` in a new worktree. Returns a result dict.

    `argv` is a list, or a callable taking the new worktree path and returning
    the list (see docker_agent_argv).

    Raises (leaving the task EXECUTING for recover()) if interrupted or if a
    failed attempt's work could not be preserved.
    """
    repo = Path(repo) if repo else store.root
    task = store.state["tasks"][task_id]
    if task["status"] != "PENDING":
        raise ValueError("%s is %s, not PENDING" % (task_id, task["status"]))
    n = task["attempt_seq"] + 1
    run_id = store.state["run_id"]
    root = Path(worktrees_root) if worktrees_root else repo / "build" / "worktrees"
    wt = root / run_id / ("%s-a%d-%s" % (task_id, n, uuid.uuid4().hex[:6]))
    create_worktree(repo, wt, base_sha)
    argv = list(argv(wt) if callable(argv) else argv)
    store.begin_attempt(task_id, worktree=wt, base_sha=base_sha)  # path + intent recorded before launch

    log = store.root / "artifacts" / "runs" / run_id / "logs" / ("%s-a%d.log" % (task_id, n))
    log.parent.mkdir(parents=True, exist_ok=True)
    child_env = {k: v for k, v in (os.environ if env is None else env).items() if k not in DROP_ENV}
    start = time.monotonic()
    old_handler = None
    if threading.current_thread() is threading.main_thread():
        old_handler = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        with open(log, "wb") as lf:
            try:
                proc = subprocess.Popen(argv, cwd=str(wt), env=child_env, stdin=subprocess.DEVNULL,
                                        stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:
                lf.write(("launch error: %s\n" % e).encode())
                outcome, classification, rc = "failed", "launch_error", None
            else:
                pgid = proc.pid  # start_new_session makes the agent its own group leader
                try:
                    store.note_launched(task_id, proc.pid, pgid, argv)
                    try:
                        rc = proc.wait(timeout=timeout_s)
                    except subprocess.TimeoutExpired:
                        _kill_group(proc, pgid, grace_s)
                        rc, outcome, classification = proc.returncode, "timeout", "timeout"
                    else:
                        try:
                            os.killpg(pgid, signal.SIGKILL)  # leftovers of a finished agent
                        except ProcessLookupError:
                            pass
                        if rc == 0:
                            outcome, classification = "completed", "exit_0"
                        elif rc < 0:
                            outcome, classification = "failed", "killed_by_signal_%d" % -rc
                        else:
                            outcome, classification = "failed", "nonzero_exit"
                except BaseException:
                    _kill_group(proc, pgid, grace_s)
                    raise
    finally:
        if old_handler is not None:
            signal.signal(signal.SIGTERM, old_handler)

    extra = {}
    if outcome != "completed":
        pm = store.preserve_attempt(task_id)
        if pm["status"] != "preserved":
            raise PreservationFailed("%s: %s" % (task_id, pm.get("error") or pm["status"]))
        extra = dict(preserved_dir=pm["dir"], snapshot_commit=pm["snapshot_commit"])
    duration = round(time.monotonic() - start, 3)
    store.record_result(task_id, outcome, classification=classification, exit_code=rc, duration_s=duration,
                        log=str(log.relative_to(store.root)), **extra)
    return dict(task_id=task_id, attempt=n, outcome=outcome, classification=classification, exit_code=rc,
                duration_s=duration, worktree=str(wt), log=str(log), **extra)
