'use strict';
// Shared helpers for the trusted DOOM acceptance tests. No network, no third-party modules.
const fs = require('fs');
const path = require('path');
const { performance } = require('perf_hooks');

const nowMs = () => performance.now();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function withTimeout(promise, ms, what) {
  let timer;
  const guard = new Promise((_, rej) => {
    timer = setTimeout(() => rej(new Error('timeout after ' + ms + ' ms: ' + (what || 'operation'))), ms);
  });
  return Promise.race([promise, guard]).finally(() => clearTimeout(timer));
}

// A precondition that could not be set up. Groups turn this into ERROR, never PASS.
class SetupError extends Error {}

const STATUS_ORDER = { FAIL: 2, ERROR: 1, PASS: 0 };

// Collects one result per assertion id. First result wins, so a later PASS can never hide a FAIL.
class Recorder {
  constructor(scope, opts) {
    this.scope = scope ? new Set(scope) : null; // ids this run reports; null = report everything
    this.order = scope ? scope.slice() : [];
    this.res = new Map();
    this.meas = {};
    this.quiet = !!(opts && opts.quiet);
    this.prefix = (opts && opts.prefix) || '';
  }
  set(id, status, reason, meas) {
    if (this.scope && !this.scope.has(id)) return;
    if (this.res.has(id)) return;
    const r = { id, status, reason: String(reason == null ? '' : reason).replace(/\s+/g, ' ').slice(0, 400) };
    this.res.set(id, r);
    if (!this.scope) this.order.push(id);
    if (meas) this.meas[id] = meas;
    if (!this.quiet) console.log('ASSERT ' + this.prefix + id + ' ' + status + (r.reason ? ' ' + r.reason : ''));
  }
  pass(id, reason, meas) { this.set(id, 'PASS', reason, meas); }
  fail(id, reason, meas) { this.set(id, 'FAIL', reason, meas); }
  error(id, reason, meas) { this.set(id, 'ERROR', reason, meas); }
  has(id) { return this.res.has(id); }
  status(id) { const r = this.res.get(id); return r ? r.status : null; }
  reason(id) { const r = this.res.get(id); return r ? r.reason : ''; }
  // If any blocker is recorded and did not PASS, record ERROR "skipped: <blocker> failed" and return true.
  blockedBy(id, blockers) {
    for (const b of blockers) {
      const s = this.status(b);
      if (s === null || s === 'PASS') continue;
      this.error(id, 'skipped: ' + b + (s === 'FAIL' ? ' failed' : ' did not pass (' + s + ')'));
      return true;
    }
    return false;
  }
  // Used when a whole group cannot run.
  errorAll(ids, reason) { for (const id of ids) this.error(id, reason); }
  // Finish every id still without a result: they did not run, so they are ERROR.
  closeOut(reason) { for (const id of this.order) if (!this.res.has(id)) this.error(id, reason); }
  list() { return this.order.filter((id) => this.res.has(id)).map((id) => this.res.get(id)); }
  anyStatus(s) { for (const r of this.res.values()) if (r.status === s) return true; return false; }
}

function mkdirp(d) { fs.mkdirSync(d, { recursive: true }); return d; }

function walk(dir, out) {
  out = out || [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out); else if (e.isFile()) out.push(p);
  }
  return out;
}

function fmt(n, d) { return typeof n === 'number' && isFinite(n) ? n.toFixed(d == null ? 1 : d) : String(n); }

module.exports = { nowMs, sleep, withTimeout, SetupError, Recorder, mkdirp, walk, fmt, STATUS_ORDER };
