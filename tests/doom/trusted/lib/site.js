'use strict';
// Static server on loopback, the in-page probe script, and the helpers every browser test uses.
// Nothing here knows how the candidate is structured: it only relies on section 3 of the contract.
const fs = require('fs');
const path = require('path');
const http = require('http');
const { nowMs, sleep, withTimeout, SetupError, mkdirp } = require('./util');

const MIME = {
  '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json',
  '.wasm': 'application/wasm', '.png': 'image/png', '.ico': 'image/x-icon', '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8', '.data': 'application/octet-stream', '.wad': 'application/octet-stream',
};

// Serves `root` (the directory that contains doom/) on 127.0.0.1. Only a plain Content-Type, Content-Length and
// Cache-Control are sent: no COOP/COEP, so SharedArrayBuffer is unavailable exactly as contract section 3.6 requires.
function startServer(root) {
  const log = [];
  const srv = http.createServer((req, res) => {
    let p;
    try { p = decodeURIComponent(new URL(req.url, 'http://x').pathname); } catch (e) { res.writeHead(400); res.end(); return; }
    const done = (code, extra) => { log.push({ url: req.url, status: code }); return extra; };
    if (p === '/doom') { done(301); res.writeHead(301, { Location: '/doom/' }); res.end(); return; }
    let file = path.join(root, p);
    if (!file.startsWith(path.resolve(root))) { done(403); res.writeHead(403); res.end(); return; }
    try {
      let st = fs.statSync(file);
      if (st.isDirectory()) { file = path.join(file, 'index.html'); st = fs.statSync(file); }
      const type = MIME[path.extname(file).toLowerCase()] || 'application/octet-stream';
      done(200);
      res.writeHead(200, { 'Content-Type': type, 'Content-Length': st.size, 'Cache-Control': 'no-store' });
      if (req.method === 'HEAD') { res.end(); return; }
      fs.createReadStream(file).pipe(res);
    } catch (e) { done(404); res.writeHead(404, { 'Content-Type': 'text/plain' }); res.end('not found'); }
  });
  return new Promise((resolve, reject) => {
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port;
      resolve({ port, origin: 'http://127.0.0.1:' + port, log, close: () => new Promise((r) => srv.close(() => r())) });
    });
  });
}

