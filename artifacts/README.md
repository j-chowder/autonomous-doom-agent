# Evidence

Curated records from the real runs. Large logs, preserved worktrees and build copies are not included; the full set was kept locally.

| Path | Contents |
|---|---|
| `runs/20261104T174843Z-d39cff/` | Port from scratch: plan, 5 tasks, reviews, tester reports, feedback, final gate |
| `runs/20261104T185606Z-3582b6/` | Regression 1 repair (startup) |
| `runs/20261104T202813Z-ed0024/` | Regression 2 repair (keyboard) |
| `runs/20261104T090707Z-ec79b6/` | QA test-authoring task. Ended BLOCKED; see `tests/doom/QA-PROVENANCE.md` |
| `sandbox-runs/20261104T185254Z-c6cc6167/` | Trusted `boot` gate failing on injected regression 1 |
| `sandbox-runs/20261104T202103Z-326e0efa/` | Trusted `input` gate failing on injected regression 2 (forward movement -48.0) |
| `sandbox-runs/20261104T202936Z-8867be80/` | A passing gate run during the regression 2 repair, with screenshots |
| `doom/*.json` | Policies used for the three runs (roles, allowed paths, budgets, test hashes) |

Each run folder has `summary.md` (human-readable) and `report.json` (full event journal). Absolute paths in these files refer to the original machine.
