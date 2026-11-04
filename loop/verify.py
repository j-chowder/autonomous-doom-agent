"""Step 3: scope check + independent verification of one attempt's worktree.

Flow: fingerprint the worktree -> scope check against the base SHA -> export that
exact fingerprint to a scratch directory -> run the controller-owned test command
there -> fingerprint the worktree again -> decide.

PASS only if the scope check and the tests succeed AND the worktree fingerprint
did not change while the tests ran. The agent's own claims are never consulted.

Decisions (criteria doc, section 4):
  PASS   scope clean, tests exited 0, source unchanged during the run.
  FAIL   scope violation, or the tests ran and exited nonzero.
  ERROR  no valid result: test command missing/timed out/killed, the tests exited
         ERROR_EXIT, the source changed during the run, or git failed.

Design notes (accepted for now):
- The fingerprint is a git tree hash of the worktree: base-tracked files plus
  untracked files, minus anything .gitignore'd. Ignored files are neither
  fingerprinted nor exported, so they cannot influence the tests, but they are
  still scope-checked (status "I") so they cannot hide a write to a protected
  or out-of-scope path. An edit to .gitignore is itself a tracked change.
- Tests run in an exported copy, not in the worktree, so they cannot dirty it.
  Put the trusted tests OUTSIDE the worktree and pass their absolute path in
  test_argv. The command runs with cwd = the exported copy.
- Not a sandbox: the test command runs as your OS user.
- Nothing is written to the Store here; Step 4 decides how to journal a result.
  The evidence record (verify.json) carries the tree hash, not a commit SHA.
"""
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from loop.runner import DROP_ENV, SHA_RE, _kill_group

PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"
ERROR_EXIT = 70  # a trusted test exits 70 to say "precondition failed, no valid result"


class GitError(RuntimeError):
    pass


def _git(cwd, *args, env=None):
    full_env = dict(os.environ)
    full_env.update(env or {})
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, env=full_env)
    if p.returncode != 0:
        raise GitError("git %s: %s" % (" ".join(args), p.stderr.decode(errors="replace").strip()))
    return p.stdout


def snapshot_tree(worktree, base_sha):
    """Tree hash of everything in the worktree (tracked, untracked, deletions), ignoring .gitignore'd files."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        _git(worktree, "read-tree", base_sha, env=env)
        _git(worktree, "add", "-A", env=env)
        return _git(worktree, "write-tree", env=env).decode().strip()


def changed_files(worktree, base_sha, tree):
    """[(status, path)] between base_sha and tree. Status: A added, M modified, D deleted, T type change."""
    out = _git(worktree, "diff-tree", "-r", "--no-renames", "--name-status", "-z", base_sha, tree)
    parts = out.decode(errors="replace").split("\0")
    return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def ignored_files(worktree):
    """Untracked files hidden by .gitignore. They never reach the fingerprint, so scope-check them separately."""
    out = _git(worktree, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    return [p for p in out.decode(errors="replace").split("\0") if p]


def _matches(path, prefixes):
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def scope_violations(changed, allowed, protected):
    """Protected prefixes always lose; anything not under an allowed prefix is out of scope.

    `changed` is [(status, path)]; ignored files are passed in as status "I".
    """
    bad = []
    for status, path in changed:
        if _matches(path, protected):
            bad.append(dict(status=status, path=path, reason="protected"))
        elif not _matches(path, allowed):
            bad.append(dict(status=status, path=path, reason="outside_allowed"))
    return bad


def _export(worktree, tree, dest):
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        _git(worktree, "read-tree", tree, env=env)
        _git(worktree, "checkout-index", "-a", "-f", "--prefix=%s/" % dest, env=env)


def _run_tests(argv, cwd, log, timeout_s, grace_s=5.0):
    """-> (exit_code, problem). problem is None, 'launch_error' or 'timeout'."""
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    with open(log, "wb") as lf:
        try:
            proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                    stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            lf.write(("launch error: %s\n" % e).encode())
            return None, "launch_error"
        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(proc, proc.pid, grace_s)
            return None, "timeout"
        except BaseException:
            _kill_group(proc, proc.pid, grace_s)
            raise
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # leftovers of a finished test run
        except ProcessLookupError:
            pass
        return rc, None


def verify(worktree, base_sha, allowed, protected, test_argv, evidence_dir, timeout_s=300):
    """Verify one attempt. Returns the evidence record (also written to evidence_dir/verify.json)."""
    worktree, ev = Path(worktree), Path(evidence_dir)
    ev.mkdir(parents=True, exist_ok=True)
    log = ev / "verify.log"
    rec = dict(decision=ERROR, classification=None, base_sha=base_sha, worktree=str(worktree),
               test_argv=list(test_argv), allowed=list(allowed), protected=list(protected),
               fingerprint=None, fingerprint_after=None, changed=[], violations=[],
               exit_code=None, log=str(log), duration_s=None)
    start = time.monotonic()

    def done(decision, classification, **extra):
        rec.update(decision=decision, classification=classification, duration_s=round(time.monotonic() - start, 3), **extra)
        (ev / "verify.json").write_text(json.dumps(rec, indent=2) + "\n")
        return rec

    try:
        if not isinstance(base_sha, str) or not SHA_RE.match(base_sha):
            return done(ERROR, "bad_base_sha")
        fp = rec["fingerprint"] = snapshot_tree(worktree, base_sha)
        changed = changed_files(worktree, base_sha, fp)
        rec["changed"] = [dict(status=s, path=p) for s, p in changed]
        bad = scope_violations(changed + [("I", p) for p in ignored_files(worktree)], allowed, protected)
        if bad:
            return done(FAIL, "scope_violation", violations=bad)
        with tempfile.TemporaryDirectory() as src:
            _export(worktree, fp, src)
            rc, problem = _run_tests(list(test_argv), src, log, timeout_s)
        rec["exit_code"] = rc
        rec["fingerprint_after"] = after = snapshot_tree(worktree, base_sha)
    except (GitError, OSError) as e:
        return done(ERROR, "infra_error", error=str(e))

    if after != fp:
        return done(ERROR, "stale_source")  # tested source is not what is in the worktree now
    if problem:
        return done(ERROR, "test_" + problem)
    if rc == 0:
        return done(PASS, "tests_passed")
    if rc in (ERROR_EXIT, 126, 127) or rc < 0:
        return done(ERROR, "test_error_exit")
    return done(FAIL, "test_failed")
