'use strict';
// INPUT-01.a-f, INPUT-02.a, INPUT-03.a-c. Every motion is measured between two game-tic snapshots, never wall time.
// Every test starts on a fresh page at the E1M1 start (open to the north, 736 units; see README "open position").
const { sleep, SetupError, fmt } = require('../lib/util');
const { withGame, settle, snap, waitTic, holdKeys, safeEval, shot } = require('../lib/site');
const M = require('../lib/model');

const I1 = ['INPUT-01.a', 'INPUT-01.b', 'INPUT-01.c', 'INPUT-01.d', 'INPUT-01.e', 'INPUT-01.f'];
const LAG = 3;             // tics between the snapshot before key-down and the engine seeing it (README "input lag")
const LO = 0.5, HI = 1.5;  // distance must be within 50% .. 150% of the model (README "movement distance")
const TURN_LO = 0.6, TURN_HI = 1.4;
const RUN_RATIO = [1.6, 2.4]; // 2.0 +/- 20% (README "run ratio")

function proj(env, dx, dy) {
  const th = env.start.angle * Math.PI / 180;
  return { along: dx * Math.cos(th) + dy * Math.sin(th), lat: -dx * Math.sin(th) + dy * Math.cos(th) };
}
function disp(env, r) {
  if ([r.s0.x, r.s0.y, r.s1.x, r.s1.y].some((v) => v === null)) throw new SetupError('DG_TEST_GetPlayerX/Y returned null');
  return proj(env, (r.s1.x - r.s0.x) / 65536, (r.s1.y - r.s0.y) / 65536);
}
const dist = (a, b) => Math.hypot((b.x - a.x) / 65536, (b.y - a.y) / 65536);

// Fresh page at the start position, settled. fn(g, settledSnap).
async function fresh(env, label, fn) {
  return withGame(env, { label }, async (g) => {
    if (!g.firstSnap) throw new SetupError('game did not start: ' + g.failReason);
    const s = await settle(g, { warmMs: 1500, minTics: 35 });
    if (s.x !== env.start.x * 65536 || s.y !== env.start.y * 65536) throw new SetupError('precondition: player is not at the E1M1 start before any input (x=' + s.x / 65536 + ', y=' + s.y / 65536 + ')');
    return fn(g, s);
  });
}

// Run one assertion body; exceptions (setup problems, page death) become ERROR, never PASS.
async function guarded(rec, ids, body) {
  try { await body(); } catch (e) { for (const id of ids) rec.error(id, (e instanceof SetupError ? 'precondition: ' : 'infrastructure: ') + String(e && e.message || e).split('\n')[0]); }
}

