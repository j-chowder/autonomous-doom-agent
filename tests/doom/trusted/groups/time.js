'use strict';
// TIME-01.a, TIME-02.a, TIME-02.b over one window (10 s fast gates, 60 s final gate).
const { sleep, nowMs, withTimeout, SetupError } = require('../lib/util');
const { withGame, safeEval, shot } = require('../lib/site');

const IDS = ['TIME-01.a', 'TIME-02.a', 'TIME-02.b'];
const RATE_LO = 26.25, RATE_HI = 43.75;   // 35 tics/s +/- 25% (contract section 1)
const MAX_GAP_MS = 1000;                  // contract section 1, max heartbeat gap
const WARMUP_MS = 2000;                   // contract section 1 (proposal), after the first tic
const PING_EVERY_MS = 250, PING_TIMEOUT_MS = 1000; // watchdog: must answer within the heartbeat bound

function startWatchdog(g) {
  const st = { pings: 0, timeouts: 0, maxLat: 0, stop: false };
  const run = (async () => {
    while (!st.stop) {
      const t = nowMs();
      try { await withTimeout(g.page.evaluate(() => 1), PING_TIMEOUT_MS, 'watchdog ping'); const l = nowMs() - t; if (l > st.maxLat) st.maxLat = l; }
      catch (e) { st.timeouts++; }
      st.pings++;
      await sleep(PING_EVERY_MS);
    }
  })();
  st.finish = async () => { st.stop = true; await run; };
  return st;
}

async function timeGroup(env, rec, opts) {
  const windowMs = opts.windowMs;
  if (rec.blockedBy('TIME-01.a', ['LOAD-01.a']) | rec.blockedBy('TIME-02.a', ['LOAD-01.a']) | rec.blockedBy('TIME-02.b', ['LOAD-01.a'])) return;
  try {
    await withGame(env, { label: 'time-' + windowMs / 1000 + 's' }, async (g) => {
      if (!g.firstSnap) throw new SetupError('game did not start: ' + g.failReason);
      await sleep(WARMUP_MS);
      const ws = await safeEval(g.page, () => { window.__qa.rafReset(); return window.__qa.snap(); }, 8000);
      if (ws.tic === null) throw new SetupError('DG_TEST_GetGameTic is not callable');
      const w0 = nowMs();
      if (env.fault && env.fault.afterWarm) await env.fault.afterWarm(g);
      const wd = startWatchdog(g);
      await sleep(Math.max(0, windowMs - (nowMs() - w0)));
      let we = null, raf = null, endErr = null;
      try { [we, raf] = await safeEval(g.page, () => [window.__qa.snap(), window.__qa.rafRead()], 8000); } catch (e) { endErr = e; }
      await wd.finish();
      const wall = nowMs() - w0;
      if (endErr) {
        const why = 'page did not answer at the window end: ' + String(endErr.message).split('\n')[0];
        rec.fail('TIME-01.a', why); rec.fail('TIME-02.a', why);
      } else {
        const dTic = we.tic - ws.tic, dRef = (we.rnow - ws.rnow) / 1000, dPage = (we.pnow - ws.pnow) / 1000;
        const rateRef = dTic / dRef, ratePage = dPage > 0 ? dTic / dPage : NaN;
        const m = { windowMs, dTic, dRefSec: dRef, dPageSec: dPage, rateRef, ratePage, wallMs: Math.round(wall) };
        rec.meas['TIME-01.a'] = m;
        const txt = dTic + ' tics: ' + rateRef.toFixed(2) + '/s on the unfaulted clock, ' + (isFinite(ratePage) ? ratePage.toFixed(2) : 'undefined (performance.now did not advance)') + '/s on page performance.now; accepted ' + RATE_LO + ' to ' + RATE_HI;
        if (Math.abs(dRef * 1000 - windowMs) > Math.max(2000, windowMs * 0.25)) rec.error('TIME-01.a', 'window ran ' + dRef.toFixed(1) + ' s instead of ' + windowMs / 1000 + ' s (host stalled); ' + txt);
        else if (isFinite(rateRef) && isFinite(ratePage) && rateRef >= RATE_LO && rateRef <= RATE_HI && ratePage >= RATE_LO && ratePage <= RATE_HI) rec.pass('TIME-01.a', txt);
        else rec.fail('TIME-01.a', txt);
        const gap = raf.max;
        rec.meas['TIME-02.a'] = { maxGapMs: gap, rafCallbacks: raf.count };
        if (gap <= MAX_GAP_MS) rec.pass('TIME-02.a', 'largest requestAnimationFrame gap ' + gap.toFixed(0) + ' ms <= ' + MAX_GAP_MS + ' ms (' + raf.count + ' callbacks in ' + windowMs / 1000 + ' s)');
        else rec.fail('TIME-02.a', 'largest requestAnimationFrame gap ' + gap.toFixed(0) + ' ms > ' + MAX_GAP_MS + ' ms');
      }
      rec.meas['TIME-02.b'] = { pings: wd.pings, timeouts: wd.timeouts, maxLatencyMs: Math.round(wd.maxLat) };
      if (wd.pings >= 3 && wd.timeouts === 0) rec.pass('TIME-02.b', wd.pings + ' watchdog pings every ' + PING_EVERY_MS + ' ms, all answered, max latency ' + wd.maxLat.toFixed(0) + ' ms (timeout ' + PING_TIMEOUT_MS + ' ms)');
      else rec.fail('TIME-02.b', wd.timeouts + ' of ' + wd.pings + ' watchdog pings got no answer within ' + PING_TIMEOUT_MS + ' ms');
      await shot(g, 'end');
    });
  } catch (e) {
    rec.errorAll(IDS, 'infrastructure or precondition: ' + String(e && e.message || e).split('\n')[0]);
  }
}

module.exports = { timeGroup, IDS };
