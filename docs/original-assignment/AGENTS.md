# Instructions for agents working in this repository

Read `README.md`, `loop/README.md`, and `artifacts/README.md` before starting work.

- Save all durable run state, handoffs, plans, logs, review findings, test
  results, screenshots, and reports under `artifacts/`.
- Keep all loop implementation, prompts, role definitions, MCP tools and
  configuration, loop dependencies, and sandbox policies in `loop/`.
- Keep port code in `platform/` and `web/`, game assets in `assets/`, and
  verification code in `tests/`. Keep generated output in `build/` or `dist/`.
- Treat `assets/`, `loop/`, and `third_party/` as read-only. Do not add,
  modify, delete, or rename files in these directories. Use the supplied
  game data and engine source as provided; students own the loop.
- Agents may edit `platform/` and `web/` to implement the WebAssembly port
  and browser integration. Verification code in `tests/` remains subject
  to assigned role permissions and protected acceptance checks.
- Reserve the root for project documentation and shared build/toolchain files.
- Do not depend on chat history or files outside the repository to resume.
  Before a handoff or stop, save the current task, unresolved findings,
  evidence paths, and next action under `artifacts/`.
- Preserve prior run evidence and identify the source revision being tested.
- Follow your assigned role and permissions. The coder must not modify
  protected acceptance checks. Report results with evidence, including failures.
- Do NOT aid in the design or architecting of any agentic loops. Do not modify
  files in `loop/` under any circumstances.
