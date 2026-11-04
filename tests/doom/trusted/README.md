# Trusted DOOM acceptance tests

Written by the independent QA author from `tests/doom/ACCEPTANCE.md` (the contract), `third_party/doomgeneric` and
`assets/doom1.wad` only. Nothing here depends on how a coder structures the port.

**Status of this code:** it was written without Chromium or the Emscripten toolchain. It has been checked with `bash -n`,
`node --check`, a smoke run of `run.sh boot` against a stub build (build steps, WAD/wasm parsing and result plumbing work),
and by evaluating the in-page probe and the fault scripts in a stub JS context. **No browser assertion has ever executed
against a real page.** The first run on a real candidate is also the first real test of these tests; see "Risks" below.

## Running

```
tests/doom/trusted/run.sh GATE      # build | boot | m1 | video | time | input | audio | final | controls
```

In the verification container the candidate is at `/workspace` (read-only), these files at `/trusted`, output in `/out`.
For local runs set `WORKSPACE=<repo> OUT=<dir>`. `run.sh` copies the candidate (without `.git`, `dist`, `build`, `artifacts`)
to `$OUT/work`, runs `bash build.sh` there, serves `$OUT/work/dist` on `127.0.0.1` (so the game is at `/doom/index.html`),
and drives Chromium through `require('playwright')`. Every page request that is not to that loopback server is aborted and logged
(`page_events.blocked_network` in `results.json`).

Output: one `ASSERT <ID> PASS|FAIL|ERROR <reason>` line per assertion, a `SUMMARY` line, and `$OUT/results.json`
(gate, candidate SHA from `CANDIDATE_SHA` or `git rev-parse`, hash of these test files, `IMAGE_ID` if set, WAD sha256, browser
version, assertions, measurements, page errors, paths of logs and screenshots in `$OUT/logs`, `$OUT/screenshots`).
Exit 0 all PASS, 1 any FAIL, 70 no FAIL and at least one ERROR. An assertion that cannot run prints
`ERROR <ID> skipped: <blocking ID> failed` (or `did not pass (ERROR)`); anything never reached is closed out as `ERROR not run: ...`.
Exceptions inside a test become ERROR, never PASS. Every wait has a timeout; each gate has a wall-clock budget
(`BUDGET_S` in `main.js`, the container limits from `loop/doom_setup.py` minus 60 s) after which remaining groups are skipped as ERROR.

Layout: `main.js` (orchestration, gates), `controls.js` (negative controls), `groups/*.js` (assertions), `lib/*.js` (server,
in-page probe, WAD and wasm readers, movement model, fault injection), `derive.js` (re-derives the numbers below).

## Frozen thresholds (contract section 1, used exactly)

| Value | Where it is used |
|---|---|
| startup timeout 20 s, navigation start to first `GetGameTic() > 0` | `lib/site.js openGame`, LOAD-01.a |
| tic rate 26.25 to 43.75 tics/s | `groups/time.js RATE_LO/RATE_HI` |
| warmup 2 s after the first tic | `groups/time.js WARMUP_MS`, LOAD-01.b, VIDEO-01 |
| rate window 10 s (gates up to `audio`), 60 s (`final`) | `main.js` (`windowMs`) |
| max rAF gap 1000 ms | `groups/time.js MAX_GAP_MS` |
| infrastructure retries (2) and coding attempts (3) | controller policy, not implemented in these tests: a check that hits infrastructure trouble reports ERROR and the controller retries |

Rate is `delta(GetGameTic) / delta(clock)` read inside the page. TIME-01.a is computed on **two** clocks and both must be in range:
page `performance.now()` (as the contract says) and a copy of `performance.now` captured by the probe before any page script ran.
Without the second clock, a fault that scales time for both the engine and the test (control "scale by 2x") would go unnoticed.

## Derived values

### E1M1 start (WAD-01.a, WAD-01.b)
- Source: `assets/doom1.wad`, lump `E1M1/THINGS`, first THING with type 1: x=1056, y=-3616, angle 90.
- Expected raw hook values: `x = 1056*65536 = 69206016`, `y = -3616*65536 = -236978176` (exact equality; `mo->x/->y` are 16.16 fixed,
  `P_SpawnPlayer` sets them to `mthing->x << FRACBITS`, no tic has moved the player without input).
