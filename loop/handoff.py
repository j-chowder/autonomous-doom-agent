"""Step 5: handoff documents between roles, and the controller-owned validator for them.

Every non-coder role (planner, reviewer, tester) is a Step 4 task whose ONLY allowed change is its own handoff file
(`handoff/<task>.json`, plus `handoff/<task>.tests/` for the tester). The task's trusted test command is

    python3 /abs/loop/handoff.py check ROLE FILE [--policy POLICY.json]

run by verify.py in an exported copy of the role's worktree. So the existing machinery gives each role:
  - read-only enforcement  (any other changed file is a scope violation -> FAIL)
  - schema enforcement     (an invalid handoff -> FAIL, the errors printed here become the retry feedback)
  - retries, preservation, journaling, and an accepted commit that carries the handoff file.

Exit codes follow verify.py: 0 valid, 1 invalid (FAIL), 70 the validator itself cannot run (ERROR).
Agents never decide validity. This file is hashed into `test_files`, so it cannot change under a run.
"""
import argparse
import json
import posixpath
import re
import sys
from pathlib import Path

ERROR_EXIT = 70
PLAN_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")  # no "." : the coordinator derives T1.review, T1.fix1, ...
MAX_TEXT = 6000
MAX_TEST_BYTES = 20000


def norm(path):
    """Normalized relative posix path, or None if it is absolute, escapes, or is empty."""
    if not isinstance(path, str) or not path.strip() or path.startswith("/") or "\\" in path:
        return None
    n = posixpath.normpath(path.strip())
    return None if n in (".", "") or ".." in n.split("/") else n


def under(path, roots):
    return any(path == r or path.startswith(r + "/") for r in roots)


def _text(errors, where, v, limit=MAX_TEXT):
    if not isinstance(v, str) or not v.strip():
        errors.append("%s must be a non-empty string" % where)
    elif len(v) > limit:
        errors.append("%s is longer than %d characters" % (where, limit))


def check_plan(doc, policy):
    """Plan = a linear chain of coder tasks. -> list of error strings (empty = valid)."""
    errors = []
    if not isinstance(doc, dict) or not isinstance(doc.get("tasks"), list) or not doc["tasks"]:
        return ['top level must be {"tasks": [ ... ]} with at least one task']
    tasks = doc["tasks"]
    if len(tasks) > policy["max_tasks"]:
        errors.append("too many tasks: %d (maximum %d)" % (len(tasks), policy["max_tasks"]))
    used, seen, prev = set(policy.get("used_ids", ())), set(), None
    roots = [norm(r) for r in policy["allowed_roots"]]
    protected = [norm(r) for r in policy.get("protected", ())]
    for i, t in enumerate(tasks):
        w = "tasks[%d]" % i
        if not isinstance(t, dict):
            errors.append("%s must be an object" % w)
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not PLAN_ID_RE.match(tid):
            errors.append("%s.id must match %s (no dots)" % (w, PLAN_ID_RE.pattern))
        elif tid in used:
            errors.append("%s.id %r was already used in this run; choose a new id" % (w, tid))
        elif tid in seen:
            errors.append("%s.id %r is repeated in this plan" % (w, tid))
        seen.add(tid)
        _text(errors, w + ".title", t.get("title"), 120)
        _text(errors, w + ".instructions", t.get("instructions"))
        _text(errors, w + ".acceptance", t.get("acceptance"))
        if t.get("profile") not in policy["profiles"]:
            errors.append("%s.profile %r is not a controller-owned verification profile; choose one of: %s"
                          % (w, t.get("profile"), ", ".join(sorted(policy["profiles"]))))
        role = t.get("coder_role", "coder")
        if role not in policy["coder_roles"]:
            errors.append("%s.coder_role %r is not allowed; choose one of: %s" % (w, role, ", ".join(sorted(policy["coder_roles"]))))
        allowed = t.get("allowed")
        if not isinstance(allowed, list) or not allowed:
            errors.append("%s.allowed must be a non-empty list of repository-relative paths" % w)
        else:
            role_roots = [norm(r) for r in policy["coder_roles"].get(role, {}).get("allowed_roots", policy["allowed_roots"])]
            for raw in allowed:
                p = norm(raw)
                if p is None:
                    errors.append("%s.allowed has an unusable path %r (relative, no '..')" % (w, raw))
                elif protected and under(p, protected):
                    errors.append("%s.allowed %r is protected" % (w, raw))
                elif not under(p, roots) or not under(p, role_roots):
                    errors.append("%s.allowed %r is outside the approved paths (%s)" % (w, raw, ", ".join(role_roots)))
        want = [prev] if prev else []
        if t.get("depends_on", []) != want:
            errors.append('%s.depends_on must be %s (a linear chain: each task depends only on the one before it)' % (w, json.dumps(want)))
        prev = tid
    return errors


