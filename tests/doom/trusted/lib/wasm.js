'use strict';
// Minimal WebAssembly binary reader: type, import, function and export sections only.
// Used to read the DG_TEST_* exports from the binary itself, not from the page.

function reader(buf) {
  let p = 0;
  const u8 = () => { if (p >= buf.length) throw new Error('unexpected end of wasm'); return buf[p++]; };
  const leb = () => { let r = 0, s = 0, b; do { b = u8(); r += (b & 0x7f) * Math.pow(2, s); s += 7; } while (b & 0x80 && s < 70); return r; };
  const str = () => { const n = leb(); if (p + n > buf.length) throw new Error('bad string'); const t = buf.toString('utf8', p, p + n); p += n; return t; };
  return { u8, leb, str, pos: () => p, set: (x) => { p = x; }, len: buf.length };
}

function parseWasm(buf) {
  if (buf.length < 8 || buf.readUInt32LE(0) !== 0x6d736100) throw new Error('not a wasm binary (bad magic)');
  const r = reader(buf);
  r.set(8);
  const types = [], funcTypeIdx = [], exports = [];
  let importedFuncs = 0;
  while (r.pos() < r.len) {
    const id = r.u8(); const size = r.leb(); const end = r.pos() + size;
    if (end > r.len) throw new Error('section ' + id + ' overruns file (truncated)');
    if (id === 1) {
      const n = r.leb();
      for (let i = 0; i < n; i++) {
        if (r.u8() !== 0x60) throw new Error('bad func type');
        const np = r.leb(); const params = []; for (let k = 0; k < np; k++) params.push(r.u8());
        const nr = r.leb(); const results = []; for (let k = 0; k < nr; k++) results.push(r.u8());
        types.push({ params, results });
      }
    } else if (id === 2) {
      const n = r.leb();
      for (let i = 0; i < n; i++) {
        r.str(); r.str(); const kind = r.u8();
        if (kind === 0) { funcTypeIdx.push(r.leb()); importedFuncs++; }
        else if (kind === 1) { r.u8(); const f = r.leb(); r.leb(); if (f & 1) r.leb(); }
        else if (kind === 2) { const f = r.leb(); r.leb(); if (f & 1) r.leb(); }
        else if (kind === 3) { r.u8(); r.u8(); }
        else if (kind === 4) { r.u8(); r.leb(); }
        else throw new Error('unknown import kind ' + kind);
      }
    } else if (id === 3) {
      const n = r.leb(); for (let i = 0; i < n; i++) funcTypeIdx.push(r.leb());
    } else if (id === 7) {
      const n = r.leb();
      for (let i = 0; i < n; i++) { const name = r.str(); const kind = r.u8(); const index = r.leb(); exports.push({ name, kind, index }); }
    }
    r.set(end);
  }
  const out = exports.map((e) => {
    const o = { name: e.name, kind: e.kind };
    if (e.kind === 0) { const t = types[funcTypeIdx[e.index]]; o.params = t ? t.params.length : null; o.results = t ? t.results.slice() : null; }
    return o;
  });
  return { exports: out };
}

const HOOKS = ['GetGameTic', 'GetFrameCount', 'GetPlayerX', 'GetPlayerY', 'GetViewAngle', 'GetAmmo'].map((n) => 'DG_TEST_' + n);

// Returns {ok, reason}. Names may carry a leading underscore depending on the toolchain; accept both.
function checkHookExports(buf) {
  let info;
  try { info = parseWasm(buf); } catch (e) { return { ok: false, reason: 'cannot parse wasm: ' + e.message }; }
  const hooks = info.exports.filter((e) => /^_?DG_TEST_/.test(e.name));
  const names = hooks.map((e) => e.name.replace(/^_/, ''));
  const missing = HOOKS.filter((h) => !names.includes(h));
  const extra = names.filter((n) => !HOOKS.includes(n));
  const dup = names.filter((n, i) => names.indexOf(n) !== i);
  const bad = hooks.filter((e) => HOOKS.includes(e.name.replace(/^_/, '')) && (e.kind !== 0 || e.params !== 0 || !e.results || e.results.length !== 1 || e.results[0] !== 0x7f));
  const problems = [];
  if (missing.length) problems.push('missing ' + missing.join(','));
  if (extra.length) problems.push('extra DG_TEST_ exports ' + extra.join(','));
  if (dup.length) problems.push('duplicate ' + dup.join(','));
  if (bad.length) problems.push('hook is not a () -> i32 function: ' + bad.map((e) => e.name).join(','));
  return problems.length ? { ok: false, reason: problems.join('; '), exports: names } : { ok: true, reason: 'six DG_TEST_ getters, no others', exports: names };
}

module.exports = { parseWasm, checkHookExports, HOOKS };
