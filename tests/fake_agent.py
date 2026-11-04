"""Scripted agent (run by tests/fake_docker_agent.py, or directly by tests) for runner tests and the demo."""
import json
import os
import signal
import subprocess
import sys
import time

mode = sys.argv[1]
hb = os.environ.get("FAKE_HB")
journal = os.environ.get("FAKE_JOURNAL")


def write(name, text):
    with open(name, "w") as f:
        f.write(text)


def heartbeat():
    if hb:
        subprocess.Popen([sys.executable, "-c",
                          "import sys,time\nwhile True:\n    open(sys.argv[1],'a').write('x')\n    time.sleep(0.05)", hb])


if journal:
    intents = [json.loads(line) for line in open(journal) if '"type": "intent"' in line]
    write("observed.json", json.dumps({"intent_worktree": intents[-1]["worktree"] if intents else None,
                                       "cwd": os.getcwd(), "api_key_set": "ANTHROPIC_API_KEY" in os.environ}))

if mode == "ok":
    write("hello.txt", "hello\n")
    print("agent done", flush=True)
elif mode == "ok_leftover":
    write("hello.txt", "hello\n")
    heartbeat()
    deadline = time.time() + 5
    while time.time() < deadline and not (os.path.exists(hb) and os.path.getsize(hb)):
        time.sleep(0.02)
elif mode == "fail":
    write("partial.txt", "half\n")
    print("boom", file=sys.stderr, flush=True)
    sys.exit(3)
elif mode == "die":
    write("partial.txt", "half\n")
    os.kill(os.getpid(), signal.SIGKILL)
elif mode in ("hang", "stubborn"):
    if mode == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    write("partial.txt", "half\n")
    heartbeat()
    time.sleep(60)
else:
    sys.exit("unknown mode " + mode)
