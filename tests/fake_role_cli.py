"""Stand-in for the standalone `docker-agent` CLI for Step 5 (MOCK: no model, no Claude Code).

One script plays all four roles; the role is the `-a` agent name the controller passes (planner/coder/reviewer/tester).
It reads the controller-built prompt (TASK_ID:, OUTPUT_FILE:, TESTS_DIR:, [feature:x] tags) like a real agent would.

Scenario switches (env):
  FAKE_DIR               every (agent, task id, prompt) is appended to FAKE_DIR/calls.log as one JSON line
  FAKE_PLAN              good (default) | blocked_once (plan 1 puts div under docs/, which the coder cannot satisfy) | invalid_once
  FAKE_CODER             good (default) | regress_mul   (the first mul attempt breaks add(); the retry with feedback repairs it)
  FAKE_REVIEW            pass (default) | block_once (first T1 review blocks; the fix clears it) | rogue (reviewer also edits code)
  FAKE_KILL_PARENT       agent:task, e.g. coder:T2 - SIGKILL the coordinator once, mid-run (restart test)
  FAKE_TESTER            good (default) | bad_test      (the tester proposes a test that fails: evidence only)
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("cmd", choices=["run"])
ap.add_argument("--exec", action="store_true", required=True, dest="exec_")
ap.add_argument("config")
ap.add_argument("-a", "--agent", required=True)
ap.add_argument("--working-dir", required=True)
ap.add_argument("message")
a = ap.parse_args()
os.chdir(a.working_dir)
msg = a.message


def field(name):
    m = re.search(r"^%s[^:\n]*: (.*)$" % name, msg, re.M)
    return m.group(1).strip() if m else None


tid, out = field("TASK_ID"), field("OUTPUT_FILE")
with open(os.path.join(os.environ["FAKE_DIR"], "calls.log"), "a") as f:
    f.write(json.dumps({"agent": a.agent, "task": tid, "prompt": msg}) + "\n")


kill = os.environ.get("FAKE_KILL_PARENT")  # "agent:task": SIGKILL the coordinator once, while this agent is running
if kill == "%s:%s" % (a.agent, tid) and not os.path.exists(os.environ["FAKE_DIR"] + "/killed"):
    open(os.environ["FAKE_DIR"] + "/killed", "w").write("x")
    import signal
    os.kill(os.getppid(), signal.SIGKILL)


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)


ADD_OK = "def add(a, b):\n    return a + b\n"
ADD_DOC = 'def add(a, b):\n    """Return a + b."""\n    return a + b\n'
ADD_BAD = "def add(a, b):\n    return a - b\n"
MUL = "\n\ndef mul(a, b):\n    return a * b\n"
DIV = "\n\ndef div(a, b):\n    return a / b\n"

if a.agent == "planner":
    used = (field("USED_IDS") or "").split(", ")
    replan = "THIS IS A REPLAN" in msg
    t = lambda i, title, feat, allowed, profile: dict(  # noqa: E731
        id=i, title=title, instructions="[feature:%s] Implement %s in src/calc.py." % (feat, feat), acceptance="%s behaves correctly and earlier functions still work" % feat,
        allowed=allowed, profile=profile, coder_role="coder", depends_on=[])
    if os.environ.get("FAKE_PLAN") == "invalid_once" and "Feedback from the previous attempt" not in msg:
        tasks = [t("T.1", "bad id and profile", "add", ["src"], "no_such_profile")]
    elif replan:
        tasks = [t("T4", "div with the right scope", "div", ["src"], "div")]
    else:
        tasks = [t("T1", "add", "add", ["src"], "add"), t("T2", "mul", "mul", ["src"], "mul")]
        tasks.append(t("T3", "div", "div", ["docs"] if os.environ.get("FAKE_PLAN") == "blocked_once" else ["src"], "div"))
    for i, x in enumerate(tasks):  # chain
        x["depends_on"] = [tasks[i - 1]["id"]] if i else []
    assert not [x["id"] for x in tasks if x["id"] in used], "mock planner would reuse an id"
    write(out, json.dumps({"tasks": tasks, "notes": "mock plan"}, indent=1))

elif a.agent == "coder":
    feat = re.search(r"\[feature:(\w+)\]", msg)
    feat = feat.group(1) if feat else None
    fb = "Feedback from the previous attempt" in msg
    review_fix = "INDEPENDENT REVIEW requested changes" in msg
    src = Path("src/calc.py").read_text() if Path("src/calc.py").exists() else ""
    have_mul, have_div = "def mul" in src, "def div" in src
    add = ADD_DOC if ("def add" in src and '"""' in src) or review_fix else ADD_OK
    if feat == "add" and not review_fix:
        add = ADD_OK
    if feat == "mul":
        have_mul = True
        if os.environ.get("FAKE_CODER") == "regress_mul" and not fb:
            add = ADD_BAD  # a regression: new feature works, an old one breaks
    if feat == "div":
        have_div = True
    if feat is None and not review_fix:
        sys.exit("mock coder: no [feature:x] tag in the prompt")
    write("src/calc.py", add + (MUL if have_mul else "") + (DIV if have_div else ""))
    if feat == "div":  # a coder follows the instructions even if the plan's scope forbids it; the controller then rejects it
        pass

elif a.agent == "reviewer":
    first_t1 = tid == "T1.review"
    if os.environ.get("FAKE_REVIEW") == "block_once" and first_t1:
        doc = {"verdict": "changes_requested", "summary": "add() has no docstring",
               "findings": [{"id": "F1", "severity": "blocking", "criterion": "acceptance: add is documented", "file": "src/calc.py",
                             "evidence": "src/calc.py:1 def add has no docstring", "summary": "Add a docstring to add()."}]}
    else:
        doc = {"verdict": "pass", "summary": "matches the acceptance text",
               "findings": [{"id": "N1", "severity": "minor", "criterion": "style", "file": "src/calc.py", "evidence": "none", "summary": "naming is fine"}]}
    write(out, json.dumps(doc, indent=1))
    if os.environ.get("FAKE_REVIEW") == "rogue":
        write("src/evil.py", "x = 1\n")  # a reviewer that edits code: the controller must reject the run

elif a.agent == "tester":
    tests_dir = field("TESTS_DIR").rstrip("/")
    subject = field("TESTING")
    body = {"T1": "assert add(2, 3) == 5 and add(-1, 1) == 0", "T2": "assert mul(0, 5) == 0 and mul(-2, 3) == -6",
            "T3": "assert div(9, 3) == 3", "T4": "assert div(9, 3) == 3"}.get(subject.split(".")[0], "assert add(1, 1) == 2")
    if os.environ.get("FAKE_TESTER") == "bad_test":
        body = "assert add(2, 2) == 5  # deliberately wrong proposal"
    write(tests_dir + "/test_proposed.py",
          "import sys\nsys.path.insert(0, 'src')\nfrom calc import *\n" + body + "\nprint('proposed test ok')\n")
    write(out, json.dumps({"summary": "edge cases for " + subject,
                           "tests": [{"file": tests_dir + "/test_proposed.py", "purpose": "edge cases", "criterion": "acceptance"}]}, indent=1))
else:
    sys.exit("unknown agent " + a.agent)
print("fake %s done (%s)" % (a.agent, tid))