- Added **WAD-01.b**: `GetViewAngle` at the first tic equals `ANG45 * (90/45) = 0x40000000 = 1073741824` (`P_SpawnPlayer`).
  It is not in the contract; the angle hook and the facing direction used by the movement tests depend on it.

### Open position for the input tests
- The player start itself. Facing 90 degrees (north). Computed by `lib/wad.js freeDistance` from `E1M1/LINEDEFS`, `VERTEXES`, `SIDEDEFS`,
  `SECTORS` (a line blocks if one-sided, flagged impassable, floor step > 24 or opening < 56; rays at -16, 0, +16 lateral for the radius).
- North: 736 units to the wall at y=-2880 (linedef 44), 720 after the player radius of 16. South: 64 units (48 usable) to the wall at y=-3680.
  No blocking THING lies in the strip x 1040..1072 (the nearby barrel is at x=1152). `main.js` recomputes these at run time and ERRORs
  (`reference setup failed`) if the geometry or angle differs.
- Every input test uses a fresh page at the start, after settling (1.5 s plus 35 tics) with no input sent. If the player is not exactly at the
  start before the test begins, the result is ERROR (precondition), never FAIL. The pass/fail verdict never depends on a blocked path: forward
  runs stay below ~500 units, the 8-tic backward window stays below 48 units.

### Movement model (INPUT-01.a, .b, .f; INPUT-02.a; INPUT-03.c)
- Walk command `forwardmove[0] = 0x19`, run `forwardmove[1] = 0x32` (`g_game.c:156`); thrust per tic = `cmd * 2048 / 65536` units (`p_user.c`
  `P_Thrust`); `FRICTION = 0xe800/65536 = 0.90625` and `STOPSPEED = 0x1000` (`p_mobj.c:105-106`); `MAXMOVE = 30` (`p_local.h:51`).
  Per tic: `mom += push; pos += mom; mom *= FRICTION` (`P_MovePlayer`, then `P_XYMovement`). Implemented in `lib/model.js moveDisp`.
- Values from `derive.js`: walk 22.8 units after 8 tics, 97.4 after 20, 173.6 after 30; run 347.3 after 30; steady speed 8.33 (walk) and 16.67 (run) units/tic.
- **Distance window: 0.5 x model(nT - 3) to 1.5 x model(nT)** (`LO`, `HI` in `groups/input.js`), where `nT` is the number of game tics
  between the snapshot before key-down and the snapshot at the end of the hold. The 3-tic allowance: the key event is consumed at the next
  `I_StartTic`/`G_BuildTiccmd`, plus the gap between the snapshot and `keyboard.down`, plus one tic of polling; 3 tics cost 13.6% of a 30-tic
  distance, 50% is a wide margin on top. Lateral drift must be <= 10% of the forward distance + 2 units.
- Forward (a): 30 tics. Backward (b): 8 tics only (48 units are free behind the start); needs <= -0.5 x model(5) = about -5 units.
- Hold-and-coast: after release, momentum falls below STOPSPEED in 45 tics from a 12-tic walk, so the "stopped" tests wait 50 tics, then measure 10.

### Run ratio (INPUT-01.f)
- Source: `forwardmove[1] / forwardmove[0] = 0x32 / 0x19 = 2.0` (`g_game.c:156`). The model is linear in the push, so run/walk is exactly 2.000 for every n.
- Measured as `(D_run / D_walk) * (walk(nT_walk) / walk(nT_run))` from two fresh pages, 30 tics each (tic-normalized so unequal windows do not matter).
- **Accepted: 1.6 to 2.4** (2.0 +/- 20%). One tic of lag changes a 30-tic distance by 4.5%, so two tics of difference between the two runs is about 9%; 20% leaves room.
  A run key that does nothing gives 1.0; the swapped-key control gives about 1.0.

### Turn angle (INPUT-01.c, .d)
- `angleturn[0] = 640` for a held turn key, `angleturn[2] = 320` for the first `SLOWTURNTICS - 1 = 5` tics (`g_game.c:158, 193, 360-363`);
  `P_MovePlayer` adds `angleturn << 16` to the BAM angle (`p_user.c:147`). 2^32 BAM = 360 degrees. `lib/model.js turnDeg`: 20 tics = 61.5 degrees.
