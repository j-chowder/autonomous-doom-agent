# DOOM WebAssembly Port: Acceptance Contract

**Version:** 1.0-draft (awaiting human approval; thresholds below are FROZEN by the student on 2026-11-04)
**Owner:** controller / human. Coders and QA may not edit this file.
**Supersedes:** project docs "loop criteria" v0.1 and "loop hooks" v0.1 (same IDs). Items marked **[PROPOSAL]** are new and need approval.

Scope, criteria IDs and negative-control intent come from "loop criteria". Hook semantics come from "loop hooks".
Feasibility of all six hooks was checked against `third_party/doomgeneric` (see section 2).

## 1. Frozen thresholds

| Parameter | Value | Note |
|---|---|---|
| Browser / toolchain | Playwright 1.56.1 Chromium, Emscripten 4.0.18 | the root `Dockerfile` image |
| Startup timeout | 20 s | navigation start to first `GetGameTic() > 0`. Measured alone. |
| Nominal rate | 35 tics/s | `TICRATE` in `doomdef.h` |
| Accepted rate | 26.25 to 43.75 tics/s (+/-25%) | |
| Warmup before timing | 2 s after the first tic **[PROPOSAL]** | not part of startup and not part of the rate window |
| Rate window, fast gates | 10 s | |
| Rate window, final gate | 60 s | |
| Max heartbeat gap | 1000 ms | provisional; validate on the first passing candidate |
| Coding attempts per task | 3 | |
| Infrastructure retries per check | 2 | |
| Concurrent coders | 1 | |

Rate = `delta(GetGameTic) / delta(performance.now())`, both read in the page at the window start and end.
It is never taken from the engine's own clock alone.

## 2. Hook feasibility (checked against the supplied engine)

| Hook | Engine source | Result |
|---|---|---|
| `DG_TEST_GetGameTic` | `int gametic` (`d_loop.c:72`), incremented in `TryRunTics` (`d_loop.c:812`) | feasible |
| `DG_TEST_GetFrameCount` | no engine counter; platform counts completed `DG_DrawFrame` calls | feasible, in `platform/` |
| `DG_TEST_GetPlayerX/Y` | `players[consoleplayer].mo->x / ->y` (`p_mobj.h:207`) | feasible |
| `DG_TEST_GetViewAngle` | `players[consoleplayer].mo->angle` (`p_mobj.h:216`) | feasible |
| `DG_TEST_GetAmmo` | `players[consoleplayer].ammo[weaponinfo[readyweapon].ammo]` (`d_player.h:115`, `d_items.h:30`); fists have no ammo type, so return 0 | feasible |

Hook rules: getters only, in one file `platform/dg_test_hooks.c`, no synthetic counters, no new `DG_TEST_*` export beyond these six.

## 3. Candidate interface (what the trusted tests rely on) **[PROPOSAL]**

Tests are independent of how the coder structures the code. They rely only on this interface.

1. `bash build.sh` at the repository root produces the static site in `dist/doom/`. It needs no network.
   It deletes `dist/` and `build/` first. A clean build is the only build the gates accept.
2. `dist/doom/index.html` is the entry page. `dist/doom/` holds exactly one `*.wasm` file and every file the page needs, including the WAD data.
3. The page exposes the Emscripten module as the global `Module` (no `MODULARIZE`). Hooks are `Module._DG_TEST_*`.
4. The game draws to `<canvas id="canvas">` at 640x400.
5. The page starts the engine by itself. It honors these query parameters, which become standard DOOM arguments:
   `warp=E1M1` (`-warp 1 1`), `skill=N` (`-skill N`, default 3), `nomonsters=1` (`-nomonsters`).
   Without parameters the page shows the title and menu. Tests use `?warp=E1M1&nomonsters=1` unless the criterion says otherwise.
6. The site works from any static server under a subpath (`/doom/`), with no special response headers (so no SharedArrayBuffer or threads).
7. Keys: ArrowUp/ArrowDown move, ArrowLeft/ArrowRight turn, Control fires, Shift runs, Space uses, Enter and Escape drive the menu.
   Input works without a click first (a click may be needed only to start audio).

## 4. Trusted test runner contract **[PROPOSAL]**

QA writes everything under `tests/doom/trusted/`. Entry point: `tests/doom/trusted/run.sh GATE`.
Inside the verification container the candidate is read-only at `/workspace`, the tests are read-only at `/trusted`, and `/out` is writable.
`run.sh` copies `/workspace` into `/out/work`, builds there, serves `dist/` itself on loopback, and drives Chromium.

- Output: one line per assertion, `ASSERT <ID> PASS|FAIL|ERROR <short reason>`, plus `/out/results.json`
  (candidate SHA from env `CANDIDATE_SHA` if set, gate, assertion list, measurements, paths to logs and screenshots).
- Exit code: 0 all PASS; 1 any FAIL; 70 no FAIL but at least one ERROR (infrastructure or unmet precondition, per loop criteria section 4).
- A precondition that cannot be set up (for example the player is blocked by a wall) is ERROR, not FAIL.
- Every assertion that cannot run because an earlier one failed prints `ERROR <ID> skipped: <blocking ID> failed`. It never prints PASS.
- Tests may not use the network. They may not hide a failure by catching it.

### Gates (cumulative; each includes the one above)

