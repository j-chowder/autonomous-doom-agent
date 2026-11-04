"""Persistent loop state.

artifacts/state/state.json  current truth, replaced atomically
artifacts/events.jsonl      append-only history, one JSON object per line

Every change appends its event first (write-ahead), then applies it with
_reduce() and saves state with last_seq = that event's seq. On open, events
newer than last_seq are re-applied in order by the same _reduce(). A failed
event leaves state unchanged; replay stops, and open fails, at the first event
that cannot be applied, with last_seq left at the last event that was.

The journal must be a contiguous run of valid lines with seq 1, 2, 3...
Only an incomplete final line (no trailing newline) is treated as a torn
write: it is moved to events.jsonl.torn-<offset> and removed. Anything else
(bad line mid-file, bad newline-terminated last line, seq gap or repeat) raises
JournalCorrupt and nothing is modified.

Only one controller may hold the state directory lock at a time.
"""
import copy
import fcntl
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .preserve import preserve_worktree

TRANSITIONS = {
    "PENDING": {"EXECUTING", "BLOCKED"},
    "EXECUTING": {"EXECUTED", "FAILED", "PENDING", "QUARANTINED"},
    "EXECUTED": {"VERIFIED", "FAILED"},
    "FAILED": {"PENDING", "BLOCKED"},
    "QUARANTINED": {"PENDING", "BLOCKED"},
    "VERIFIED": set(),
    "BLOCKED": set(),
}
OUTCOMES = ("completed", "failed", "timeout")
RELEASE_CLASSES = ("preserved_manually", "nothing_to_preserve", "discard_accepted")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class JournalCorrupt(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_json_atomic(path, obj):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write((json.dumps(obj, indent=2) + "\n").encode())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        events, good_len, tail = self._scan()
        self.torn_tail = None
        if tail:
            self.torn_tail = self.path.with_name("%s.torn-%d" % (self.path.name, good_len))
            with open(self.torn_tail, "ab") as f:
                f.write(tail)
                f.flush()
                os.fsync(f.fileno())
            with open(self.path, "r+b") as f:
                f.truncate(good_len)
                f.flush()
                os.fsync(f.fileno())
        self.seq = events[-1]["seq"] if events else 0

    def _scan(self):
        """-> (events, bytes of valid data, incomplete trailing bytes)."""
        data = self.path.read_bytes() if self.path.exists() else b""
        lines = data.split(b"\n")
        tail = lines.pop()  # bytes after the last newline; b"" when the file ends cleanly
        if tail:
            lines.append(tail)
        events, prev = [], 0
        for n, line in enumerate(lines, 1):
            try:
                ev = json.loads(line)
                ok = isinstance(ev, dict) and isinstance(ev.get("seq"), int) and isinstance(ev.get("type"), str)
            except ValueError:
                ok = False
            if not ok:
                if tail and n == len(lines):
                    return events, len(data) - len(tail), tail
                raise JournalCorrupt("%s line %d is malformed" % (self.path.name, n))
            if ev["seq"] != prev + 1:
                raise JournalCorrupt("%s line %d: seq %r after seq %d" % (self.path.name, n, ev["seq"], prev))
            events.append(ev)
            prev = ev["seq"]
        return events, len(data), b""

    def read(self):
        return self._scan()[0]

    def _needs_newline(self):
        try:
            with open(self.path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                return f.read(1) != b"\n"
        except OSError:
            return False

    def append(self, etype, **fields):
        self.seq += 1
        ev = dict(fields, seq=self.seq, ts=now(), type=etype)
        data = (json.dumps(ev, sort_keys=True) + "\n").encode()
        if self._needs_newline():
            data = b"\n" + data
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return ev


def apply_event(s, ev):
    """Apply one journal event to state dict `s` (mutates `s`; caller passes a copy)."""
    typ = ev["type"]
    if typ == "run_started":
        s["run_id"] = ev["run_id"]
        return
    if typ == "task_added":
        s["tasks"].setdefault(ev["task_id"], ev["task"])
        return
    if typ == "launched":
        t = s["tasks"][ev["task_id"]]
        if t["status"] != "EXECUTING" or t["current"]["attempt"] != ev["attempt"]:
            raise ValueError("%s: launched event for an attempt that is not executing" % ev["task_id"])
        t["current"]["pid"], t["current"]["pgid"] = ev["pid"], ev["pgid"]
        return
    if "to" not in ev:
        return
    t = s["tasks"].get(ev["task_id"])
    if t is None:
        raise ValueError("unknown task %r" % ev["task_id"])
    if t["status"] != ev["from"] or ev["to"] not in TRANSITIONS[t["status"]]:
        raise ValueError("%s: cannot apply %s -> %s (task is %s)" % (ev["task_id"], ev["from"], ev["to"], t["status"]))
    t["status"] = ev["to"]
    if typ == "intent":
        t["attempt_seq"] = ev["attempt"]
        t["current"] = {"attempt": ev["attempt"], "worktree": ev["worktree"],
                        "base_sha": ev["base_sha"], "started_at": ev["ts"]}
    elif typ == "result":
        if ev["outcome"] != "completed":
            t["attempts"]["coding"] += 1
        if ev["outcome"] == "timeout":
            t["attempts"]["timeout"] += 1
    elif typ == "recovered":
        t["attempts"]["interrupt"] += 1
    elif typ == "recovery_failed":
        t["attempts"]["interrupt"] += 1
        s["quarantine"][ev["worktree"]] = {"task_id": ev["task_id"], "attempt": ev["attempt"]}
    elif typ == "released":
        for path in [p for p, q in s["quarantine"].items() if q["task_id"] == ev["task_id"]]:
            del s["quarantine"][path]
    elif typ == "transition":
        if ev["to"] == "VERIFIED":
            t["verified_sha"] = ev["sha"]
        elif ev["to"] == "FAILED":
            t["attempts"]["coding"] += 1


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / "artifacts" / "state"
        self.state_path = self.dir / "state.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(self.dir, os.O_RDONLY)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._lock_fd)
            raise RuntimeError("another controller holds %s" % self.dir)
        try:
            self.journal = Journal(self.root / "artifacts" / "events.jsonl")
            if self.state_path.exists():
                self.state = json.loads(self.state_path.read_text())
            else:
                self.state = {"schema": 1, "run_id": None, "last_seq": 0, "tasks": {}}
            self.state.setdefault("quarantine", {})
            self.replayed = self._replay()
            if self.state["run_id"] is None:
                run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
                self._commit("run_started", run_id=run_id)
        except Exception:
            self.close()
            raise

    def close(self):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _save(self, ev):
        self.state["last_seq"] = ev["seq"]
        write_json_atomic(self.state_path, self.state)

    def _replay(self):
        if self.state["last_seq"] > self.journal.seq:
            raise RuntimeError("state.json (seq %d) is ahead of the journal (seq %d)"
                               % (self.state["last_seq"], self.journal.seq))
        applied = []
        try:
            for ev in self.journal.read():
                if ev["seq"] <= self.state["last_seq"]:
                    continue
                try:
                    self._reduce(ev)
                except Exception as e:
                    raise RuntimeError("journal replay stopped at seq %d: %s: %s" % (ev["seq"], type(e).__name__, e))
                self.state["last_seq"] = ev["seq"]
                applied.append(ev["seq"])
        finally:
            if applied:
                write_json_atomic(self.state_path, self.state)
        return applied

    def _reduce(self, ev):
        new = copy.deepcopy(self.state)
        apply_event(new, ev)
        self.state = new

    def _commit(self, etype, **fields):
        ev = self.journal.append(etype, **fields)
        self._reduce(ev)
        self._save(ev)

    def _move(self, task_id, to, etype, attempt=None, **data):
        t = self.state["tasks"][task_id]
        if to not in TRANSITIONS[t["status"]]:
            raise ValueError("%s: illegal transition %s -> %s" % (task_id, t["status"], to))
        self._commit(etype, task_id=task_id, attempt=t["attempt_seq"] if attempt is None else attempt,
                     **dict(data, **{"from": t["status"], "to": to}))

    def is_quarantined(self, worktree):
        return str(worktree) in self.state["quarantine"]

    def add_task(self, task_id, title, role, depends_on=()):
        if not ID_RE.match(task_id):
            raise ValueError("bad task id: %r" % task_id)
        if task_id in self.state["tasks"]:
            raise ValueError("duplicate task: " + task_id)
        task = {
            "id": task_id, "title": title, "role": role, "depends_on": list(depends_on),
            "status": "PENDING", "attempt_seq": 0,
            "attempts": {"coding": 0, "interrupt": 0, "timeout": 0},
            "current": None, "verified_sha": None,
        }
        self._commit("task_added", task_id=task_id, task=task)

    def next_task(self):
        tasks = self.state["tasks"]
        for t in tasks.values():
            if t["status"] == "PENDING" and all(tasks[d]["status"] == "VERIFIED" for d in t["depends_on"]):
                return t["id"]
        return None

    def begin_attempt(self, task_id, worktree=None, base_sha=None):
        if worktree and self.is_quarantined(worktree):
            raise ValueError("worktree is quarantined: %s" % worktree)
        n = self.state["tasks"][task_id]["attempt_seq"] + 1
        self._move(task_id, "EXECUTING", "intent", attempt=n,
                   worktree=str(worktree) if worktree else None, base_sha=base_sha)
        return n

    def note_launched(self, task_id, pid, pgid, argv):
        t = self.state["tasks"][task_id]
        if t["status"] != "EXECUTING":
            raise ValueError("%s is not executing" % task_id)
        self._commit("launched", task_id=task_id, attempt=t["attempt_seq"], pid=pid, pgid=pgid, argv=list(argv))

    def record_result(self, task_id, outcome, **data):
        if outcome not in OUTCOMES:
            raise ValueError("bad outcome: %r" % outcome)
        self._move(task_id, "EXECUTED" if outcome == "completed" else "FAILED", "result", outcome=outcome, **data)

    def transition(self, task_id, to, reason=None, sha=None):
        status = self.state["tasks"][task_id]["status"]
        if status == "EXECUTING":
            raise ValueError("%s: use record_result or recover while EXECUTING" % task_id)
        if status == "QUARANTINED" and to == "PENDING":
            raise ValueError("%s: use release() to leave quarantine" % task_id)
        if to == "VERIFIED" and not sha:
            raise ValueError("%s: VERIFIED requires a source sha" % task_id)
        self._move(task_id, to, "transition", reason=reason, sha=sha)

    def release(self, task_id, classification, note):
        if self.state["tasks"][task_id]["status"] != "QUARANTINED":
            raise ValueError("%s is not quarantined" % task_id)
        if classification not in RELEASE_CLASSES or not note:
            raise ValueError("release needs a classification in %s and a note" % (RELEASE_CLASSES,))
        self._move(task_id, "PENDING", "released", classification=classification, note=note)

    def preserve_attempt(self, task_id):
        t = self.state["tasks"][task_id]
        cur, attempt = t["current"] or {}, t["attempt_seq"]
        dest = self.root / "artifacts" / "runs" / self.state["run_id"] / "preserved" / ("%s-a%d" % (task_id, attempt))
        if not cur.get("worktree"):
            pm = {"status": "no_worktree"}
        else:
            try:
                pm = preserve_worktree(cur["worktree"], dest,
                                       "refs/loop/preserved/%s/%s-a%d" % (self.state["run_id"], task_id, attempt),
                                       cur.get("base_sha"))
            except Exception as e:
                pm = {"status": "error", "error": str(e)}
        pm["dir"] = str(dest.relative_to(self.root))
        return pm

    def recover(self):
        report = {"replayed": self.replayed, "torn_tail": None, "interrupted": [], "quarantined": []}
        if self.replayed:
            self._commit("state_reconciled", replayed_seqs=self.replayed)
            self.replayed = []
        if self.journal.torn_tail:
            report["torn_tail"] = self.journal.torn_tail.name
            self._commit("journal_tail_discarded", sidecar=self.journal.torn_tail.name)
            self.journal.torn_tail = None
        for t in list(self.state["tasks"].values()):
            if t["status"] != "EXECUTING":
                continue
            cur, attempt = t["current"] or {}, t["attempt_seq"]
            if cur.get("pgid") and group_alive(cur["pgid"]):
                pm = {"status": "agent_running", "dir": None,
                      "error": "agent process group %d may still be running" % cur["pgid"]}
            else:
                pm = self.preserve_attempt(t["id"])
            info = dict(preserved_dir=pm["dir"], preserved_status=pm["status"])
            if pm["status"] in ("preserved", "no_worktree"):
                self._move(t["id"], "PENDING", "recovered", attempt=attempt, reason="interrupted",
                           snapshot_commit=pm.get("snapshot_commit"), **info)
                report["interrupted"].append(t["id"])
            else:
                self._move(t["id"], "QUARANTINED", "recovery_failed", attempt=attempt, worktree=cur["worktree"],
                           error=pm.get("error", pm["status"]), **info)
                report["quarantined"].append(t["id"])
        return report
