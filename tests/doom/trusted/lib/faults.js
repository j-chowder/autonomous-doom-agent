'use strict';
// Black-box fault injection for the negative controls (contract section 6). Faults are browser-side scripts
// (installed with addInitScript, after the probe), request interception, or edits to a disposable copy of dist/doom.
// Nothing here depends on how the candidate is written.
const fs = require('fs');
const path = require('path');
const { walk } = require('./util');

const script = (fn, ...args) => '(' + fn.toString() + ')(' + args.map((a) => JSON.stringify(a)).join(',') + ');';

// VIDEO-01: corrupt the pixels the engine presents. Upload paths are wrapped (2D putImageData, WebGL texImage2D/texSubImage2D with
// a typed array) and every conversion is counted in window.__qaPixelN. Rendering through any other path (drawImage from another canvas
// or an ImageBitmap, WebGL with an ImageData/canvas source, ...) leaves the counter at 0; then the fault is applied at read-back
// (getImageData, which is how readCanvas observes the canvas). The two never both apply, so the fault is always applied exactly once.
function pixelFault(mode) {
  window.__qaPixelN = 0;
  const conv = (src) => {
    window.__qaPixelN++;
    const o = new Uint8Array(src.length);
    for (let i = 0; i + 3 < src.length; i += 4) {
      if (mode === 'swap') { o[i] = src[i + 2]; o[i + 1] = src[i + 1]; o[i + 2] = src[i]; o[i + 3] = src[i + 3]; }
      else { o[i] = src[i]; o[i + 1] = src[i + 1]; o[i + 2] = src[i + 2]; o[i + 3] = 0; }
    }
    return o;
  };
  for (const name of ['CanvasRenderingContext2D', 'OffscreenCanvasRenderingContext2D']) {
    const C = window[name];
    if (!C) continue;
    const orig = C.prototype.putImageData;
    C.prototype.putImageData = function (img, ...rest) {
      return orig.call(this, new ImageData(new Uint8ClampedArray(conv(img.data).buffer), img.width, img.height), ...rest);
    };
  }
  const gi = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function (...a) {
    const img = gi.apply(this, a);
    if (window.__qaPixelN === 0) {
      const d = img.data;
      for (let i = 0; i + 3 < d.length; i += 4) {
        if (mode === 'swap') { const t = d[i]; d[i] = d[i + 2]; d[i + 2] = t; } else d[i + 3] = 0;
      }
    }
    return img;
  };
  for (const name of ['WebGLRenderingContext', 'WebGL2RenderingContext']) {
    const C = window[name];
    if (!C) continue;
    for (const fn of ['texImage2D', 'texSubImage2D']) {
      const orig = C.prototype[fn];
      C.prototype[fn] = function (...a) {
        const last = a[a.length - 1];
        if (last && ArrayBuffer.isView(last)) a[a.length - 1] = conv(new Uint8Array(last.buffer, last.byteOffset, last.byteLength));
        return orig.apply(this, a);
      };
    }
  }
}

// TIME-01: performance.now becomes controllable; the test switches it on after the warmup (window.__qaClock).
function clockFault() {
  const real = Performance.prototype.now;
  let mode = 'off', t0 = 0, scale = 1;
  Performance.prototype.now = function () {
    const r = real.call(this);
    return mode === 'off' ? r : mode === 'freeze' ? t0 : t0 + (r - t0) * scale;
  };
  window.__qaClock = (m, s) => { t0 = real.call(performance); mode = m; scale = s || 1; };
}

// INPUT: a capture listener on window runs before every page handler.
function dropKeys(type) {
  window.addEventListener(type, (e) => { e.stopImmediatePropagation(); }, true);
}
function swapKeys() {
  const MAP = { ArrowUp: 'ArrowDown', ArrowDown: 'ArrowUp', ArrowLeft: 'ArrowRight', ArrowRight: 'ArrowLeft', Control: 'Shift', Shift: 'Control' };
  const INFO = { ArrowUp: ['ArrowUp', 38, 0], ArrowDown: ['ArrowDown', 40, 0], ArrowLeft: ['ArrowLeft', 37, 0], ArrowRight: ['ArrowRight', 39, 0], Control: ['ControlLeft', 17, 1], Shift: ['ShiftLeft', 16, 1] };
  let again = false;
  const h = (e) => {
    if (again || !MAP[e.key]) return;
    e.stopImmediatePropagation();
    const to = MAP[e.key], i = INFO[to];
    const ne = new KeyboardEvent(e.type, { key: to, code: i[0], keyCode: i[1], which: i[1], location: i[2], bubbles: true, cancelable: true });
    try { Object.defineProperty(ne, 'keyCode', { value: i[1] }); Object.defineProperty(ne, 'which', { value: i[1] }); } catch (x) { /* init dict already set it */ }
    again = true;
    try { (e.target || document).dispatchEvent(ne); } finally { again = false; }
  };
  window.addEventListener('keydown', h, true);
  window.addEventListener('keyup', h, true);
}

