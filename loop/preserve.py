import json
import os
import subprocess
import tempfile
from pathlib import Path

IDENT = {
    "GIT_AUTHOR_NAME": "loop",
    "GIT_AUTHOR_EMAIL": "loop@localhost",
    "GIT_COMMITTER_NAME": "loop",
    "GIT_COMMITTER_EMAIL": "loop@localhost",
}


def _git(cwd, *args, env=None, check=True):
    full_env = dict(os.environ)
    full_env.update(env or {})
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, env=full_env)
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), p.stderr.decode(errors="replace").strip()))
    return p


def _out(cwd, *args, **kw):
    return _git(cwd, *args, **kw).stdout.decode(errors="replace").strip()


def preserve_worktree(worktree, dest, ref, base_sha=None):
    """Save everything an agent left in a worktree without modifying it.

    A snapshot commit (HEAD + all tracked, staged and untracked files) is
    stored under `ref`; agent commits are its ancestors. A bundle of the
    snapshot (relative to base_sha) and status listings go to `dest`.
    """
    worktree, dest = Path(worktree), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    m = {"worktree": str(worktree), "base_sha": base_sha, "ref": ref}
    if not worktree.is_dir():
        m["status"] = "worktree_missing"
    else:
        head = _out(worktree, "rev-parse", "HEAD")
        (dest / "status.txt").write_text(_out(worktree, "status", "--porcelain=v1", "-uall") + "\n")
        ignored = _out(worktree, "ls-files", "--others", "--ignored", "--exclude-standard")
        (dest / "ignored_not_preserved.txt").write_text(ignored + "\n")
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(IDENT, GIT_INDEX_FILE=os.path.join(tmp, "index"))
            _git(worktree, "read-tree", "HEAD", env=env)
            _git(worktree, "add", "-A", env=env)
            tree = _out(worktree, "write-tree", env=env)
        commit = _out(worktree, "commit-tree", tree, "-p", head, "-m", "loop snapshot " + ref, env=IDENT)
        _git(worktree, "update-ref", ref, commit)
        have_base = bool(base_sha) and _git(worktree, "cat-file", "-e", "%s^{commit}" % base_sha, check=False).returncode == 0
        bundle_args = ["bundle", "create", str(dest / "snapshot.bundle"), ref] + (["^" + base_sha] if have_base else [])
        _git(worktree, *bundle_args)
        _git(worktree, "bundle", "verify", str(dest / "snapshot.bundle"))
        m.update(
            status="preserved",
            head=head,
            snapshot_commit=commit,
            clean=(tree == _out(worktree, "rev-parse", "HEAD^{tree}")),
            commits_since_base=_out(worktree, "rev-list", "%s..%s" % (base_sha, head)).split() if have_base else None,
            ignored_file_count=len(ignored.splitlines()),
            bundle="snapshot.bundle",
        )
    (dest / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    return m