// ---- In-page probe (runs before any page script via addInitScript). Never touches the engine. ----
function baseInit() {
  if (window.__qa) return;
  const realNow = performance.now.bind(performance); // survives later faults that replace performance.now
  const qa = window.__qa = { now: realNow, errs: [] };
  window.addEventListener('error', (e) => { if (e && e.target && e.target !== window) return; qa.errs.push('error: ' + (e && e.message)); }, true);
  window.addEventListener('unhandledrejection', (e) => { qa.errs.push('unhandledrejection: ' + (e && e.reason && (e.reason.stack || e.reason.message || e.reason))); });

  qa.snap = () => {
    const M = (typeof Module !== 'undefined') ? Module : null;
    const q = (n) => { try { const f = M && M['_DG_TEST_' + n]; return typeof f === 'function' ? f.call(M) : null; } catch (e) { return null; } };
    const a = q('GetViewAngle');
    return { tic: q('GetGameTic'), frame: q('GetFrameCount'), x: q('GetPlayerX'), y: q('GetPlayerY'), ang: a === null ? null : (a >>> 0), ammo: q('GetAmmo'), rnow: realNow(), pnow: performance.now(), errs: qa.errs.length };
  };
  qa.poll = () => {
    try {
      if (typeof Module === 'undefined' || !Module) return { s: 'no global Module' };
      if (typeof Module._DG_TEST_GetGameTic !== 'function') return { s: 'Module._DG_TEST_GetGameTic is not a function' };
      return { s: 'ok', snap: qa.snap() };
    } catch (e) { return { s: 'threw ' + String(e).slice(0, 80) }; }
  };

  // Own requestAnimationFrame loop: the largest gap between callbacks (TIME-02.a). Uses the unfaulted clock.
  const raf = qa.raf = { last: null, max: 0, count: 0 };
  const tick = () => { const t = realNow(); if (raf.last !== null && t - raf.last > raf.max) raf.max = t - raf.last; raf.last = t; raf.count++; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  qa.rafReset = () => { raf.max = 0; raf.count = 0; };
  qa.rafRead = () => { const t = realNow(); return { max: Math.max(raf.max, raf.last === null ? 1e9 : t - raf.last), count: raf.count, now: t }; };

  // Audio tap: every connection to an AudioContext destination goes through an AnalyserNode first.
  const au = qa.audio = { ctxs: [], samples: [] };
  const Orig = window.AudioContext || window.webkitAudioContext;
  if (Orig && window.AudioNode) {
    const origConnect = AudioNode.prototype.connect, origDisconnect = AudioNode.prototype.disconnect;
    const taps = new WeakMap();
    const tapFor = (ctx) => {
      let t = taps.get(ctx);
      if (!t) { t = ctx.createAnalyser(); t.fftSize = 16384; origConnect.call(t, ctx.destination); taps.set(ctx, t); }
      return t;
    };
    AudioNode.prototype.connect = function (dest, ...rest) {
      try {
        if (window.AudioDestinationNode && dest instanceof AudioDestinationNode && dest.context === this.context) {
          origConnect.call(this, tapFor(this.context), ...rest);
          return dest;
        }
      } catch (e) { /* fall through to the real connect */ }
      return origConnect.call(this, dest, ...rest);
    };
    AudioNode.prototype.disconnect = function (dest, ...rest) {
      try {
        if (window.AudioDestinationNode && dest instanceof AudioDestinationNode && taps.get(this.context)) return origDisconnect.call(this, taps.get(this.context), ...rest);
      } catch (e) { /* fall through */ }
      return origDisconnect.apply(this, arguments);
    };
    class QAAudioContext extends Orig {
      constructor(...a) { super(...a); au.ctxs.push(this); try { tapFor(this); } catch (e) { /* tap unavailable */ } }
    }
    window.AudioContext = QAAudioContext;
    if (window.webkitAudioContext) window.webkitAudioContext = QAAudioContext;
    const buf = new Float32Array(16384);
    setInterval(() => {
      for (const c of au.ctxs) {
        const t = taps.get(c);
        if (!t) continue;
        try {
          t.getFloatTimeDomainData(buf);
          let peak = 0, sum = 0;
          for (let i = 0; i < buf.length; i++) { const v = Math.abs(buf[i]); if (v > peak) peak = v; sum += v * v; }
          if (au.samples.length < 200000) au.samples.push({ t: realNow(), peak, rms: Math.sqrt(sum / buf.length), state: c.state });
        } catch (e) { /* ignore */ }
      }
    }, 25);
  }
  qa.audioInfo = () => ({ n: au.ctxs.length, states: au.ctxs.map((c) => c.state), samples: au.samples.length });
  qa.audioPeakSince = (from) => { let peak = 0, rms = 0; for (let i = from; i < au.samples.length; i++) { if (au.samples[i].peak > peak) peak = au.samples[i].peak; if (au.samples[i].rms > rms) rms = au.samples[i].rms; } return { peak, rms, n: au.samples.length - from }; };
}
const BASE_INIT = '(' + baseInit.toString() + ')();';

const FATAL_RE = /(^|\s)Aborted\(|RuntimeError:|program (has already )?aborted|\bI_Error\b|program exited \(with status: [1-9]|^\s*Error:|Uncaught/i;
const wadEvidenceRe = /iwad|\.wad|doom1|no such file|could not open|cannot open|file not found|not found|404|preload|file_packager|\.data/i;

function safeEval(page, fn, ms, arg) { return withTimeout(page.evaluate(fn, arg), ms || 5000, 'page.evaluate'); }

// Open the game page in a fresh browser context and wait for the first game tic (LOAD-01.a procedure).
// env: { browser, origin, out, fault, fatalLog }. Returns g; g.firstSnap is set on success, g.failReason otherwise.
async function openGame(env, opts) {
  opts = opts || {};
  const label = opts.label || 'page';
  const query = opts.query != null ? opts.query : '?warp=E1M1&nomonsters=1';
  const timeout = opts.startupTimeout || 20000;
  env.pagesOpened = (env.pagesOpened || 0) + 1;
  const g = { env, label, firstSnap: null, startMs: null, failReason: null, closing: false, log: [], shots: [],
    events: { fatal: [], bad: [], failed: [], responses: [], net: [] } };
  g.ctx = await env.browser.newContext({ viewport: { width: 1024, height: 768 }, serviceWorkers: 'block' });
  await g.ctx.addInitScript(BASE_INIT);
  if (env.fault && env.fault.init) await g.ctx.addInitScript(env.fault.init);
  await g.ctx.route('**/*', async (route, request) => {
    try {
      const url = request.url();
      if (!url.startsWith(env.origin + '/')) { g.events.net.push(url); await route.abort('blockedbyclient'); return; }
      if (env.fault && env.fault.route && await env.fault.route(route, request)) return;
      await route.continue();
    } catch (e) { /* page closed while routing */ }
  });
  const page = g.page = await g.ctx.newPage();
  page.on('pageerror', (err) => g.events.fatal.push({ kind: 'pageerror', text: String(err && err.message || err).slice(0, 300) }));
  page.on('crash', () => g.events.fatal.push({ kind: 'crash', text: 'page crashed' }));
  page.on('close', () => { if (!g.closing) g.events.fatal.push({ kind: 'close', text: 'page closed unexpectedly' }); });
  page.on('console', (m) => {
    const text = m.text();
    if (g.log.length < 3000) g.log.push('[' + m.type() + '] ' + text.slice(0, 500));
    if (m.type() === 'error' && FATAL_RE.test(text)) g.events.fatal.push({ kind: 'abort', text: text.slice(0, 300) });
  });
  page.on('response', (r) => {
    try {
      const u = new URL(r.url());
      if (u.origin !== env.origin) return;
      const rec = { path: u.pathname, status: r.status(), type: r.headers()['content-type'] || '' };
      g.events.responses.push(rec);
      if (r.status() >= 400 && !/favicon\.ico$/.test(u.pathname)) g.events.bad.push(rec);
    } catch (e) { /* ignore */ }
  });
  page.on('requestfailed', (req) => {
    try {
      const u = new URL(req.url());
      if (u.origin !== env.origin || /favicon\.ico$/.test(u.pathname)) return;
      g.events.failed.push({ path: u.pathname, err: (req.failure() && req.failure().errorText) || '' });
    } catch (e) { /* ignore */ }
  });

  const url = env.origin + '/doom/index.html' + query;
  g.url = url;
  const t0 = g.t0 = nowMs();
  try { await page.goto(url, { waitUntil: 'commit', timeout }); } catch (e) { g.failReason = 'navigation failed: ' + String(e.message).split('\n')[0]; }
  let last = 'no answer yet', fatalSeen = null;
  while (!g.firstSnap && !g.failReason) {
    if (nowMs() - t0 > timeout) { g.failReason = 'no GetGameTic() > 0 within ' + timeout + ' ms (' + last + ')'; break; }
    let r = null;
    try { r = await safeEval(page, () => window.__qa.poll(), 1500); } catch (e) { last = 'page not answering: ' + String(e.message).slice(0, 60); }
    if (r) {
      last = r.s + (r.snap ? ' tic=' + r.snap.tic : '');
      if (r.s === 'ok' && r.snap.tic > 0) { g.firstSnap = r.snap; g.startMs = nowMs() - t0; break; }
    }
    const critical = (x) => /\.(js|mjs|wasm|data|wad|html)$|\/$/i.test(x.path);
    const dead = g.events.fatal.filter((f) => f.kind !== 'pageerror').concat(g.events.bad.filter(critical), g.events.failed.filter(critical));
    if (dead.length) {
      // An abort, crash or failed critical resource ends the wait early (after a short grace) instead of burning the full timeout.
      if (fatalSeen === null) fatalSeen = nowMs();
      else if (nowMs() - fatalSeen > 1500) {
        const e = dead[0];
        g.failReason = 'startup failed before the first tic: ' + JSON.stringify(e).slice(0, 200) + ' (' + last + ')';
        break;
      }
    }
    await sleep(20);
  }
  g.startupFatal = g.events.fatal.slice();
  return g;
}

const snap = (g, ms) => safeEval(g.page, () => window.__qa.snap(), ms || 5000);

// Poll until the engine's game tic reaches `target`. Returns the snapshot, or null on timeout.
async function waitTic(g, target, maxMs) {
  const t0 = nowMs();
  for (;;) {
    const s = await snap(g, 4000);
    if (s.tic !== null && s.tic >= target) return s;
    if (nowMs() - t0 > maxMs) return null;
    await sleep(6);
  }
}

// Warmup (wall clock, after the first tic) and then wait until the game has advanced `minTics` more tics.
async function settle(g, opts) {
  opts = opts || {};
  if (!g.firstSnap) throw new SetupError('game never started');
  await sleep(opts.warmMs == null ? 1500 : opts.warmMs);
  const s = await snap(g);
  const s2 = await waitTic(g, (s.tic || 0) + (opts.minTics == null ? 35 : opts.minTics), opts.maxMs || 12000);
  if (!s2) throw new SetupError('game did not advance ' + (opts.minTics || 35) + ' tics within ' + (opts.maxMs || 12000) + ' ms after warmup');
  return s2;
}

// Hold keys until the game tic advances `nTics`. Snapshots bracket the window, so distances are per game tic.
async function holdKeys(g, keys, nTics, opts) {
  opts = opts || {};
  const kb = g.page.keyboard;
  const s0 = await snap(g);
  for (const k of keys) await kb.down(k);
  let s1 = null;
  try { s1 = await waitTic(g, s0.tic + nTics, nTics * 80 + 5000); }
  finally { if (!opts.noRelease) for (const k of keys.slice().reverse()) await kb.up(k); }
  if (!s1) throw new SetupError('game did not advance ' + nTics + ' tics while keys were held');
  return { s0, s1, nT: s1.tic - s0.tic };
}

// Read the canvas through a scratch 2D canvas (works for 2D and WebGL canvases). Returns {w,h,data} or {err}.
async function readCanvas(g) {
  const r = await safeEval(g.page, async () => {
    const c = document.getElementById('canvas');
    if (!c) return { err: 'no <canvas id="canvas"> element' };
    const w = c.width, h = c.height;
    const grab = () => {
      const s = document.createElement('canvas'); s.width = w; s.height = h;
      const x = s.getContext('2d', { willReadFrequently: true });
      x.drawImage(c, 0, 0);
      return x.getImageData(0, 0, w, h).data;
    };
    let d;
    try { d = grab(); if (!d.some((v) => v !== 0)) { await new Promise((res) => requestAnimationFrame(res)); d = grab(); } }
    catch (e) { return { err: 'cannot read canvas: ' + e.message, w, h }; }
    let s = '';
    for (let i = 0; i < d.length; i += 0x8000) s += String.fromCharCode.apply(null, d.subarray(i, i + 0x8000));
    return { w, h, b64: btoa(s) };
  }, 15000);
  if (r.err) return r;
  return { w: r.w, h: r.h, data: Buffer.from(r.b64, 'base64') };
}

async function shot(g, name) {
  try {
    const dir = mkdirp(path.join(g.env.out, 'screenshots'));
    const file = path.join(dir, g.label + '-' + name + '.png');
    await g.page.screenshot({ path: file, timeout: 5000 });
    g.shots.push(file);
    if (g.env.artifacts) g.env.artifacts.screenshots.push(file);
  } catch (e) { /* evidence only */ }
}

// Close the page. Collects uncaught errors the page recorded and appends them to env.fatalLog (STABILITY-01.a).
async function closeGame(g) {
  g.closing = true;
  try {
    const errs = await safeEval(g.page, () => window.__qa.errs.slice(), 1500);
    for (const e of errs) g.events.fatal.push({ kind: 'js', text: String(e).slice(0, 300) });
  } catch (e) { /* page already gone or hung */ }
  try {
    const dir = mkdirp(path.join(g.env.out, 'logs'));
    const file = path.join(dir, g.label + '.log');
    fs.writeFileSync(file, 'url: ' + g.url + '\nfatal: ' + JSON.stringify(g.events.fatal) + '\nbad: ' + JSON.stringify(g.events.bad) +
      '\nfailed: ' + JSON.stringify(g.events.failed) + '\nblocked-network: ' + JSON.stringify(g.events.net) + '\n--- console ---\n' + g.log.join('\n') + '\n');
    if (g.env.artifacts) g.env.artifacts.logs.push(file);
  } catch (e) { /* evidence only */ }
  if (g.env.fatalLog) for (const f of g.events.fatal) g.env.fatalLog.push({ label: g.label, kind: f.kind, text: f.text });
  if (g.env.netLog) for (const u of g.events.net) g.env.netLog.push({ label: g.label, url: u });
  try { await withTimeout(g.ctx.close(), 10000, 'context.close'); } catch (e) { /* ignore */ }
}

// Run a group body with a game page; always closes the page. Errors thrown by `fn` propagate.
async function withGame(env, opts, fn) {
  const g = await openGame(env, opts);
  try { return await fn(g); } finally { await closeGame(g); }
}

function copyDir(src, dst) {
  mkdirp(dst);
  fs.cpSync(src, dst, { recursive: true });
}

module.exports = { startServer, BASE_INIT, openGame, closeGame, withGame, snap, waitTic, settle, holdKeys, readCanvas, shot,
  safeEval, copyDir, wadEvidenceRe, FATAL_RE, MIME };
