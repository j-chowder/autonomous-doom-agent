'use strict';
// Reproduces the numbers in README.md from assets/doom1.wad and third_party/doomgeneric. Not run by any gate.
//   node tests/doom/trusted/derive.js [repo-root]
const path = require('path');
const { loadWad, loadGamma0, palette0, playerStart, freeDistance, statusBarRefs } = require('./lib/wad');
const M = require('./lib/model');

const root = path.resolve(process.argv[2] || path.join(__dirname, '..', '..', '..'));
const wad = loadWad(path.join(root, 'assets', 'doom1.wad'));
const pal = palette0(wad, loadGamma0(path.join(root, 'third_party', 'doomgeneric', 'tables.c')));
const start = playerStart(wad, 'E1M1');
console.log('E1M1 player 1 start THING:', JSON.stringify(start), '=> raw fixed x,y =', start.x * 65536, start.y * 65536, ', view angle BAM =', Math.round(start.angle / 360 * 4294967296));
for (const [name, deg] of [['north', 90], ['south', 270], ['east', 0], ['west', 180]]) {
  const d = [-16, 0, 16].map((l) => freeDistance(wad, 'E1M1', start.x, start.y, deg, l));
  console.log('free distance ' + name + ' (rays at -16,0,+16 lateral):', d.map((v) => v.toFixed(1)).join(', '), '=> usable after radius 16:', (Math.min(...d) - 16).toFixed(1));
}
console.log('\nmovement model (units after holding n tics, from rest, at key release)');
for (const n of [8, 10, 12, 20, 30, 33]) console.log('  n=' + n + '  walk ' + M.walkDisp(n).toFixed(1) + '  run ' + M.runDisp(n).toFixed(1) + '  run/walk ' + (M.runDisp(n) / M.walkDisp(n)).toFixed(3));
console.log('  steady speed walk ' + (M.moveDisp(400, 25) - M.moveDisp(399, 25)).toFixed(2) + ' units/tic, run ' + (M.moveDisp(400, 50) - M.moveDisp(399, 50)).toFixed(2));
console.log('  1 tic of lag costs (n=30): ' + (100 * (1 - M.walkDisp(29) / M.walkDisp(30))).toFixed(1) + '% of distance; 3 tics: ' + (100 * (1 - M.walkDisp(27) / M.walkDisp(30))).toFixed(1) + '%');
console.log('  coast after release from n=12 walk: tics until momentum < STOPSPEED (0.0625):', (() => { let m = M.moveDisp(12, 25) && 0; let v = 0, p = 25 * 2048 / 65536; for (let i = 0; i < 12; i++) { v += p; v *= M.FRICTION; } let t = 0; while (v >= 0.0625) { v *= M.FRICTION; t++; } return t; })());
console.log('\nturn model (degrees): ' + [10, 17, 20, 23].map((n) => 'n=' + n + ' ' + M.turnDeg(n).toFixed(1)).join('  '));
const refs = statusBarRefs(wad, pal);
const sens = refs.widgets.filter((p) => Math.abs(p.c[0] - p.c[2]) > 2);
console.log('\nstatus bar background pixels compared:', refs.bg.length, ' (all gray: max |R-B| =', Math.max(...refs.bg.map((p) => Math.abs(p.c[0] - p.c[2]))) + ')');
console.log('HUD glyph pixels (ammo 50, health 100%, armor 0%):', refs.widgets.length, '; with |R-B| > 2 (so an R/B swap moves them out of tolerance):', sens.length, '(' + (100 * sens.length / refs.widgets.length).toFixed(1) + '%); smallest |R-B| among those:', Math.min(...sens.map((p) => Math.abs(p.c[0] - p.c[2]))));
const colors = new Set(pal.map((c) => c.join(',')));
const swapped = pal.filter((c) => pal.some((q) => Math.abs(q[0] - c[2]) <= 2 && Math.abs(q[1] - c[1]) <= 2 && Math.abs(q[2] - c[0]) <= 2)).length;
console.log('palette: ' + colors.size + ' distinct colors; ' + swapped + ' of 256 entries keep an in-palette color after R/B swap (tol 2)');