| Gate | Adds |
|---|---|
| `build` | BUILD-01 |
| `boot` | LOAD-01, WAD-01 |
| `m1` | STABILITY-01 (short), DEPLOY-01. **This is milestone 1.** |
| `video` | VIDEO-01 |
| `time` | TIME-01, TIME-02 (10 s window) |
| `input` | INPUT-01, INPUT-02, INPUT-03 |
| `audio` | AUDIO-01 |
| `final` | 60 s TIME window, 60 s soak in STABILITY-01, and all negative controls (`controls`) |

`controls` also runs alone: `run.sh controls`. PLAY-01 is not automated here (agent playtest plus human acceptance).

## 5. Assertions (stable IDs)

Each ID must appear in the tests and in the output. QA may add IDs but may not drop or rename these.

| Assertion | Check |
|---|---|
| BUILD-01.a | `bash build.sh` exits 0 from a clean tree |
| BUILD-01.b | `emcc --version` reports 4.0.18 |
| BUILD-01.c | `dist/doom/index.html` and exactly one `*.wasm` exist |
| BUILD-01.d | `WebAssembly.validate()` is true for the wasm |
| BUILD-01.e | the wasm exports all six `DG_TEST_*` and no other `DG_TEST_*` (read from the binary, not the page) |
| LOAD-01.a | `GetGameTic() > 0` within the 20 s startup timeout |
| LOAD-01.b | the canvas is not blank (not one uniform color) |
| LOAD-01.c | no fatal error before the timeout |
| WAD-01.a | with `?warp=E1M1`, player X/Y at the first tic equal the E1M1 player-start THING in `assets/doom1.wad` |
| VIDEO-01.a | every canvas pixel has alpha 255 |
| VIDEO-01.b | known E1M1 regions match WAD-derived reference colors (PLAYPAL; status bar patch) within a tolerance QA derives and documents |
| VIDEO-01.c | `GetFrameCount()` rises and the canvas content changes while the player turns |
| TIME-01.a | tic rate is inside 26.25 to 43.75 over the window |
| TIME-02.a | the largest `requestAnimationFrame` gap in the window is at most 1000 ms |
| TIME-02.b | an external watchdog still gets an answer from the page, or reports FAIL at its own timeout |
| INPUT-01.a to .f | forward, back, turn left, turn right, fire, run each cause the expected state change, measured over game-tic windows |
| INPUT-02.a | after key release, motion stops as in the held-key control comparison |
| INPUT-03.a to .c | arrow/space keys do not scroll the page; focus is kept; keys release on window blur |
| AUDIO-01.a | an `AudioContext` exists and is running after a key press |
| AUDIO-01.b | non-silent audio reaches the output after firing |
| STABILITY-01.a | zero page crashes, uncaught exceptions, unhandled rejections or aborts across all gate runs |
| STABILITY-01.b | the 60 s soak ends with tics still rising (final gate) |
| DEPLOY-01.a | served under `/doom/`, the wasm has MIME `application/wasm` |
| DEPLOY-01.b | LOAD-01.a and WAD-01.a hold under the subpath |

Thresholds not fixed here (movement distances, run ratio, pixel tolerance, silence level) must be derived by QA from the engine source,
the WAD, or a controlled baseline. Each derived value needs a comment naming its source. No value may be invented silently.
Input tests need a known open position; QA picks it from the E1M1 map data and documents it.

## 6. Negative controls (black-box fault injection) **[PROPOSAL]**

Faults are applied to a disposable copy of `dist/doom/` or inside the browser (`addInitScript`, request interception). They never depend on the coder's source layout.
Each control passes only if the named assertion FAILS and no other infrastructure ERROR explains it.

| Criterion | Fault | Must fail |
|---|---|---|
| BUILD-01 | truncate the wasm; replace it with a wasm that lacks the hook exports | BUILD-01.d; BUILD-01.e |
| LOAD-01 | block the wasm request | LOAD-01.a |
| WAD-01 | delete the WAD data files from the copy | WAD-01.a |
| VIDEO-01 | wrap canvas drawing to swap red/blue; to zero alpha | VIDEO-01.b; VIDEO-01.a |
| TIME-01 | freeze `performance.now`; scale it by 2x | TIME-01.a |
| TIME-02 | inject a 3 s busy wait in the page | TIME-02.a or TIME-02.b |
| INPUT-01 | drop key-down events; swap two key codes | INPUT-01.* |
| INPUT-02 | drop key-up events | INPUT-02.a |
| INPUT-03 | make `preventDefault` a no-op; ignore blur | INPUT-03.a; INPUT-03.c |
| AUDIO-01 | make `AudioContext` output nothing | AUDIO-01.b |
| STABILITY-01 | throw an uncaught exception after load | STABILITY-01.a |
| DEPLOY-01 | serve the wasm as `text/plain`; break asset paths | DEPLOY-01.a; DEPLOY-01.b |

## 7. Review and acceptance policy

- Coder tasks are verified by the cumulative gate named in the plan (`build`, `boot`, ..., `final`).
- Review is **flagged**: a reviewer and tester run only when a task changes `platform/dg_test_hooks.c` or `build.sh`.
  One mandatory cumulative review runs after the final gate. Remaining blocking findings give `complete_with_open_findings`, never `complete`.
- Completion needs the `final` gate PASS on the final head, with evidence: candidate SHA, test hash, image ID, assertion IDs, command, result.
- The student accepts after a human playtest (PLAY-01). Automated PASS is necessary, not sufficient.
- Known limits: hooks can be implemented deceptively; frame changes may come from animation; audio loudness is not judged; one browser, scripted scenarios only.
