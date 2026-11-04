'use strict';
// Closed-form models of player movement and turning, taken from the engine source.
// They turn "hold a key for N game tics" into an expected distance or angle.

// g_game.c:156-158
const FORWARDMOVE = [0x19, 0x32];           // walk, run (forward/back command units)
const ANGLETURN = [640, 1280, 320];          // normal, run, slow-start (BAM>>16 per tic)
const SLOWTURNTICS = 6;                      // g_game.c:193; tspeed=2 while turnheld < 6
// p_mobj.c:106 FRICTION 0xe800 (16.16 fixed); P_Thrust adds cmd*2048 (p_user.c) so units/tic = cmd/32.
const FRICTION = 0xe800 / 65536;
const MAXMOVE = 30;                          // p_local.h:51 (units/tic); never reached by walk/run

// Displacement in map units after holding forward for n tics from rest, measured when the key is released.
// Per tic: mom += push; pos += mom; mom *= FRICTION (P_MovePlayer, then P_XYMovement).
function moveDisp(n, cmd) {
  const push = cmd * 2048 / 65536;
  let m = 0, pos = 0;
  for (let i = 0; i < n; i++) { m += push; if (m > MAXMOVE) m = MAXMOVE; pos += m; m *= FRICTION; }
  return pos;
}
const walkDisp = (n) => moveDisp(Math.max(0, n), FORWARDMOVE[0]);
const runDisp = (n) => moveDisp(Math.max(0, n), FORWARDMOVE[1]);

// Degrees turned after holding a turn key for n tics without Shift (turnheld counts 1,2,... per tic).
function turnDeg(n) {
  let sum = 0;
  for (let i = 1; i <= n; i++) sum += i < SLOWTURNTICS ? ANGLETURN[2] : ANGLETURN[0];
  return sum / 65536 * 360; // angleturn<<16 BAM; 2^32 BAM = 360 degrees
}

// Signed degrees between two uint32 BAM angles (positive = counter-clockwise = ArrowLeft).
function angleDelta(a1, a0) {
  const d = ((a1 - a0) | 0);
  return d / 4294967296 * 360;
}

module.exports = { FORWARDMOVE, ANGLETURN, SLOWTURNTICS, FRICTION, moveDisp, walkDisp, runDisp, turnDeg, angleDelta };
