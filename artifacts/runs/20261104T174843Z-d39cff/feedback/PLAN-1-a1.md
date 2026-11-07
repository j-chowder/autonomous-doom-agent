Attempt 1 failed: verify_fail:test_failed
Independent verification: FAIL (test_failed)
The trusted test command exited 1. Output tail:
INVALID planner handoff handoff/PLAN-1.json:
  - tasks[0].coder_role 'coder' is not allowed; choose one of: integrator, wasm_platform, web_frontend
  - tasks[1].coder_role 'coder' is not allowed; choose one of: integrator, wasm_platform, web_frontend
  - tasks[2].coder_role 'coder' is not allowed; choose one of: integrator, wasm_platform, web_frontend
  - tasks[3].coder_role 'coder' is not allowed; choose one of: integrator, wasm_platform, web_frontend
  - tasks[4].coder_role 'coder' is not allowed; choose one of: integrator, wasm_platform, web_frontend

Evidence: /Users/jadenc/Desktop/bse/capstone/artifacts/runs/20261104T174843Z-d39cff/verify/PLAN-1-a1-v0
Preserved work: artifacts/runs/20261104T174843Z-d39cff/preserved/PLAN-1-a1
