'use strict';
// Negative controls (contract section 6). Each control runs the SAME assertion code as the gates against a faulted copy
// and passes only if the named assertion FAILS (not ERROR) while the same assertion PASSES on the unfaulted candidate.
const fs = require('fs');
const path = require('path');
const { Recorder, SetupError } = require('./lib/util');
const { startServer, copyDir } = require('./lib/site');
const F = require('./lib/faults');
const { distChecks } = require('./groups/build');
const { bootGroup } = require('./groups/boot');
const { videoGroup } = require('./groups/video');
const { timeGroup } = require('./groups/time');
const { input01, input02, input03, I1 } = require('./groups/input');
const { audioGroup } = require('./groups/audio');
const { stabilityShort, finalizeStability } = require('./groups/stability');

const RUN = {
  buildwasm: async (env, rec) => { rec.pass('BUILD-01.a', 'n/a'); distChecks(path.join(env.root, 'doom'), rec); },
  boot: (env, rec) => bootGroup(env, rec, { deploy: true, label: 'ctl-boot' }),
  video: videoGroup,
  time: (env, rec) => timeGroup(env, rec, { windowMs: 10000 }),
  input01, input02, input03,
  audio: audioGroup,
  stability: async (env, rec) => { await stabilityShort(env, rec); finalizeStability(env, rec); },
};

const init = (fn, ...a) => ({ init: F.script(fn, ...a) });
const CONTROLS = [
  { id: 'BUILD-01.truncate-wasm', group: 'buildwasm', must: ['BUILD-01.d'], prep: (d) => F.truncateWasm(d) },
  { id: 'BUILD-01.no-hook-exports', group: 'buildwasm', must: ['BUILD-01.e'], prep: (d) => F.replaceWasmWithoutHooks(d) },
  { id: 'LOAD-01.block-wasm-request', group: 'boot', must: ['LOAD-01.a'], fault: { route: F.routes.blockWasm } },
  { id: 'WAD-01.delete-wad-data', group: 'boot', must: ['WAD-01.a'], prep: (d, env) => F.removeWadData(d, env.wadBuf) },
  { id: 'VIDEO-01.swap-red-blue', group: 'video', must: ['VIDEO-01.b'], fault: init(F.pixelFault, 'swap') },
  { id: 'VIDEO-01.zero-alpha', group: 'video', must: ['VIDEO-01.a'], fault: init(F.pixelFault, 'alpha0') },
  { id: 'TIME-01.freeze-performance-now', group: 'time', must: ['TIME-01.a'], fault: Object.assign(init(F.clockFault), { afterWarm: F.after.freeze }) },
  { id: 'TIME-01.scale-performance-now-2x', group: 'time', must: ['TIME-01.a'], fault: Object.assign(init(F.clockFault), { afterWarm: F.after.scale2 }) },
  { id: 'TIME-02.busy-wait-3s', group: 'time', must: ['TIME-02.a', 'TIME-02.b'], any: true, fault: { afterWarm: F.after.busy3s } },
  { id: 'INPUT-01.drop-keydown', group: 'input01', must: I1, fault: init(F.dropKeys, 'keydown') },
  { id: 'INPUT-01.swap-key-codes', group: 'input01', must: I1, fault: init(F.swapKeys) },
  { id: 'INPUT-02.drop-keyup', group: 'input02', must: ['INPUT-02.a'], fault: init(F.dropKeys, 'keyup') },
  { id: 'INPUT-03.preventdefault-noop', group: 'input03', must: ['INPUT-03.a'], fault: init(F.noPreventDefault) },
  { id: 'INPUT-03.ignore-blur', group: 'input03', must: ['INPUT-03.c'], fault: init(F.ignoreBlur) },
  { id: 'AUDIO-01.silent-output', group: 'audio', must: ['AUDIO-01.b'], fault: init(F.silentAudio) },
  { id: 'STABILITY-01.uncaught-exception', group: 'stability', must: ['STABILITY-01.a'], fault: { afterWarm: F.after.throwUncaught } },
  { id: 'DEPLOY-01.wasm-as-text-plain', group: 'boot', must: ['DEPLOY-01.a'], fault: { route: F.routes.wasmAsText } },
  { id: 'DEPLOY-01.broken-asset-paths', group: 'boot', must: ['DEPLOY-01.b'], fault: { route: F.routes.brokenAssets } },
];
const CONTROL_IDS = CONTROLS.map((c) => 'CONTROL-' + c.id);

