'use strict';
// VIDEO-01.a-c. Pixel references come from PLAYPAL (with the engine's gamma step), the STBAR / STTNUM / STTPRCNT patches
// and the layout constants in lib/wad.js. Canvas scale is 2 (i_video.c fb_scaling for 640x400).
const { SetupError } = require('../lib/util');
const { withGame, settle, snap, waitTic, readCanvas, shot } = require('../lib/site');

const IDS = ['VIDEO-01.a', 'VIDEO-01.b', 'VIDEO-01.c'];
const TOL = 2;            // per-channel tolerance, see README "pixel tolerance"
const VIEW_ROWS = 336;    // canvas rows of the 3D view: 168 Doom rows (screenblocks 10, m_menu.c:78) x 2
const MIN_VIEW = 0.99, MIN_BAR = 0.99, MIN_WIDGET = 0.95;

function paletteMatcher(pal) {
  const cache = new Map();
  return (r, g, b) => {
    const k = (r << 16) | (g << 8) | b;
    let v = cache.get(k);
    if (v === undefined) {
      v = false;
      for (let i = 0; i < pal.length && !v; i++) v = Math.abs(pal[i][0] - r) <= TOL && Math.abs(pal[i][1] - g) <= TOL && Math.abs(pal[i][2] - b) <= TOL;
      cache.set(k, v);
    }
    return v;
  };
}

function near(d, o, c) { return Math.abs(d[o] - c[0]) <= TOL && Math.abs(d[o + 1] - c[1]) <= TOL && Math.abs(d[o + 2] - c[2]) <= TOL; }

function pixelMatch(c, refs, pal) {
  const d = c.data, W = c.w;
  const inPal = paletteMatcher(pal);
  let n = 0, ok = 0;
  for (let y = 0; y < VIEW_ROWS; y++) for (let x = 0; x < W; x++) { const o = (y * W + x) * 4; n++; if (inPal(d[o], d[o + 1], d[o + 2])) ok++; }
  const view = ok / n;
  const block = (list, dx, dy) => {
    let t = 0, m = 0;
    for (const p of list) for (let j = 0; j < 2; j++) for (let i = 0; i < 2; i++) {
      const cx = 2 * (p.x + dx) + i, cy = 2 * (p.y + dy) + j;
      if (cx < 0 || cx >= W || cy < 0 || cy >= c.h) continue;
      t++; if (near(d, (cy * W + cx) * 4, p.c)) m++;
    }
    return t ? m / t : 0;
  };
  const bar = block(refs.bg, 0, 0);
  let widget = 0, shift = '0,0';
  for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const f = block(refs.widgets, dx, dy); if (f > widget) { widget = f; shift = dx + ',' + dy; } }
  return { view, bar, widget, shift };
}

function changedFraction(a, b) {
  let n = 0, diff = 0;
  for (let i = 0; i < VIEW_ROWS * a.w * 4; i += 4) { n++; if (Math.abs(a.data[i] - b.data[i]) > TOL || Math.abs(a.data[i + 1] - b.data[i + 1]) > TOL || Math.abs(a.data[i + 2] - b.data[i + 2]) > TOL) diff++; }
  return diff / n;
}

async function videoGroup(env, rec) {
  if (rec.blockedBy('VIDEO-01.a', ['LOAD-01.a']) | rec.blockedBy('VIDEO-01.b', ['LOAD-01.a']) | rec.blockedBy('VIDEO-01.c', ['LOAD-01.a'])) return;
  try {
    await withGame(env, { label: 'video' }, async (g) => {
      if (!g.firstSnap) throw new SetupError('game did not start: ' + g.failReason);
      await settle(g, { warmMs: 2000, minTics: 35 });
      const c0 = await readCanvas(g);
      if (c0.err) { for (const id of IDS) rec.fail(id, c0.err); return; }
      // a
      let bad = 0;
      for (let i = 3; i < c0.data.length; i += 4) if (c0.data[i] !== 255) bad++;
      if (c0.w !== 640 || c0.h !== 400) rec.fail('VIDEO-01.a', 'canvas is ' + c0.w + 'x' + c0.h + ', contract says 640x400');
      else if (bad) rec.fail('VIDEO-01.a', bad + ' of ' + c0.w * c0.h + ' canvas pixels have alpha != 255');
      else rec.pass('VIDEO-01.a', 'all ' + c0.w * c0.h + ' canvas pixels have alpha 255');
      // b
      if (c0.w !== 640 || c0.h !== 400) rec.fail('VIDEO-01.b', 'canvas is ' + c0.w + 'x' + c0.h + ', cannot compare against the 640x400 reference');
      else {
        const m = pixelMatch(c0, env.refs, env.pal);
        const txt = 'view-in-palette ' + m.view.toFixed(4) + ' (>= ' + MIN_VIEW + '), status-bar background ' + m.bar.toFixed(4) + ' (>= ' + MIN_BAR + '), HUD number glyphs ' + m.widget.toFixed(4) + ' (>= ' + MIN_WIDGET + ', shift ' + m.shift + '), tol ' + TOL;
        rec.meas['VIDEO-01.b'] = m;
        if (m.view >= MIN_VIEW && m.bar >= MIN_BAR && m.widget >= MIN_WIDGET) rec.pass('VIDEO-01.b', txt); else rec.fail('VIDEO-01.b', txt);
      }
      // c: turn left for 20 tics and watch the frame counter and the canvas
      const kb = g.page.keyboard;
      const s0 = await snap(g);
      await kb.down('ArrowLeft');
      let s1 = null, c1 = null;
      try { s1 = await waitTic(g, s0.tic + 20, 5000); c1 = await readCanvas(g); } finally { await kb.up('ArrowLeft'); }
      if (!s1) throw new SetupError('game did not advance 20 tics while turning');
      if (s0.frame === null || s1.frame === null) rec.fail('VIDEO-01.c', 'DG_TEST_GetFrameCount is not callable');
      else if (c1.err) rec.fail('VIDEO-01.c', c1.err);
      else {
        const nT = s1.tic - s0.tic, frames = s1.frame - s0.frame, ch = changedFraction(c0, c1);
        const need = Math.max(3, Math.floor(nT / 4));
        const txt = 'frames +' + frames + ' over ' + nT + ' tics (need >= ' + need + '), view pixels changed ' + (ch * 100).toFixed(1) + '% (need >= 5%)';
        if (frames >= need && ch >= 0.05) rec.pass('VIDEO-01.c', txt); else rec.fail('VIDEO-01.c', txt);
      }
      await shot(g, 'turned');
    });
  } catch (e) {
    rec.errorAll(IDS, 'infrastructure or precondition: ' + String(e && e.message || e).split('\n')[0]);
  }
}

module.exports = { videoGroup, IDS };
