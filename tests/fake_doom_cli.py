"""Stand-in for `docker-agent` for the DOOM-shaped dry run (MOCK: no model). Roles: planner, coder, reviewer, tester.
Prompts are the controller-built ones; the mock reads TASK_ID / OUTPUT_FILE / TESTS_DIR like a real agent.
FAKE_DIR/calls.log records every call. The coder writes the files each plan task needs, including a gitignored dist/ output."""
import argparse
import json
import os
import re
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
    f.write(json.dumps({"agent": a.agent, "task": tid}) + "\n")


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)


if a.agent == "planner":
    def t(i, title, text, allowed, profile, role):
        return dict(id=i, title=title, instructions="[work:%s] %s" % (i, text), acceptance="gate %s passes" % profile, allowed=allowed,
                    profile=profile, coder_role=role, depends_on=[])
    tasks = [t("D1", "build pipeline", "Create build.sh and the platform main.", ["platform", "build.sh", "dist"], "build", "wasm_platform"),
             t("D2", "hooks and page", "Add the six getters and load the module in the page.", ["platform", "web", "build.sh", "dist"], "m1", "integrator"),
             t("D3", "finish", "Final polish of the page.", ["web", "dist"], "final", "web_frontend")]
    for i, x in enumerate(tasks):
        x["depends_on"] = [tasks[i - 1]["id"]] if i else []
    write(out, json.dumps({"tasks": tasks, "notes": "mock DOOM plan"}, indent=1))
elif a.agent == "coder":
    m = re.search(r"\[work:(\w+)\]", msg)
    w = m.group(1) if m else None
    if w == "D1":
        write("build.sh", "#!/bin/sh\nmkdir -p dist/doom\ncp web/index.html dist/doom/index.html\n")
        write("platform/main.c", "int main(void){return 0;}\n")
        write("dist/doom/index.html", "<html>built</html>\n")  # gitignored build output inside an allowed root
    elif w == "D2":
        write("platform/dg_test_hooks.c", "unsigned DG_TEST_GetGameTic(void){return 0;}\n")  # flagged path -> review + tester
        write("web/index.html", "<html><script>var Module={};</script></html>\n")
    elif w == "D3":
        write("web/index.html", "<html><script>var Module={};</script><!-- final-ready --></html>\n")
    else:
        raise SystemExit("mock coder: no [work:x] tag")
elif a.agent == "reviewer":
    write(out, json.dumps({"verdict": "pass", "summary": "hooks are getters only", "findings": []}, indent=1))
elif a.agent == "tester":
    d = field("TESTS_DIR").rstrip("/")
    write(d + "/note.txt", "proposal\n")
    write(out, json.dumps({"summary": "notes", "tests": [{"file": d + "/note.txt", "purpose": "note", "criterion": "gate"}]}, indent=1))
print("fake doom %s done (%s)" % (a.agent, tid))
