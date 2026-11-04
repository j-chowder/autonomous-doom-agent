# Sandbox (Tier 1): containerized verification

Status: DECIDED (Option A). Verification container: implemented, unit-tested, and demonstrated on the Mac
(`python3 tests/demo_sandbox_verify.py --build`: PASS / FAIL / ERROR as expected, per the user). Agent sandbox (Phase 2): stopped.

## What runs where

| Piece | Runs | Isolation |
|---|---|---|
| Docker Agent + Claude Code harness (coder, reviewer, ...) | Host, in a git worktree (unchanged) | None beyond the worktree. Not a security boundary. |
| Trusted verification (compile, Playwright, negative controls) | Toolchain container from the root `Dockerfile` | See below |

The agent is NOT sandboxed in Tier 1. Reason: the supplied sandbox rules want no credentials in the sandbox and
Docker Agent's `--sandbox` does not carry the `claude` login. Tier 2 (agent in `sbx`) is a separate feasibility test.

## Container policy (`loop/sandbox_verify.py run`)

| Item | Setting |
|---|---|
| Candidate source | read-only at `/workspace` (verify.py's exported fingerprint copy) |
| Frozen tests | read-only at `/trusted` (controller-owned; keep them hash-listed in `test_files`) |
| Output | read-write at `/out` = `artifacts/sandbox-runs/<run>/` |
| Network | `--network none` |
| Privileges | `--cap-drop ALL --cap-add DAC_OVERRIDE`, `no-new-privileges`, `--init`, memory 4g, pids 1024, shm 1g |
| Host env / `$HOME` / `~/.claude` / docker socket | none passed or mounted |
| Image | referenced by content ID; the ID is written to `sandbox.json` and the verify log |

Exit codes: test 0 -> PASS; other test failure -> FAIL; docker failure 125, in-container timeout 124, killed 137,
missing docker/image -> 70 = ERROR.

## Network exceptions (documented on purpose)

1. Image build (`sandbox_verify.py build`) needs the network: base image, apt, npm, Chromium download. Build-time only.
2. The host agent (Docker Agent + Claude Code) has open network. Tier 1 does not restrict it.
3. Runtime verification has no exceptions.

## Wiring a task (controller JSON; Steps 1-4 code unchanged)

`test_argv`: `["python3", "<abs>/loop/sandbox_verify.py", "run", "--trusted", "<abs>/tests/<frozen dir>", "--timeout", "240", "--", "<cmd inside container>"]`

Keep `--timeout` below the controller's `verify_timeout_s`. Add the wrapper and each trusted file to `test_files` so the
controller hash-checks them before use.

## Known limits / unverified

- Chromium and emcc under `--cap-drop ALL` are untested on the Mac. emcc may rebuild its cache on every `--rm` run (slow first compile).
- Evidence links verify.json to sandbox.json only through the verify log line `SANDBOX_EVIDENCE <path>`.
- Container output on a Linux host would be root-owned; fine on Docker Desktop for Mac.

## Decision: Option A (2026-11-04)

Coding, planning, reviewing and testing agents run ON THE HOST (Docker Agent + Claude Code harness, subscription login) in
per-attempt git worktrees. Compilation and trusted browser verification run in the restricted course-toolchain container.
Phase 2 stopped: the Docker Agent sandbox has no `claude` CLI, and merging the Docker Agent kit with the Claude mixin fails on
a duplicate `com.docker.sandbox/credential@1` owner. Fixing that needs a custom kit, which is out of scope.
Record: `artifacts/sandbox/phase2-result.md`.

### Known host-agent isolation limitation (explicit)

- A harness agent runs with permission prompts bypassed, as your OS user, with open network.
- A worktree protects the main checkout's working files. It is NOT a security boundary: the agent can still read or write
  outside the worktree, including the shared `.git` (e.g. `refs/loop/accepted/*`) and `$HOME`.
- What the controller does enforce, after each run and independently of the agent: scope checks against allowed and
  protected paths (including ignored files), trusted-test hash checks, fingerprint-stable verification, and preservation or
  quarantine of failed work. Read-only roles (planner, reviewer, tester) are enforced the same way: only their handoff file
  may change.
- Accidental damage is covered by those checks; a deliberately hostile agent is not. Do not run untrusted prompts or repositories.
