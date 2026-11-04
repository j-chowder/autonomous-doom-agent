# Step 5: autonomous planning, coordinated roles, bounded replanning

Entry point: `python3 -m loop.coordinator POLICY.json` (policy example: `tests/step5_fixture.py: make_policy`).
Demo: `python3 tests/demo_step5.py` (mock agents) and `python3 tests/demo_step5.py --docker` (real Claude Code harness agents).

## How it works (no second execution loop)

Every agent invocation is a Step 4 task run by `Controller.run()`. The Coordinator only decides WHICH task to admit next.

| Role | Task id | May change | Accepted when |
|---|---|---|---|
| planner | `PLAN-n` | `handoff/PLAN-n.json` only | `loop/handoff.py check planner` passes (ids, paths, profiles, chain, task count) |
| coder | plan id (`T1`) | the plan's `allowed` paths | the controller-owned verification profile passes |
| reviewer | `<id>.review` | `handoff/<id>.review.json` only | `handoff.py check reviewer` passes |
| tester | `<id>.test` | `handoff/<id>.test.json`, `handoff/<id>.test.tests/` | `handoff.py check tester` passes |
| fix | `<id>.fix1` | like the coder | same profile as the task it fixes |

Read-only roles are enforced by the existing scope check (any other changed file = FAIL), not by the YAML. An invalid
handoff is a FAIL, so Step 4 retries it with the validator's errors as feedback. Reviewer and tester prompts contain the task,
acceptance text, changed files and diff command, and nothing from the coder's own run.

## Decisions (all deterministic, journaled once with a `key`)

- Plan admitted: only after the validator passed. Tasks form a linear chain.
- Replan: only when a task is BLOCKED (its remaining planned tasks are superseded), or the chain finished and the final gate FAILS.
  At most `max_replans` (default 1). A coding failure is never a replan; Step 4 retries it.
- Review-driven fix: only for blocking findings that cite a criterion and evidence; at most `review.max_fixes` (default 1), then a second review.
  Findings still open after that are recorded and the final status becomes `complete_with_open_findings`. They never loop.
- Tester proposals are EVIDENCE ONLY. They run against the accepted code and are recorded; a failing one is a warning.
  They become trusted tests only through a human-approved, hash-frozen promotion (loop criteria section 4).
- Completion: the policy's `final_gate` profile (controller-owned tests) must PASS on the final accepted head. An agent saying "done"
  counts for nothing. Status `complete` also requires no open findings and no warnings.
- Stop reasons: `replan_budget_exhausted:*`, `plan_failed:PLAN-n`, `final_gate_error:*`, `invocation_budget_exhausted`,
  `budget_exhausted`, `trusted_tests_modified`, `infra_error`, `quarantined`.

## Budgets (policy `budgets`)

Step 4: `max_attempts`, `agent_timeout_s`, `verify_timeout_s`, `verify_retries`. Step 5: `max_tasks` (6), `max_replans` (1),
`max_invocations` (40, counts every agent run), `max_total_runtime_s` (7200), `proposal_timeout_s` (120).
Docker Agent does NOT enforce budgets for harness agents; these are the controller's.

## Token cost knob

`review.per_task`: `all` (reviewer + tester after every coder task), `flagged` (only when a changed file is under
`review.flagged_paths`), or `none`. `review.final` adds one cumulative review after the final gate. For the DOOM run prefer
`flagged` plus `final`.

## Evidence

`artifacts/runs/<run_id>/`: `report.json`, `summary.md`, `handoffs/*.json` (plan, reviews, tester reports), `policies/`, `verify/`,
`feedback/`. Journal events: `plan_admitted`, `task_accepted`, `review_done`, `review_unresolved`, `tester_evidence`,
`replan_triggered`, `task_dropped`, `final_gate`, `coordinator_stopped`. Accepted commits: `refs/loop/accepted/<run>/<task>`.

## Recovery

The Coordinator is a pure function of (policy, journal, accepted commits). Re-running it replays from the start: tasks that are
VERIFIED or BLOCKED are skipped, handoffs are re-read from their accepted commits, decisions are not journaled twice.
Do not change the policy between restarts.

## Mocked vs real

`tests/test_loop_coordinator.py` and `demo_step5.py` use `tests/fake_role_cli.py` (scripted agents). Real and tested here: the
Coordinator, controller, worktrees, verify.py, validators, accepted refs, journal. NOT tested here: real Claude Code agents.

## Limits

- Strictly sequential; linear plans only; no merge of independent tasks.
- Reviewer findings are checked for shape (criterion + evidence), not for truth.
- Tester-proposed tests run on the host (see loop/sandbox.md for the host-agent limitation). For DOOM, wrap `proposed_test_argv` with `loop/sandbox_verify.py run`.
- Host agents are not isolated beyond worktrees and the controller's post-run checks.
