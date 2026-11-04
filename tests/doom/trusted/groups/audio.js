'use strict';
// AUDIO-01.a-b. Both are judged after the first sound-triggering key press (fire). The probe in lib/site.js routes every AudioContext destination connection through an AnalyserNode.
const { sleep, nowMs, SetupError, fmt } = require('../lib/util');
const { withGame, settle, holdKeys, safeEval, waitTic } = require('../lib/site');

const IDS = ['AUDIO-01.a', 'AUDIO-01.b'];
// Silence floor: peak sample magnitude below 0.001 (-60 dBFS) counts as silence. README "silence level".
const SILENCE_PEAK = 0.001;

async function audioGroup(env, rec) {
  if (rec.blockedBy('AUDIO-01.a', ['LOAD-01.a']) | rec.blockedBy('AUDIO-01.b', ['LOAD-01.a'])) return;
  try {
    await withGame(env, { label: 'audio' }, async (g) => {
      if (!g.firstSnap) throw new SetupError('game did not start: ' + g.failReason);
      const s = await settle(g, { warmMs: 1500, minTics: 35 });
      // The first sound-triggering interaction (firing) comes BEFORE any judgement, so lazily created audio is not rejected.
      const pre = await safeEval(g.page, () => window.__qa.audioPeakSince(0), 3000);
      const mark = pre.n + 0;
      await holdKeys(g, ['Control'], 40);
      // a: after that key press an AudioContext must exist and be "running" (polled up to 3 s: creation may be lazy)
      let info = null, ok = false;
      for (let t0 = nowMs(); nowMs() - t0 < 3000 && !ok; await sleep(100)) {
        info = await safeEval(g.page, () => window.__qa.audioInfo(), 3000);
        ok = info.n >= 1 && info.states.includes('running');
      }
      if (ok) rec.pass('AUDIO-01.a', info.n + ' AudioContext(s), states ' + info.states.join(',') + ' after a sound-triggering key press (fire)');
      else rec.fail('AUDIO-01.a', info.n === 0 ? 'no AudioContext was created after firing' : 'AudioContext states after firing: ' + info.states.join(','));
      // b: non-silent output after firing
      if (rec.blockedBy('AUDIO-01.b', ['AUDIO-01.a'])) return;
      if (s.ammo === null || !(s.ammo > 0)) { rec.error('AUDIO-01.b', 'precondition: no ammo to fire (ammo=' + s.ammo + ')'); return; }
      await sleep(1500);
      const post = await safeEval(g.page, (m) => window.__qa.audioPeakSince(m), 3000, mark);
      rec.meas['AUDIO-01.b'] = { preFirePeak: pre.peak, postFirePeak: post.peak, postFireRms: post.rms, polls: post.n };
      const txt = 'peak output after firing ' + post.peak.toFixed(5) + ' (rms ' + post.rms.toFixed(5) + ', ' + post.n + ' polls); silence floor ' + SILENCE_PEAK + '; before firing peak was ' + pre.peak.toFixed(5);
      if (post.n < 5) rec.error('AUDIO-01.b', 'audio tap produced only ' + post.n + ' samples; ' + txt);
      else if (post.peak >= SILENCE_PEAK) rec.pass('AUDIO-01.b', txt); else rec.fail('AUDIO-01.b', txt);
    });
  } catch (e) {
    for (const id of IDS) rec.error(id, 'infrastructure or precondition: ' + String(e && e.message || e).split('\n')[0]);
  }
}

module.exports = { audioGroup, IDS };