def check_review(doc):
    errors = []
    if not isinstance(doc, dict):
        return ["top level must be an object"]
    verdict, findings = doc.get("verdict"), doc.get("findings")
    if verdict not in ("pass", "changes_requested"):
        errors.append('verdict must be "pass" or "changes_requested"')
    _text(errors, "summary", doc.get("summary"), 2000)
    if not isinstance(findings, list) or len(findings) > 20:
        return errors + ["findings must be a list of at most 20 items"]
    ids, blocking = set(), 0
    for i, f in enumerate(findings):
        w = "findings[%d]" % i
        if not isinstance(f, dict):
            errors.append("%s must be an object" % w)
            continue
        if f.get("id") in ids or not isinstance(f.get("id"), str) or not f["id"].strip():
            errors.append("%s.id must be a unique non-empty string" % w)
        ids.add(f.get("id"))
        if f.get("severity") not in ("blocking", "minor"):
            errors.append('%s.severity must be "blocking" or "minor"' % w)
        _text(errors, w + ".summary", f.get("summary"), 1000)
        if f.get("severity") == "blocking":
            blocking += 1
            # A blocking finding must be checkable: it names the requirement and the concrete evidence.
            _text(errors, w + ".criterion (the task requirement or criterion id it violates)", f.get("criterion"), 500)
            _text(errors, w + ".evidence (file:line or command output that shows it)", f.get("evidence"), 2000)
    if verdict == "pass" and blocking:
        errors.append('verdict "pass" cannot have blocking findings')
    if verdict == "changes_requested" and not blocking:
        errors.append('verdict "changes_requested" needs at least one blocking finding (with criterion and evidence)')
    return errors


def check_tester(doc, root=".", doc_path=None):
    errors = []
    if not isinstance(doc, dict):
        return ["top level must be an object"]
    _text(errors, "summary", doc.get("summary"), 2000)
    tests = doc.get("tests")
    if not isinstance(tests, list) or len(tests) > 5:
        return errors + ["tests must be a list of at most 5 items (it may be empty)"]
    for i, t in enumerate(tests):
        w = "tests[%d]" % i
        if not isinstance(t, dict):
            errors.append("%s must be an object" % w)
            continue
        f = norm(t.get("file"))
        if f is None or not f.startswith("handoff/") or f == doc_path:
            errors.append("%s.file must be a path under handoff/ (your tests directory)" % w)
        else:
            p = Path(root) / f
            if not p.is_file():
                errors.append("%s.file %s does not exist" % (w, f))
            elif p.stat().st_size > MAX_TEST_BYTES:
                errors.append("%s.file %s is larger than %d bytes" % (w, f, MAX_TEST_BYTES))
        _text(errors, w + ".purpose", t.get("purpose"), 500)
        _text(errors, w + ".criterion", t.get("criterion"), 500)
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate a role handoff file (controller-owned test command).")
    ap.add_argument("verb", choices=["check"])
    ap.add_argument("role", choices=["planner", "reviewer", "tester"])
    ap.add_argument("file", help="handoff file, relative to the current directory")
    ap.add_argument("--policy", help="policy JSON (planner only)")
    a = ap.parse_args(argv)
    try:
        policy = json.loads(Path(a.policy).read_text()) if a.policy else None
        if a.role == "planner" and policy is None:
            raise ValueError("planner check needs --policy")
    except (OSError, ValueError) as e:
        print("handoff check cannot run: %s" % e)
        return ERROR_EXIT
    path = Path(a.file)
    if not path.is_file():
        print("INVALID: %s was not written. Write exactly this file." % a.file)
        return 1
    try:
        doc = json.loads(path.read_text())
    except ValueError as e:
        print("INVALID: %s is not valid JSON: %s" % (a.file, e))
        return 1
    errors = {"planner": lambda: check_plan(doc, policy), "reviewer": lambda: check_review(doc),
              "tester": lambda: check_tester(doc, ".", norm(a.file))}[a.role]()
    if errors:
        print("INVALID %s handoff %s:" % (a.role, a.file))
        for e in errors:
            print("  - " + e)
        return 1
    print("OK: %s handoff %s is valid" % (a.role, a.file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
