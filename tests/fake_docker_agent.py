"""Stand-in for the standalone `docker-agent` CLI. Accepts only the syntax the runner is meant to use:

    fake_docker_agent.py run --exec CONFIG [-a AGENT] --working-dir DIR PROMPT
    fake_docker_agent.py run --help
    fake_docker_agent.py doctor CONFIG

PROMPT is the scripted mode for tests/fake_agent.py. Like a CLI that does not
trust its inherited cwd, it moves to / first and only then into --working-dir.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]

if args[:1] == ["doctor"]:
    if os.environ.get("FAKE_CLI_DOCTOR_FAIL"):
        sys.exit("not logged in")
    print("ok")
    sys.exit(0)

if args[:2] == ["run", "--help"]:
    flags = ["--exec", "--agent", "--working-dir", "--json"]
    omit = os.environ.get("FAKE_CLI_HELP_OMIT")
    print("\n".join(f for f in flags if f != omit))
    sys.exit(0)

ap = argparse.ArgumentParser(prog="fake-docker-agent")
ap.add_argument("cmd", choices=["run"])
ap.add_argument("--exec", action="store_true", required=True, dest="exec_")
ap.add_argument("config")
ap.add_argument("-a", "--agent")
ap.add_argument("--working-dir", required=True)
ap.add_argument("message")
a = ap.parse_args(args)

if not os.path.isabs(a.config) or not os.path.isfile(a.config):
    sys.exit("config must be an absolute existing file: %s" % a.config)
if not os.path.isdir(a.working_dir):
    sys.exit("working dir missing: %s" % a.working_dir)
if os.path.realpath(a.config).startswith(os.path.realpath(a.working_dir) + os.sep):
    sys.exit("config is inside the working dir")
if os.path.exists(os.path.join(a.working_dir, "loop", "agents.yaml")):
    sys.exit("test setup error: worktree unexpectedly contains loop/agents.yaml")
os.chdir("/")
os.chdir(a.working_dir)
os.execv(sys.executable, [sys.executable, os.path.join(HERE, "fake_agent.py"), a.message])
