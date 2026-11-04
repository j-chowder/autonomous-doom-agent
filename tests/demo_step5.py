"""Step 5 demo on the trivial calc fixture (not DOOM).

  python3 tests/demo_step5.py            MOCK agents (tests/fake_role_cli.py). One run that shows every behaviour:
                                          objective -> plan, coding regression repaired, blocked task -> one replan,
                                          blocking review -> one fix, separate reviewer/tester evidence, final gate.
  python3 tests/demo_step5.py --docker   REAL standalone `docker-agent` + Claude Code harness for ALL four roles
                                          (loop/agents.yaml; uses your subscription login). Max 40 agent invocations.
                                          The real planner will not blunder on purpose, so a replan may not happen.

The controller, not any agent, decides every outcome. Mocked vs real is printed at the top.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from loop.coordinator import Coordinator  # noqa: E402
from loop.state import Store  # noqa: E402
from step5_fixture import git, make_policy, make_repo  # noqa: E402

SHOW = ("coordinator_started", "plan_admitted", "task_accepted", "review_done", "review_unresolved", "review_fix_accepted",
        "tester_evidence", "replan_triggered", "task_dropped", "final_gate", "coordinator_stopped")


def brief(e):
    t = e["type"]
    if t == "plan_admitted":
        return "%s v%d tasks=%s replan=%s" % (e["plan"], e["version"], [x["id"] for x in e["tasks"]], e["replan"])
    if t == "task_accepted":
        return "%s accepted %s (profile %s)" % (e["task"], e["sha"][:10], e["profile"])
    if t == "review_done":
        return "%s verdict=%s blocking=%s" % (e["reviewer_task"], e["verdict"], [f["id"] for f in e["findings"] if f["severity"] == "blocking"])
    if t == "tester_evidence":
        return "%s proposed tests: %s" % (e["tester_task"], [(p["file"].split("/")[-1], p["result"]) for p in e["proposals"]])
    if t == "replan_triggered":
        return "trigger=%s superseded=%s (%d of %d)" % (e["trigger"], e["superseded"], e["replan_number"], e["of"])
    if t == "final_gate":
        return "gate=%s -> %s" % (e["gate"], e["decision"])
    if t == "coordinator_stopped":
        return "status=%s reason=%s" % (e["status"], e["reason"])
    return {k: v for k, v in e.items() if k in ("task", "fix", "verdict", "mode", "findings")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docker", action="store_true")
    real = ap.parse_args().docker
    tmp = Path(tempfile.mkdtemp(prefix="loop-step5-"))
    main_repo, base = make_repo(tmp)
    (tmp / "fake").mkdir()
    if real:
        policy = make_policy(tmp, base, mode="real", binary="docker-agent", agent_config=str(REPO / "loop" / "agents.yaml"),
                             agent_timeout_s=600, max_runtime_s=3000, max_total_runtime_s=6000, max_attempts=3, verify_timeout_s=120)
        print("REAL agents: planner, coder, reviewer, tester = Claude Code harness via docker-agent (loop/agents.yaml)")
    else:
        policy = make_policy(tmp, base)
        os.environ.update(FAKE_DIR=str(tmp / "fake"), FAKE_PLAN="blocked_once", FAKE_CODER="regress_mul", FAKE_REVIEW="block_once")
        print("MOCK agents (tests/fake_role_cli.py): scripted planner/coder/reviewer/tester; the controller, git, verify.py and the journal are real")
    print("repo:", main_repo, "\nobjective:", policy["objective"])
    store = Store(main_repo)
    try:
        result = Coordinator(store, policy).run()
        journal = store.journal.read()
    finally:
        store.close()
    print("\n--- decisions (coordinator events, in order) ---")
    for e in journal:
        if e["type"] in SHOW:
            print("  %3d %-20s %s" % (e["seq"], e["type"], brief(e)))
    print("\n--- Step 4 controller verification results ---")
    for e in journal:
        if e["type"] == "verification":
            print("  %-16s attempt %s: %s (%s)" % (e["task_id"], e["attempt"], e["decision"], e["classification"]))
    rep = json.loads(Path(result["report"]).read_text())
    print("\n--- who did what ---")
    for role, v in sorted(rep["roles"].items()):
        print("  %-9s agent=%-9s tasks=%d agent runs=%d" % (role, v["agent"], v["tasks"], v["attempts"]))
    print("\naccepted refs:\n  " + "\n  ".join(rep["accepted_refs"]))
    if real:
        print("\ncalls log not available for real agents; see artifacts/runs/%s" % rep["run_id"])
    else:
        calls = [json.loads(x) for x in (tmp / "fake" / "calls.log").read_text().splitlines()]
        print("\nagent calls in order:", " ".join("%s:%s" % (c["agent"][0].upper(), c["task"]) for c in calls))
    print("\nRESULT: %s (%s)\nreport: %s" % (result["status"], result["reason"], result["report"]))
    return 0 if result["status"].startswith("complete") else 1


if __name__ == "__main__":
    sys.exit(main())