- **Accepted: 0.6 x turnDeg(nT - 3) to 1.4 x turnDeg(nT)**; left (ArrowLeft) is positive, right negative. The wide band covers the `angleturn` rounding
  at `g_game.c:583-595` and input lag (3 tics are 17% of a 20-tic turn).

### Fire (INPUT-01.e, AUDIO-01.b)
- Start state: pistol ready, 50 bullets (`g_game.c:1100`, `deh_initial_bullets`). One shot costs one bullet (`A_FirePistol`).
  Pistol cycle 4+6+4+5 = 19 tics (`info.c` S_PISTOL1..S_PISTOL4), first shot after about 5 tics.
- Holding Control for 40 tics must reduce ammo by at least 1 and at most `floor(nT / 19) + 1`. Precondition: ammo > 0 before firing, otherwise ERROR.

### INPUT-02.a and INPUT-03.c "motion stops"
- Held-key control: speed over tics 5..11 of the hold must be >= 1 unit/tic (else ERROR; expected about 4). After release (or after the dispatched
  `blur`), wait 50 tics and measure the next 10: movement must be <= 1.0 unit **and** <= 5% of what the control predicts (about 40 units). Real stop is exactly 0
  (`P_XYMovement` zeroes momentum below STOPSPEED). A stuck key moves about 80 units in the window and stays inside the 720 free units.

### Pixel tolerance and references (LOAD-01.b, VIDEO-01.a, .b, .c)
- Canvas is 640x400; each Doom pixel is a 2x2 block (`i_video.c:277-282`, auto-scaling `fb_scaling = 640/320 = 2`).
- Palette: `PLAYPAL` palette 0 after the engine's gamma step `gammatable[usegamma=0][v]` (`i_video.c:122, 408-412`; `tables.c:2129+`, read from the candidate
  copy of `third_party`, which is supplied read-only). Palette 0 is correct at level start (`st_stuff.c:934-996`, no damage or pickup).
- **Tolerance: 2 per channel (0..255).** The pipeline is integer and should be exact; 2 only absorbs one rounding step in a colour-managed or
  premultiplied path. It is far below the signal: among the HUD glyph pixels that an R/B swap would move, the smallest |R-B| is 67 (`derive.js`).
- VIDEO-01.b passes only if all three hold:
  1. **View region** (canvas rows 0..335 = 168 Doom rows, `screenblocks = 10`, `m_menu.c:78`): at least **99%** of pixels are some palette-0 colour. Fraction chosen to allow for
     a few non-palette pixels from the weapon flash or a mid-transition frame; an R/B swap keeps only 58 of 256 palette entries in the palette.
  2. **Status bar background**: the 3168 opaque `STBAR` pixels that none of the widgets can overwrite (rectangles from the `ST_*X/Y` constants,
     `st_stuff.c:84-215`, listed in `lib/wad.js BAR_OVERLAYS`) match at least **99%**. Note these pixels are all gray (max |R-B| = 0), so this part alone cannot see an R/B swap; it detects wrong geometry, wrong palette and alpha.
  3. **HUD glyphs**: ammo `50`, health `100%`, armor `0%` (all at level start with the pistol ready; `STlib_drawNum`, `st_lib.c:77-120`; `STTNUM0-9`, `STTPRCNT` patches with their offsets)
     give 1349 red pixels, 85.4% of which have |R-B| > 2. At least **95%** of the 2x2 blocks must match; the best of 9 one-Doom-pixel shifts is used to absorb
     an off-by-one in this derivation. This is the part that catches red/blue swaps.
- VIDEO-01.a: 640x400 and every pixel has alpha 255. The canvas is read through a scratch 2D canvas (`drawImage`), so 2D and WebGL canvases both work;
  a WebGL canvas without `preserveDrawingBuffer` is read inside a `requestAnimationFrame` as a second attempt.
- VIDEO-01.c: turn left for 20 tics. `GetFrameCount` must rise by at least `max(3, floor(nT/4))` and at least **5%** of the view pixels must change (a 60 degree turn changes most of them).
  The frame minimum is a liveness bound (a render per tic would give 20), not a frame-rate claim.
