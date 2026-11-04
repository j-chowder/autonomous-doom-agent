'use strict';
// STABILITY-01.a (short observation + aggregate over every page of the run) and STABILITY-01.b (60 s soak).
const { sleep, nowMs, SetupError } = require('../lib/util');
const { withGame, settle, snap, waitTic } = require('../lib/site');

// Short run: load, run 6 s after warmup, so a crash or exception shortly after load is seen.
async function stabilityShort(env, rec) {
  try {
    await withGame(env, { label: 'stability-short' }, async (g) => {
      if (!g.firstSnap) throw new SetupError('game did not start: ' + g.failReason);
      await sleep(2000);
      if (env.fault && env.fault.afterWarm) await env.fault.afterWarm(g);
      await sleep(6000);
      env.stabilityShortRan = true;
    });
  } catch (e) {
    env.stabilityShortError = String(e && e.message || e).split('\n')[0];
  }
}

// Called once at the end of a gate run (or a control run). Fatal events come from every page the run opened.
function finalizeStability(env, rec) {
  if (!env.stabilityShortRan) { rec.error('STABILITY-01.a', 'short stability run did not complete: ' + (env.stabilityShortError || 'not run')); return; }
  if (env.fatalLog.length) {
    const f = env.fatalLog[0];
    rec.fail('STABILITY-01.a', env.fatalLog.length + ' crash/exception/rejection/abort event(s) over ' + env.pagesOpened + ' page(s); first in ' + f.label + ': ' + f.kind + ': ' + f.text);
  } else rec.pass('STABILITY-01.a', 'zero crashes, uncaught exceptions, unhandled rejections or aborts over ' + env.pagesOpened + ' page(s)');
}

// Soak: 60 s of scripted play. STABILITY-01.b needs tics still rising over the last 10 s.
// Liveness threshold: >= 175 tics in the last 10 s = 50% of the nominal 350 (TICRATE 35 x 10 s). Rate accuracy is TIME-01's job.
const SOAK_STEPS = [['ArrowLeft'], ['ArrowUp'], ['Control'], [' '], ['ArrowDown'], ['ArrowRight'], ['Shift', 'ArrowUp'], ['Control']];
async function soak(env, rec) {
  try {
    await withGame(env, { label: 'soak' }, async (g) => {
      if (!g.firstSnap) throw new SetupError('game did not start: ' + g.failReason);
      await settle(g, { warmMs: 1000, minTics: 35 });
      const t0 = nowMs(), cps = [];
      let step = 0, nextCp = 0;
      while (nowMs() - t0 < 60000) {
        if (nowMs() - t0 >= nextCp) { cps.push({ at: Math.round(nowMs() - t0), s: await snap(g) }); nextCp += 5000; }
        const keys = SOAK_STEPS[step++ % SOAK_STEPS.length];
        for (const k of keys) await g.page.keyboard.down(k);
        await sleep(350);
        for (const k of keys.slice().reverse()) await g.page.keyboard.up(k);
        await sleep(250);
      }
      const last = await snap(g);
      cps.push({ at: Math.round(nowMs() - t0), s: last });
      const tenAgo = cps.filter((c) => c.at <= cps[cps.length - 1].at - 10000).pop() || cps[0];
      const rising = cps.every((c, i) => i === 0 || c.s.tic >= cps[i - 1].s.tic);
      const d = last.tic - tenAgo.s.tic, secs = (cps[cps.length - 1].at - tenAgo.at) / 1000;
      rec.meas = rec.meas || {};
      rec.meas.soak = { checkpoints: cps.map((c) => [c.at, c.s.tic]), lastWindowTics: d, lastWindowSecs: secs };
      if (rising && d >= 17.5 * secs) rec.pass('STABILITY-01.b', 'soak ' + cps[cps.length - 1].at + ' ms; tic ' + cps[0].s.tic + ' -> ' + last.tic + '; last ' + secs.toFixed(1) + ' s advanced ' + d + ' tics');
      else rec.fail('STABILITY-01.b', 'tics not rising at the end of the soak: last ' + secs.toFixed(1) + ' s advanced ' + d + ' tics (need >= ' + Math.round(17.5 * secs) + '), monotonic=' + rising);
    });
  } catch (e) {
    rec.error('STABILITY-01.b', 'soak did not complete: ' + String(e && e.message || e).split('\n')[0]);
  }
}

module.exports = { stabilityShort, finalizeStability, soak };
