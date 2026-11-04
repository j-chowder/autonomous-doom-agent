# Provenance of the trusted DOOM tests

**The original QA-AUTHOR controller task did NOT succeed. Its final state is BLOCKED.** Do not cite it as a passing controller run.

| Item | Fact |
|---|---|
| Controller run | `artifacts/runs/20261104T090707Z-ec79b6` (task `QA-AUTHOR`, 3 attempts) |
| Attempt 2 | agent finished; verification FAIL `scope_violation`. Only cause: Python bytecode (`loop/__pycache__/*.pyc`, gitignored) written by running `python3 loop/doom_setup.py lint`. No test file was out of scope. |
| Attempt 3 | agent exited nonzero; task BLOCKED (`attempt_budget_exhausted:agent_nonzero_exit`) |
| Recovery | the files in `tests/doom/trusted/` were recovered manually from the preserved work of attempt 2 (`preserved/QA-AUTHOR-a2`) |
| Human review | the student reviewed all 18 files: `node --check` and `bash -n` pass; a simulated missing-build run returned BUILD-01.a FAIL with `results.json` |
| Changes after review (2) | `groups/audio.js`: AUDIO-01.a and .b are judged after the first sound-triggering key press (fire). `lib/faults.js`: VIDEO-01 faults count upload-path applications and fall back to read-back corruption, so the fault is always applied. Nothing else was changed. |
| Not yet done | no Chromium/DOOM run of these tests; the red baseline against the empty port is the first container run |

The `scope_violation` also explains the earlier tester and final-reviewer failures: the cause is the bytecode, not role boundaries.
The coordinator now sets `PYTHONDONTWRITEBYTECODE=1`; the QA task was started directly with the controller, which did not.