- LOAD-01.b: at least 2 distinct colours (contract wording "not one uniform color"), sampled up to 4 times over 3 s after the 2 s warmup.

### Silence level (AUDIO-01.b)
- The probe wraps `AudioNode.connect` so every connection to a destination goes through an `AnalyserNode` (fft 16384, polled every 25 ms).
- **Silence: peak sample magnitude < 0.001 (-60 dBFS).** It is a convention, not an engine value: 33 times one 16-bit LSB (1/32768), so quantization
  noise cannot pass, and well below any sampled effect (DMX sounds are 8-bit, one LSB is 1/128 = 0.0078 before volume scaling; default `sfxVolume = 8`, `s_sound.c:86`).
  Loudness is not judged (contract). The peak before firing is recorded for the evidence but the pass condition is the post-fire peak only, so music alone can satisfy it (known limit).
- AUDIO-01.a: after a `Shift` key press (no game action), some `AudioContext` created by the page has `state === 'running'` within 3 s.

### Other derived values
- Watchdog (TIME-02.b): a page ping every 250 ms with a 1000 ms timeout, equal to the contract heartbeat bound. Any unanswered ping is FAIL; at least 3 pings required.
- Soak liveness (STABILITY-01.b): over the last 10 s the tic advances at least 175 tics (50% of 350). It is a "still rising" test, not a rate test; TIME-01.a owns rate accuracy.
- STABILITY-01.a: a dedicated 8 s page plus every page opened during the run; any `pageerror`, crash, unexpected close, `window.onerror`, unhandled rejection, or console
  error matching `Aborted(`, `RuntimeError:`, `I_Error`, `program exited (with status: N)`, `Error:` counts.

## How the assertions interpret the contract (read before disputing a result)

- Everything is served and tested under `/doom/`. **DEPLOY-01.a** reads the `Content-Type` of the `.wasm` response the page actually received
  (`application/wasm` and status 200). **DEPLOY-01.b** is the conjunction "LOAD-01.a and WAD-01.a hold on this `/doom/` load, and no asset request failed (404 or network error, favicon excluded)";
  it reports FAIL, not "skipped", when either does not hold. Only the `/doom/` mount is tested, not an arbitrary deeper prefix.
- **WAD-01.a** with no first tic: FAIL if there is evidence the WAD data was unavailable (a non-wasm request failed, or a console error mentions the WAD or a missing file); otherwise ERROR skipped. This
  is what lets the "delete the WAD data" control fail WAD-01.a as the contract requires.
- **TIME-02.a** is measured by the probe's own `requestAnimationFrame` loop (real clock), including the gap between the last callback and the window end.
- **INPUT-03.a** needs two things: no scroll after real ArrowDown, ArrowUp, Space, ArrowRight, ArrowLeft presses on a page made scrollable by injecting a tall invisible element (the page's own overflow
  rules are untouched), **and** `preventDefault` called for synthetic cancelable keydown events of those keys on the active element (so a page that cannot scroll is still checked).
  **INPUT-03.b**: `document.activeElement` is unchanged and `document.hasFocus()` stays true. **INPUT-03.c**: `window.dispatchEvent(new Event('blur'))` while ArrowUp is physically down must stop the motion.
- The startup wait ends early (after 1.5 s) when an abort, crash or a failed `.js/.wasm/.data/.wad/.html` request is seen, so controls do not burn 20 s each; LOAD-01.a is still FAIL.
- Hooks are read as `Module._DG_TEST_*` on the global `Module` and must return plain integers; X/Y are raw 16.16 fixed values; the angle is read as unsigned 32-bit.

## Negative controls (`controls.js`, run in `final` and by `run.sh controls`)

Each control copies `dist/doom` to `$OUT/controls/<name>/doom`, applies one fault, serves that copy, runs the **same** assertion code (10 s windows) and passes only if every named assertion is FAIL
(TIME-02: either). It is ERROR if the named assertion errors, or if the same assertion does not PASS on the unfaulted candidate (baseline: taken from the same run when available, otherwise run first). Output id: `CONTROL-<name>`.

