'use strict';
// Trusted DOOM acceptance runner. Usage: node main.js GATE   (normally started by run.sh)
// Output: "ASSERT <ID> PASS|FAIL|ERROR <reason>" per assertion, /out/results.json, exit 0 / 1 / 70 (contract section 4).
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');
const { Recorder, mkdirp, walk, nowMs } = require('./lib/util');
const { loadWad, loadGamma0, palette0, playerStart, freeDistance, statusBarRefs } = require('./lib/wad');
const { startServer } = require('./lib/site');
const { buildGroup } = require('./groups/build');
const { bootGroup } = require('./groups/boot');
const { stabilityShort, finalizeStability, soak } = require('./groups/stability');
const { videoGroup } = require('./groups/video');
const { timeGroup } = require('./groups/time');
const { input01, input02, input03 } = require('./groups/input');
const { audioGroup } = require('./groups/audio');
const { runControls, CONTROL_IDS } = require('./controls');

const GATES = ['build', 'boot', 'm1', 'video', 'time', 'input', 'audio', 'final'];
const GATE_IDS = {
  build: ['BUILD-01.a', 'BUILD-01.b', 'BUILD-01.c', 'BUILD-01.d', 'BUILD-01.e'],
  boot: ['LOAD-01.a', 'LOAD-01.b', 'LOAD-01.c', 'WAD-01.a', 'WAD-01.b'],
  m1: ['STABILITY-01.a', 'DEPLOY-01.a', 'DEPLOY-01.b'],
  video: ['VIDEO-01.a', 'VIDEO-01.b', 'VIDEO-01.c'],
  time: ['TIME-01.a', 'TIME-02.a', 'TIME-02.b'],
  input: ['INPUT-01.a', 'INPUT-01.b', 'INPUT-01.c', 'INPUT-01.d', 'INPUT-01.e', 'INPUT-01.f', 'INPUT-02.a', 'INPUT-03.a', 'INPUT-03.b', 'INPUT-03.c'],
  audio: ['AUDIO-01.a', 'AUDIO-01.b'],
  final: ['STABILITY-01.b'].concat(CONTROL_IDS),
};
// Wall-clock budget per gate inside the verification container (loop/doom_setup.py IN_CONTAINER_S), minus slack for reporting.
const BUDGET_S = { build: 600, boot: 600, m1: 900, video: 1000, time: 1200, input: 1500, audio: 1500, final: 3000, controls: 3000 };

function sha256(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }
function testHash() {
  const h = crypto.createHash('sha256');
  for (const f of walk(__dirname).sort()) h.update(path.relative(__dirname, f) + '\0').update(fs.readFileSync(f));
  return h.digest('hex');
}
function candidateSha(workspace) {
  if (process.env.CANDIDATE_SHA) return process.env.CANDIDATE_SHA;
  const r = spawnSync('git', ['-C', workspace, 'rev-parse', 'HEAD'], { encoding: 'utf8', timeout: 10000 });
  return r.status === 0 ? r.stdout.trim() : null;
}