async function input01(env, rec) {
  const block = ['LOAD-01.a', 'WAD-01.a'];
  const todo = I1.filter((id) => !rec.blockedBy(id, block));
  const want = (ids) => ids.some((i) => todo.includes(i));

  if (want(['INPUT-01.a'])) await guarded(rec, ['INPUT-01.a'], () => fresh(env, 'in01-forward', async (g) => {
    const r = await holdKeys(g, ['ArrowUp'], 30);
    const { along, lat } = disp(env, r), lo = LO * M.walkDisp(r.nT - LAG), hi = HI * M.walkDisp(r.nT);
    const txt = 'ArrowUp for ' + r.nT + ' tics moved ' + fmt(along) + ' forward, ' + fmt(lat) + ' sideways; need ' + fmt(lo) + ' to ' + fmt(hi);
    if (along >= lo && along <= hi && Math.abs(lat) <= 0.1 * Math.abs(along) + 2) rec.pass('INPUT-01.a', txt); else rec.fail('INPUT-01.a', txt);
  }));

  if (want(['INPUT-01.b'])) await guarded(rec, ['INPUT-01.b'], () => fresh(env, 'in01-back', async (g) => {
    // Only 48 units are free behind the start (wall 64 away, radius 16), so the window is short.
    const r = await holdKeys(g, ['ArrowDown'], 8);
    const { along, lat } = disp(env, r), lo = LO * M.walkDisp(r.nT - LAG);
    const txt = 'ArrowDown for ' + r.nT + ' tics moved ' + fmt(along) + ' (negative = backward), ' + fmt(lat) + ' sideways; need <= -' + fmt(lo);
    if (along <= -lo && Math.abs(lat) <= 0.1 * Math.abs(along) + 2) rec.pass('INPUT-01.b', txt); else rec.fail('INPUT-01.b', txt);
  }));

  if (want(['INPUT-01.c', 'INPUT-01.d'])) await guarded(rec, ['INPUT-01.c', 'INPUT-01.d'], () => fresh(env, 'in01-turn', async (g) => {
    const turn = async (key, sign, id) => {
      const r = await holdKeys(g, [key], 20);
      if (r.s0.ang === null || r.s1.ang === null) { rec.fail(id, 'DG_TEST_GetViewAngle returned null'); return; }
      const d = sign * M.angleDelta(r.s1.ang, r.s0.ang), lo = TURN_LO * M.turnDeg(r.nT - LAG), hi = TURN_HI * M.turnDeg(r.nT);
      const txt = key + ' for ' + r.nT + ' tics turned ' + fmt(d) + ' deg in the expected direction; need ' + fmt(lo) + ' to ' + fmt(hi);
      if (d >= lo && d <= hi) rec.pass(id, txt); else rec.fail(id, txt);
    };
    await turn('ArrowLeft', 1, 'INPUT-01.c');
    const s = await snap(g); await waitTic(g, s.tic + 6, 3000); // let turnheld reset
    await turn('ArrowRight', -1, 'INPUT-01.d');
  }));

  if (want(['INPUT-01.e'])) await guarded(rec, ['INPUT-01.e'], () => fresh(env, 'in01-fire', async (g, s) => {
    if (s.ammo === null) throw new SetupError('DG_TEST_GetAmmo returned null');
    if (!(s.ammo > 0)) throw new SetupError('precondition: ammo is ' + s.ammo + ' before firing (pistol expected, 50 rounds)');
    const r = await holdKeys(g, ['Control'], 40);
    const after = await waitTic(g, r.s1.tic + 10, 3000);
    const shots = s.ammo - after.ammo, max = Math.floor(r.nT / 19) + 1; // pistol cycle 4+6+4+5 = 19 tics (info.c S_PISTOL1..4)
    const txt = 'Control for ' + r.nT + ' tics: ammo ' + s.ammo + ' -> ' + after.ammo + ' (' + shots + ' shots; expected 1 to ' + max + ')';
    if (shots >= 1 && shots <= max) rec.pass('INPUT-01.e', txt); else rec.fail('INPUT-01.e', txt);
  }));

  if (want(['INPUT-01.f'])) await guarded(rec, ['INPUT-01.f'], async () => {
    const w = await fresh(env, 'in01-walk', async (g) => { const r = await holdKeys(g, ['ArrowUp'], 30); return { r, ...disp(env, r) }; });
    const u = await fresh(env, 'in01-run', async (g) => { const r = await holdKeys(g, ['Shift', 'ArrowUp'], 30); return { r, ...disp(env, r) }; });
    const wmin = LO * M.walkDisp(w.r.nT - LAG);
    if (!(w.along >= wmin)) { rec.fail('INPUT-01.f', 'walk reference moved only ' + fmt(w.along) + ' (need >= ' + fmt(wmin) + '), so the run ratio cannot be formed'); return; }
    const ratio = (u.along / w.along) * (M.walkDisp(w.r.nT) / M.walkDisp(u.r.nT));
    const txt = 'Shift+ArrowUp moved ' + fmt(u.along) + ' in ' + u.r.nT + ' tics vs walk ' + fmt(w.along) + ' in ' + w.r.nT + ' tics; tic-normalized ratio ' + fmt(ratio, 2) + ' (expected ' + RUN_RATIO[0] + ' to ' + RUN_RATIO[1] + ')';
    if (ratio >= RUN_RATIO[0] && ratio <= RUN_RATIO[1]) rec.pass('INPUT-01.f', txt); else rec.fail('INPUT-01.f', txt);
  });
}

async function input02(env, rec) {
  if (rec.blockedBy('INPUT-02.a', ['INPUT-01.a'])) return;
  await guarded(rec, ['INPUT-02.a'], () => fresh(env, 'in02-release', async (g) => {
    const kb = g.page.keyboard;
    const s0 = await snap(g);
    await kb.down('ArrowUp');
    let sm, s1;
    try { sm = await waitTic(g, s0.tic + 5, 3000); s1 = sm && await waitTic(g, sm.tic + 6, 3000); } finally { await kb.up('ArrowUp'); }
    if (!sm || !s1) throw new SetupError('game did not advance while the key was held');
    const heldSpeed = dist(sm, s1) / (s1.tic - sm.tic); // held-key control: units per tic while the key is down
    if (!(heldSpeed >= 1)) throw new SetupError('precondition: held-key control shows no motion (' + fmt(heldSpeed, 2) + ' units/tic)');
    const sR = await waitTic(g, s1.tic + 50, 5000);       // friction (p_mobj.c FRICTION) needs ~45 tics to bring 5 units/tic under STOPSPEED
    const sE = sR && await waitTic(g, sR.tic + 10, 3000);
    if (!sR || !sE) throw new SetupError('game did not advance after release');
    const moved = dist(sR, sE), held10 = heldSpeed * (sE.tic - sR.tic);
    const txt = 'held key: ' + fmt(heldSpeed, 2) + ' units/tic; 50 tics after release the next ' + (sE.tic - sR.tic) + ' tics moved ' + fmt(moved, 2) + ' units (control would be ' + fmt(held10) + '; need <= 1.0 and <= 5% of that)';
    if (moved <= 1.0 && moved <= 0.05 * held10) rec.pass('INPUT-02.a', txt); else rec.fail('INPUT-02.a', txt);
  }));
}

