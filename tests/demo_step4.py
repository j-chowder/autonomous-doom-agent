"""Step 4 demo on the trivial add() fixture (not DOOM).

  python3 tests/demo_step4.py            MOCK agent (tests/fake_coder_cli.py): success, repair, exhausted budget
  python3 tests/demo_step4.py --docker   REAL standalone `docker-agent` + Claude Code harness, one task, 3 attempts max
                                         (uses your subscription login; the real agent may pass on attempt 1)

Prints the journal's controller events and the final state. The controller, not the agent, decides everything.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from loop.controller import Controller  # noqa: E402
from loop.state import Store  # noqa: E402
from step4_fixture import git, make_cfg, make_repo  # noqa: E402


def show(main):
    store = Store(main)
    try:
        for e in store.journal.read():
            if e["type"] in ("verification", "decision", "intent", "result", "transition", "recovered", "controller_stopped"):
                keys = ("attempt", "from", "to", "outcome", "classification", "decision", "action", "reason", "exit_code", "status", "accepted_sha", "sha")
                print("   %3d %-18s %s" % (e["seq"], e["type"], {k: e[k] for k in keys if e.get(k) is not None}))
        t = store.state["tasks"]["T1"]
        print("   final: status=%s attempts=%d verified_sha=%s" % (t["status"], t["attempt_seq"], t["verified_sha"]))
    finally:
        store.close()
    print("   accepted refs:", git(main, "for-each-ref", "--format=%(refname) %(objectname:short)", "refs/loop/accepted") or "(none)")


def scenario(title, mode, real=False, max_attempts=3):
    print("\n=== %s ===" % title)
    tmp = Path(tempfile.mkdtemp(prefix="loop-step4-"))
    main, base, check = make_repo(tmp)
    (tmp / "fake").mkdir()
    cfg = make_cfg(tmp, base, check, max_attempts=max_attempts)
    if real:
        cfg["agent"] = {"config": str(REPO / "loop" / "agents.yaml"), "name": "coder", "binary": "docker-agent"}
        # The controller refuses to start an attempt unless max_runtime_s > agent_timeout_s (the fixture default is 300).
        # 2400s = up to 3 attempts of 600s plus verification.
        cfg["budgets"].update(agent_timeout_s=600, max_runtime_s=2400)
    os.environ.update(FAKE_MODE=mode, FAKE_DIR=str(tmp / "fake"))
    store = Store(main)
    try:
        result = Controller(store, cfg).run()
    finally:
        store.close()
    print("   result:", result, "| repo:", main)
    show(main)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--docker", action="store_true")
    if ap.parse_args().docker:
        scenario("REAL docker-agent: fix add()", "n/a", real=True)
    else:
        scenario("MOCK 1: success on the first attempt", "good")
        scenario("MOCK 2: first attempt fails, repaired from feedback", "repair")
        scenario("MOCK 3: agent never fixes it; attempt budget (2) exhausted", "always_bad", max_attempts=2)