// INPUT-03
function noPreventDefault() {
  Event.prototype.preventDefault = function () {};
}
function ignoreBlur() {
  const drop = { blur: 1, focusout: 1, visibilitychange: 1, pagehide: 1 };
  const orig = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, ...rest) { if (drop[type]) return; return orig.call(this, type, ...rest); };
  for (const p of ['onblur', 'onfocusout']) { try { Object.defineProperty(window, p, { set() {}, get() { return null; } }); } catch (e) { /* not configurable */ } }
}

// AUDIO-01: nothing reaches a destination node.
function silentAudio() {
  const oc = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (d, ...r) { if (window.AudioDestinationNode && d instanceof AudioDestinationNode) return d; return oc.call(this, d, ...r); };
}

// ---- disposable-copy edits ----
function wasmIn(distDoom) {
  const w = walk(distDoom).filter((f) => /\.wasm$/i.test(f));
  if (w.length !== 1) throw new Error('expected exactly one wasm in the copy, found ' + w.length);
  return w[0];
}
function truncateWasm(distDoom) {
  const f = wasmIn(distDoom), b = fs.readFileSync(f);
  fs.writeFileSync(f, b.subarray(0, Math.floor(b.length / 2)));
}
function replaceWasmWithoutHooks(distDoom) {
  // An empty but valid module: \0asm, version 1. Validates, exports nothing.
  fs.writeFileSync(wasmIn(distDoom), Buffer.from([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]));
}
// Remove every file that carries the WAD bytes. Separate data files are deleted; if the WAD is embedded in code
// (.js/.wasm/.html) the span is zero-filled in place so the file stays loadable. Returns what was done.
function removeWadData(distDoom, wadBuf) {
  const sig = wadBuf.subarray(0, 256);
  const done = [];
  for (const f of walk(distDoom)) {
    const b = fs.readFileSync(f);
    const at = b.indexOf(sig);
    if (at < 0) continue;
    if (/\.(js|mjs|wasm|html?)$/i.test(f)) { b.fill(0, at, Math.min(b.length, at + wadBuf.length)); fs.writeFileSync(f, b); done.push('zeroed WAD span in ' + path.relative(distDoom, f)); }
    else { fs.unlinkSync(f); done.push('deleted ' + path.relative(distDoom, f)); }
  }
  if (!done.length) throw new Error('no file under dist/doom contains the WAD bytes (compressed or encoded packaging); fault cannot be applied');
  return done;
}

// ---- request interception ----
const isWasm = (req) => /\.wasm$/i.test(new URL(req.url()).pathname);
const routes = {
  blockWasm: async (route, req) => { if (isWasm(req)) { await route.abort('failed'); return true; } return false; },
  wasmAsText: async (route, req) => {
    if (!isWasm(req)) return false;
    const resp = await route.fetch();
    await route.fulfill({ response: resp, headers: Object.assign({}, resp.headers(), { 'content-type': 'text/plain' }) });
    return true;
  },
  // Everything under /doom/ except the entry page 404s, as if relative asset paths pointed at the wrong place.
  brokenAssets: async (route, req) => {
    const p = new URL(req.url()).pathname;
    if (p.startsWith('/doom/') && !/^\/doom\/(index\.html)?$/.test(p)) { await route.fulfill({ status: 404, contentType: 'text/plain', body: 'not found' }); return true; }
    return false;
  },
};

// ---- after-warmup page actions ----
const after = {
  freeze: async (g) => { await g.page.evaluate(() => window.__qaClock('freeze', 1)); },
  scale2: async (g) => { await g.page.evaluate(() => window.__qaClock('scale', 2)); },
  busy3s: async (g) => {
    await g.page.evaluate(() => { setTimeout(() => { const s = window.__qa.now(); while (window.__qa.now() - s < 3000); }, 1500); });
  },
  throwUncaught: async (g) => { await g.page.evaluate(() => { setTimeout(() => { throw new Error('qa-injected uncaught exception'); }, 100); }); },
};

module.exports = { script, pixelFault, clockFault, dropKeys, swapKeys, noPreventDefault, ignoreBlur, silentAudio,
  truncateWasm, replaceWasmWithoutHooks, removeWadData, routes, after };