const KEYS_3A = [['ArrowDown', 'ArrowDown', 40], ['ArrowUp', 'ArrowUp', 38], [' ', 'Space', 32], ['ArrowRight', 'ArrowRight', 39], ['ArrowLeft', 'ArrowLeft', 37]];

async function input03(env, rec) {
  const ids = ['INPUT-03.a', 'INPUT-03.b'];
  if (!(rec.blockedBy('INPUT-03.a', ['LOAD-01.a']) | rec.blockedBy('INPUT-03.b', ['LOAD-01.a']))) {
    await guarded(rec, ids, () => fresh(env, 'in03-page', async (g) => {
      const state = () => safeEval(g.page, () => {
        const a = document.activeElement;
        return { active: a ? a.tagName + '#' + a.id : 'none', focus: document.hasFocus(), sx: window.scrollX, sy: window.scrollY };
      }, 3000);
      // Tall, invisible content makes the page scrollable without touching the page's own overflow rules.
      await safeEval(g.page, () => {
        const d = document.createElement('div');
        d.style.cssText = 'position:absolute;left:0;top:0;width:3000px;height:6000px;visibility:hidden;pointer-events:none';
        document.body.appendChild(d);
      }, 3000);
      const before = await state();
      if (!before.focus) throw new SetupError('precondition: document does not have focus before any key press');
      for (const k of KEYS_3A) { await g.page.keyboard.press(k[0], { delay: 60 }); await sleep(120); }
      await sleep(200);
      const after = await state();
      // Synthetic cancelable keydown on the active element: dispatchEvent returns false when the page called preventDefault.
      const prevented = await safeEval(g.page, (keys) => keys.map(([key, code, kc]) => {
        const t = document.activeElement || document.body;
        const mk = (type) => { const e = new KeyboardEvent(type, { key, code, keyCode: kc, which: kc, bubbles: true, cancelable: true }); return e; };
        const notPrevented = t.dispatchEvent(mk('keydown'));
        t.dispatchEvent(mk('keyup'));
        return [code, !notPrevented];
      }), 3000, KEYS_3A);
      const unprevented = prevented.filter((p) => !p[1]).map((p) => p[0]);
      const scrolled = after.sx !== 0 || after.sy !== 0;
      const txt = 'scroll after ArrowDown/ArrowUp/Space/ArrowRight/ArrowLeft = (' + after.sx + ',' + after.sy + '); preventDefault missing for: ' + (unprevented.join(',') || 'none');
      if (!scrolled && !unprevented.length) rec.pass('INPUT-03.a', txt); else rec.fail('INPUT-03.a', txt);
      const txtb = 'activeElement ' + before.active + ' -> ' + after.active + ', document.hasFocus() ' + before.focus + ' -> ' + after.focus;
      if (after.focus && after.active === before.active) rec.pass('INPUT-03.b', txtb); else rec.fail('INPUT-03.b', txtb);
    }));
  }
  if (rec.blockedBy('INPUT-03.c', ['INPUT-01.a'])) return;
  await guarded(rec, ['INPUT-03.c'], () => fresh(env, 'in03-blur', async (g) => {
    const kb = g.page.keyboard;
    const s0 = await snap(g);
    await kb.down('ArrowUp');
    let sm = null;
    try {
      sm = await waitTic(g, s0.tic + 10, 3000);
      if (!sm) throw new SetupError('game did not advance while the key was held');
      const held = dist(s0, sm) / (sm.tic - s0.tic);
      if (!(held >= 1)) throw new SetupError('precondition: held key produced no motion (' + fmt(held, 2) + ' units/tic)');
      await safeEval(g.page, () => { window.dispatchEvent(new Event('blur')); }, 3000); // the key stays physically down in the test
      const sR = await waitTic(g, sm.tic + 50, 5000);
      const sE = sR && await waitTic(g, sR.tic + 10, 3000);
      if (!sR || !sE) throw new SetupError('game did not advance after blur');
      const moved = dist(sR, sE), held10 = held * (sE.tic - sR.tic);
      const txt = 'ArrowUp still held, window blur dispatched: before blur ' + fmt(held, 2) + ' units/tic; 50 tics later the next ' + (sE.tic - sR.tic) + ' tics moved ' + fmt(moved, 2) + ' (control ' + fmt(held10) + '; need <= 1.0 and <= 5% of that)';
      if (moved <= 1.0 && moved <= 0.05 * held10) rec.pass('INPUT-03.c', txt); else rec.fail('INPUT-03.c', txt);
    } finally { await kb.up('ArrowUp'); }
  }));
}

module.exports = { input01, input02, input03, I1 };
