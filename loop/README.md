# The agent loop

A deterministic Python controller that runs AI coding agents and accepts their work only after independent checks pass.

| Entry point | What it runs |
|---|---|
| `./loop/loop.sh CONFIG.json` | Controller: one task list, attempts, verification, retries |
| `python3 -m loop.coordinator POLICY.json` | Planner, coder, reviewer and tester roles on top of the controller |
| `python3 loop/doom_setup.py verify` | Checks that the trusted DOOM tests still match their frozen hashes |

| Module | Responsibility |
|---|---|
| `state.py` | Task state machine, atomic state writes, append-only journal, replay on restart |
| `preserve.py` | Git snapshots of failed or interrupted attempts |
| `runner.py` | One agent attempt in a fresh git worktree, with timeout and process cleanup |
| `verify.py` | Scope check (allowed and protected paths), exact-candidate test run, PASS / FAIL / ERROR |
| `sandbox_verify.py` | Runs trusted tests in Docker: no network, read-only mounts, no credentials |
| `controller.py` | Schedules tasks, retries with feedback, budgets, accepted refs |
| `coordinator.py`, `handoff.py` | Roles, plan and handoff validation, review-driven fixes, one replan |
| `agents.yaml` | Docker Agent harness definitions (Claude Code), one per role |

Design and decisions: [`step5.md`](step5.md). Sandbox limits: [`sandbox.md`](sandbox.md).

**Requirements:** Python 3.10+, Git, Docker, the `docker-agent` CLI, and a logged-in `claude` CLI. Agents run as Claude Code harness agents: Docker Agent toolsets, budgets and tool approval do not apply to them, so role limits are enforced by the controller after each run.