| Control id | Fault | Must be FAIL |
|---|---|---|
| BUILD-01.truncate-wasm | wasm cut to half | BUILD-01.d |
| BUILD-01.no-hook-exports | wasm replaced by the 8-byte empty module | BUILD-01.e |
| LOAD-01.block-wasm-request | request interception aborts `*.wasm` | LOAD-01.a |
| WAD-01.delete-wad-data | files containing the WAD bytes are deleted (or zero-filled if embedded in js/wasm/html) | WAD-01.a |
| VIDEO-01.swap-red-blue | `putImageData` / `texImage2D` / `texSubImage2D` swap R and B | VIDEO-01.b |
| VIDEO-01.zero-alpha | same wrappers zero the alpha byte | VIDEO-01.a |
| TIME-01.freeze-performance-now | `performance.now` frozen after the warmup | TIME-01.a |
| TIME-01.scale-performance-now-2x | `performance.now` runs 2x after the warmup | TIME-01.a |
| TIME-02.busy-wait-3s | 3 s busy loop injected 1.5 s into the window | TIME-02.a or TIME-02.b |
| INPUT-01.drop-keydown | capture listener on window swallows all keydown | INPUT-01.a to .f |
| INPUT-01.swap-key-codes | Up/Down, Left/Right, Control/Shift swapped by re-dispatching the event | INPUT-01.a to .f |
| INPUT-02.drop-keyup | swallows all keyup | INPUT-02.a |
| INPUT-03.preventdefault-noop | `Event.prototype.preventDefault` does nothing | INPUT-03.a |
| INPUT-03.ignore-blur | `addEventListener` ignores blur/focusout/visibilitychange/pagehide | INPUT-03.c |
| AUDIO-01.silent-output | connections to a destination node are dropped | AUDIO-01.b |
| STABILITY-01.uncaught-exception | uncaught `throw` 100 ms after the warmup | STABILITY-01.a |
| DEPLOY-01.wasm-as-text-plain | response interception rewrites the wasm `Content-Type` to `text/plain` | DEPLOY-01.a |
| DEPLOY-01.broken-asset-paths | everything under `/doom/` except the entry page returns 404 | DEPLOY-01.b |

## What these checks cannot establish

- Hooks can be deceptive (a hook can return a constant that matches the tests); movement is cross-checked only against the model, rendering only against the WAD-derived pixels.
- View pixels are checked by palette membership, not against a rendered reference; frame changes can come from animation (the turn test uses a 60 degree turn to dominate that).
- Audio: output energy after firing, not what was played, not music, not loudness. Music or any constant noise satisfies AUDIO-01.b.
- One browser, scripted scenarios, `?warp=E1M1&nomonsters=1`. Title screen, menus, save/load, other maps and sustained play are PLAY-01 (human).
- Network use by `build.sh` is not blocked by these tests (the container must do that); network use by the page is blocked and logged.
- Input is injected through Chromium's real keyboard path, but `blur` is dispatched as an event rather than by actually switching windows.

## Risks (things I could not run)

1. `AudioContext` state in the headless container: if Chromium cannot start an audio sink, `running` may never occur and AUDIO-01.a would FAIL for a correct port.
2. The 2 s warmup may end inside the level-start screen wipe (`d_main.c`/`f_wipe.c` run a blocking loop with no tic progress); the contract freezes 2 s, so TIME-01.a may see a low rate on a correct port
   if the wipe is longer than expected. The other groups use "warmup + 35 tics" instead.
3. Pixel coordinates of the HUD glyphs were derived by reading `st_lib.c`/`st_stuff.c`, not by comparing with a running engine; the 9-position shift search is the only safety net.
4. The probe assumes `Module` is a global and the canvas has id `canvas`, as the contract says.
5. Chromium version is expected to be the one bundled with Playwright 1.56.1 (141.x); a different version is noted in `results.json` but not rejected.

## Revision after human review (before freeze)
- AUDIO-01.a/.b are judged after the first sound-triggering key press (fire), so lazily created audio is accepted.
- VIDEO-01 pixel faults count upload-path applications (`window.__qaPixelN`); if none occurred, the fault is applied at `getImageData` read-back. It applies exactly once.
