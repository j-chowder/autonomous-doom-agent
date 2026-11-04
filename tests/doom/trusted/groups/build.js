'use strict';
// BUILD-01.a-e. The build runs in the disposable copy; the dist checks also run against control copies.
const fs = require('fs');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const { walk, mkdirp } = require('../lib/util');
const { checkHookExports } = require('../lib/wasm');

function runBuild(work, logFile, timeoutMs) {
  return new Promise((resolve) => {
    if (!fs.existsSync(path.join(work, 'build.sh'))) { resolve({ code: null, why: 'build.sh not found in the candidate root' }); return; }
    const out = fs.openSync(logFile, 'w');
    const child = spawn('bash', ['build.sh'], { cwd: work, detached: true, stdio: ['ignore', out, out], env: process.env });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; try { process.kill(-child.pid, 'SIGKILL'); } catch (e) { /* gone */ } }, timeoutMs);
    child.on('error', (e) => { clearTimeout(timer); resolve({ code: null, why: 'cannot start bash: ' + e.message }); });
    child.on('exit', (code, sig) => { clearTimeout(timer); resolve({ code, sig, timedOut }); });
  });
}

// c, d, e against a directory laid out like dist/doom.
function distChecks(distDoom, rec) {
  let hasIndex = false, wasms = [];
  try { hasIndex = fs.existsSync(path.join(distDoom, 'index.html')); wasms = walk(distDoom).filter((f) => /\.wasm$/i.test(f)); } catch (e) { /* missing dir */ }
  if (hasIndex && wasms.length === 1) rec.pass('BUILD-01.c', 'dist/doom/index.html and one wasm (' + path.basename(wasms[0]) + ')');
  else rec.fail('BUILD-01.c', 'index.html ' + (hasIndex ? 'present' : 'missing') + '; ' + wasms.length + ' .wasm file(s) under dist/doom (need exactly 1)');
  if (rec.blockedBy('BUILD-01.d', ['BUILD-01.c']) | rec.blockedBy('BUILD-01.e', ['BUILD-01.c'])) return null;
  const buf = fs.readFileSync(wasms[0]);
  let valid = false;
  try { valid = WebAssembly.validate(buf); } catch (e) { valid = false; }
  if (valid) rec.pass('BUILD-01.d', 'WebAssembly.validate true, ' + buf.length + ' bytes'); else rec.fail('BUILD-01.d', 'WebAssembly.validate is false for ' + path.basename(wasms[0]) + ' (' + buf.length + ' bytes)');
  const h = checkHookExports(buf);
  if (h.ok) rec.pass('BUILD-01.e', h.reason); else rec.fail('BUILD-01.e', h.reason);
  return wasms[0];
}

async function buildGroup(env, rec, opts) {
  const logFile = path.join(mkdirp(path.join(env.out, 'logs')), 'build.log');
  const t0 = Date.now();
  const r = await runBuild(env.work, logFile, opts.timeoutMs);
  env.artifacts.logs.push(logFile);
  if (r.code === 0) rec.pass('BUILD-01.a', 'bash build.sh exited 0 in ' + Math.round((Date.now() - t0) / 1000) + ' s');
  else rec.fail('BUILD-01.a', r.why || (r.timedOut ? 'build.sh exceeded ' + Math.round(opts.timeoutMs / 1000) + ' s' : 'build.sh exit ' + r.code + (r.sig ? ' signal ' + r.sig : '')) + ' (see build.log)');

  const v = spawnSync('emcc', ['--version'], { encoding: 'utf8', timeout: 60000 });
  if (v.error) rec.error('BUILD-01.b', 'cannot run emcc: ' + v.error.message);
  else {
    const first = ((v.stdout || '') + (v.stderr || '')).split('\n')[0].trim();
    if (/\b4\.0\.18\b/.test(first)) rec.pass('BUILD-01.b', first.slice(0, 120));
    else rec.fail('BUILD-01.b', 'emcc --version reports: ' + first.slice(0, 120));
  }

  if (rec.blockedBy('BUILD-01.c', ['BUILD-01.a'])) { rec.blockedBy('BUILD-01.d', ['BUILD-01.a']); rec.blockedBy('BUILD-01.e', ['BUILD-01.a']); return; }
  env.wasmFile = distChecks(path.join(env.work, 'dist', 'doom'), rec);
}

module.exports = { buildGroup, distChecks };
