"""Stand-in for the standalone `docker-agent` CLI for Step 4 (MOCK: no model, no Claude Code).

Accepts: fake_coder_cli.py run --exec CONFIG [-a AGENT] --working-dir DIR PROMPT
FAKE_MODE (env): good | repair | always_bad | scope_repair
  good         writes the correct src/calc.py
  repair       wrong code unless the prompt carries feedback, then correct (proves feedback is consumed)
  always_bad   wrong code every time
  scope_repair correct code plus an out-of-scope file unless the prompt carries feedback
FAKE_DIR (env): every prompt received is appended to FAKE_DIR/prompts.log (one JSON line each).
"""
import argparse
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("cmd", choices=["run"])
ap.add_argument("--exec", action="store_true", required=True, dest="exec_")
ap.add_argument("config")
ap.add_argument("-a", "--agent")
ap.add_argument("--working-dir", required=True)
ap.add_argument("message")
a = ap.parse_args()
os.chdir(a.working_dir)

with open(os.path.join(os.environ["FAKE_DIR"], "prompts.log"), "a") as f:
    f.write(json.dumps(a.message) + "\n")

mode = os.environ["FAKE_MODE"]
fb = "Feedback from the previous attempt" in a.message
good = "def add(a, b):\n    return a + b\n"
bad = "def add(a, b):\n    return a - b\n"
code = {"good": good, "repair": good if fb else bad, "always_bad": bad, "scope_repair": good}.get(mode)
if code is None:
    sys.exit("unknown FAKE_MODE " + mode)
with open("src/calc.py", "w") as f:
    f.write(code)
if mode == "scope_repair" and not fb:
    with open("evil.py", "w") as f:
        f.write("x = 1\n")
print("fake coder done (%s, feedback=%s)" % (mode, fb))