const NEEDS_BROWSER = (g) => g !== 'buildwasm';

// Baselines: the unfaulted candidate must PASS the assertion a control claims to break, else the control proves nothing.
async function baselineFor(base, mainRec, cache, spec) {
  if (spec.must.every((id) => mainRec.status(id) === 'PASS')) return mainRec;
  if (cache.has(spec.group)) return cache.get(spec.group);
  const env = Object.assign({}, base, { root: path.dirname(base.distDoom), origin: base.origin, fault: null, fatalLog: [], netLog: [], pagesOpened: 0 });
  const rec = new Recorder(null, { quiet: true });
  rec.pass('BUILD-01.a', 'baseline');
  await RUN[spec.group](env, rec);
  cache.set(spec.group, rec);
  return rec;
}

function verdict(spec, fr, br) {
  const bad = spec.must.filter((id) => br.status(id) !== 'PASS');
  if (bad.length) return ['ERROR', 'baseline does not pass ' + bad.map((id) => id + '=' + br.status(id)).join(', ') + ': the control cannot show anything'];
  const st = spec.must.map((id) => [id, fr.status(id) || 'ERROR', fr.reason(id)]);
  const show = (arr) => arr.map((x) => x[0] + '=' + x[1] + ' (' + x[2].slice(0, 90) + ')').join('; ');
  if (spec.any) {
    if (st.some((x) => x[1] === 'FAIL')) return ['PASS', 'fault made ' + show(st.filter((x) => x[1] === 'FAIL').slice(0, 1))];
    return st.some((x) => x[1] === 'ERROR') ? ['ERROR', 'no named assertion failed and some errored: ' + show(st)] : ['FAIL', 'fault went undetected, ' + show(st)];
  }
  if (st.every((x) => x[1] === 'FAIL')) return ['PASS', 'fault made ' + show(st)];
  if (st.some((x) => x[1] === 'PASS')) return ['FAIL', 'fault went undetected: ' + show(st.filter((x) => x[1] === 'PASS'))];
  return ['ERROR', 'named assertion errored instead of failing: ' + show(st.filter((x) => x[1] === 'ERROR'))];
}

async function runControls(base, rec, mainRec, deadline) {
  const cache = new Map();
  for (const spec of CONTROLS) {
    const cid = 'CONTROL-' + spec.id;
    if (Date.now() > deadline) { rec.error(cid, 'not run: time budget exhausted'); continue; }
    if (NEEDS_BROWSER(spec.group) && !base.browser) { rec.error(cid, 'browser unavailable'); continue; }
    let srv = null;
    try {
      const dir = path.join(base.out, 'controls', spec.id);
      fs.rmSync(dir, { recursive: true, force: true });
      copyDir(base.distDoom, path.join(dir, 'doom'));
      if (spec.prep) {
        try { spec.prep(path.join(dir, 'doom'), base); } catch (e) { rec.error(cid, 'fault could not be applied: ' + e.message); continue; }
      }
      const br = await baselineFor(base, mainRec, cache, spec);
      srv = await startServer(dir);
      const env = Object.assign({}, base, { root: dir, origin: srv.origin, fault: spec.fault || null, fatalLog: [], netLog: [], pagesOpened: 0, stabilityShortRan: false });
      const fr = new Recorder(null, { quiet: true });
      fr.pass('BUILD-01.a', 'control');
      await RUN[spec.group](env, fr);
      const [s, why] = verdict(spec, fr, br);
      rec.set(cid, s, 'must-fail ' + spec.must.join(',') + (spec.any ? ' (any)' : '') + ': ' + why);
    } catch (e) {
      rec.error(cid, 'infrastructure: ' + String(e && e.message || e).split('\n')[0]);
    } finally {
      if (srv) await srv.close();
    }
  }
}

module.exports = { runControls, CONTROL_IDS, CONTROLS };