async function main() {
  const gate = process.argv[2];
  if (!GATES.includes(gate) && gate !== 'controls') { console.error('usage: main.js ' + GATES.concat('controls').join('|')); process.exit(2); }
  const workspace = process.env.WORKSPACE || '/workspace';
  const out = mkdirp(process.env.OUT || '/out');
  const work = process.env.WORK || path.join(out, 'work');
  const started = new Date();
  const t0 = nowMs();
  const budgetS = Number(process.env.QA_BUDGET_S) || BUDGET_S[gate];
  const deadline = Date.now() + (budgetS - 60) * 1000;
  const gi = GATES.indexOf(gate);
  const upTo = (g) => GATES.indexOf(g) <= gi;

  const scope = gate === 'controls' ? ['BUILD-01.a', 'BUILD-01.c'].concat(CONTROL_IDS) : GATES.filter(upTo).reduce((a, g) => a.concat(GATE_IDS[g]), []);
  const rec = new Recorder(scope);
  const env = { out, work, workspace, fatalLog: [], netLog: [], pagesOpened: 0, artifacts: { logs: [], screenshots: [] }, fault: null };
  const notes = [];

  let finished = false;
  const finish = (reason) => {
    if (finished) return;
    finished = true;
    rec.closeOut(reason);
    const code = rec.anyStatus('FAIL') ? 1 : rec.anyStatus('ERROR') ? 70 : 0;
    const results = {
      schema: 1, gate, command: 'run.sh ' + gate, candidate_sha: candidateSha(workspace), test_hash: testHash(), image_id: process.env.IMAGE_ID || null,
      wad_sha256: env.wadSha || null, browser: env.browserVersion || null, started: started.toISOString(), finished: new Date().toISOString(),
      duration_s: Math.round((nowMs() - t0) / 1000), exit_code: code, notes,
      assertions: rec.list().map((r) => ({ id: r.id, status: r.status, reason: r.reason })), measurements: rec.meas,
      page_events: { fatal: env.fatalLog, blocked_network: env.netLog }, artifacts: env.artifacts,
    };
    try { fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); } catch (e) { console.error('cannot write results.json: ' + e.message); }
    const c = { PASS: 0, FAIL: 0, ERROR: 0 }; for (const r of rec.list()) c[r.status]++;
    console.log('SUMMARY gate=' + gate + ' PASS=' + c.PASS + ' FAIL=' + c.FAIL + ' ERROR=' + c.ERROR + ' exit=' + code);
    return code;
  };
  for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, () => { process.exit(finish('interrupted by ' + sig) || 70); });

  // ---- references from the WAD and engine source (no network) ----
  try {
    const wadFile = [path.join(work, 'assets', 'doom1.wad'), path.join(workspace, 'assets', 'doom1.wad')].find((f) => fs.existsSync(f));
    const tables = [path.join(work, 'third_party', 'doomgeneric', 'tables.c'), path.join(workspace, 'third_party', 'doomgeneric', 'tables.c')].find((f) => fs.existsSync(f));
    if (!wadFile || !tables) throw new Error('assets/doom1.wad or third_party/doomgeneric/tables.c not found');
    const wad = loadWad(wadFile);
    env.wadBuf = wad.buf; env.wadSha = sha256(wadFile);
    env.pal = palette0(wad, loadGamma0(tables));
    env.start = playerStart(wad, 'E1M1');
    env.refs = statusBarRefs(wad, env.pal);
    // The input tests need a straight run to the north of the start and a short one to the south (README "open position").
    const north = Math.min(...[-16, 0, 16].map((l) => freeDistance(wad, 'E1M1', env.start.x, env.start.y, 90, l)));
    const south = Math.min(...[-16, 0, 16].map((l) => freeDistance(wad, 'E1M1', env.start.x, env.start.y, 270, l)));
    env.open = { north, south };
    if (env.start.angle !== 90 || north < 700 || south < 60) throw new Error('E1M1 geometry differs from the derivation: angle ' + env.start.angle + ', north ' + north + ', south ' + south);
  } catch (e) { env.refsError = String(e.message); notes.push('reference setup failed: ' + env.refsError); }

  // ---- build ----
  const buildTimeoutMs = Math.min(480, Math.floor(budgetS * 0.7)) * 1000;
  try { await buildGroup(env, rec, { timeoutMs: buildTimeoutMs }); }
  catch (e) { rec.error('BUILD-01.a', 'infrastructure: ' + String(e.message).split('\n')[0]); }
  env.distDoom = path.join(work, 'dist', 'doom');

  const needsBrowser = gate !== 'build';
  const buildOk = rec.status('BUILD-01.a') === 'PASS' && rec.status('BUILD-01.c') === 'PASS';
  let server = null, browser = null;
  if (needsBrowser && buildOk) {
    try {
      server = await startServer(path.join(work, 'dist'));
      env.origin = server.origin;
      let pw;
      try { pw = require('playwright'); } catch (e) { throw new Error('require("playwright") failed: ' + e.message); }
      browser = env.browser = await pw.chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'] });
      env.browserVersion = 'chromium ' + browser.version();
      if (!/^141\./.test(browser.version())) notes.push('contract names Playwright 1.56.1 Chromium (141.x); running ' + browser.version());
    } catch (e) { env.infraError = String(e.message).split('\n')[0]; notes.push('browser setup failed: ' + env.infraError); }
  }

  const left = () => deadline - Date.now();
  const run = async (name, needS, fn) => {
    if (env.infraError || env.refsError) return;
    if (left() < needS * 1000) { notes.push('skipped ' + name + ': ' + Math.round(left() / 1000) + ' s left of budget'); return; }
    try { await fn(); } catch (e) { notes.push(name + ' threw: ' + String(e && e.stack || e).split('\n').slice(0, 3).join(' | ')); }
  };

  if (needsBrowser && buildOk && gate !== 'controls') {
    const deploy = upTo('m1');
    if (gi >= 1) await run('boot', 60, () => bootGroup(env, rec, { deploy }));
    if (gi >= 2) await run('stability-short', 40, () => stabilityShort(env, rec));
    if (gi >= 3) await run('video', 60, () => videoGroup(env, rec));
    const windowMs = gate === 'final' ? 60000 : 10000;
    if (gi >= 4) await run('time', windowMs / 1000 + 40, () => timeGroup(env, rec, { windowMs }));
    if (gi >= 5) {
      await run('input01', 150, () => input01(env, rec));
      await run('input02', 40, () => input02(env, rec));
      await run('input03', 60, () => input03(env, rec));
    }
    if (gi >= 6) await run('audio', 50, () => audioGroup(env, rec));
    if (gate === 'final') await run('soak', 100, () => soak(env, rec));
    if (gi >= 2) finalizeStability(env, rec);
  }
  if (needsBrowser && buildOk && (gate === 'final' || gate === 'controls')) {
    await run('controls', 60, () => runControls(env, rec, rec, deadline));
  }

  if (browser) { try { await browser.close(); } catch (e) { /* ignore */ } }
  if (server) await server.close();
  let why = 'not run';
  if (rec.status('BUILD-01.a') !== 'PASS' && gate !== 'build') why = 'not run: BUILD-01.a ' + (rec.status('BUILD-01.a') || 'did not run');
  else if (env.refsError) why = 'not run: ' + env.refsError;
  else if (env.infraError) why = 'not run: ' + env.infraError;
  else if (left() < 0) why = 'not run: time budget exhausted';
  process.exit(finish(why));
}

main().catch((e) => { console.error('runner crashed: ' + (e && e.stack || e)); process.exit(70); });
