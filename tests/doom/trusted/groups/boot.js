'use strict';
// LOAD-01.a-c, WAD-01.a-b and (when opts.deploy) DEPLOY-01.a-b, all from one page load served under /doom/.
const { sleep, SetupError } = require('../lib/util');
const { withGame, readCanvas, shot, wadEvidenceRe } = require('../lib/site');

const LOAD_IDS = ['LOAD-01.a', 'LOAD-01.b', 'LOAD-01.c', 'WAD-01.a', 'WAD-01.b'];
const DEPLOY_IDS = ['DEPLOY-01.a', 'DEPLOY-01.b'];

function distinctColors(data, cap) {
  const u = new Uint8Array(data.length); u.set(data);
  const px = new Uint32Array(u.buffer);
  const seen = new Set();
  for (let i = 0; i < px.length && seen.size < cap; i++) seen.add(px[i]);
  return seen.size;
}

// Evidence that the game failed because the WAD data was unavailable (used by WAD-01.a when no tic ever ran).
function wadMissingEvidence(g) {
  const nonWasm = (x) => !/\.wasm$/i.test(x.path);
  const bad = g.events.bad.filter(nonWasm)[0] || g.events.failed.filter(nonWasm)[0];
  if (bad) return 'request for ' + bad.path + ' failed (' + (bad.status || bad.err) + ')';
  const line = g.log.find((l) => /^\[(error|warning)\]/.test(l) && wadEvidenceRe.test(l) && !/wasm/i.test(l));
  return line ? line.slice(0, 160) : null;
}

async function bootGroup(env, rec, opts) {
  opts = opts || {};
  const ids = LOAD_IDS.concat(opts.deploy ? DEPLOY_IDS : []);
  if (rec.blockedBy('LOAD-01.a', ['BUILD-01.a', 'BUILD-01.c'])) { for (const id of ids) rec.error(id, rec.reason('LOAD-01.a')); return; }
  try {
    await withGame(env, { label: opts.label || 'boot' }, async (g) => {
      const startFatal = g.startupFatal.slice();
      // LOAD-01.a
      if (g.firstSnap) rec.pass('LOAD-01.a', 'first GetGameTic() > 0 (tic ' + g.firstSnap.tic + ') after ' + Math.round(g.startMs) + ' ms', { startupMs: Math.round(g.startMs) });
      else rec.fail('LOAD-01.a', g.failReason);
      // LOAD-01.c
      if (startFatal.length) rec.fail('LOAD-01.c', 'fatal error before the timeout: ' + startFatal[0].kind + ': ' + startFatal[0].text);
      else rec.pass('LOAD-01.c', 'no page error, abort or crash during startup');
      // LOAD-01.b
      if (!g.firstSnap) rec.blockedBy('LOAD-01.b', ['LOAD-01.a']);
      else {
        await sleep(2000); // contract: warmup of 2 s after the first tic before looking at pixels
        let c = null, colors = 0;
        for (let i = 0; i < 4; i++) {
          c = await readCanvas(g);
          if (c.err) break;
          colors = distinctColors(c.data, 64);
          if (colors >= 2) break;
          await sleep(1000);
        }
        if (c.err) rec.fail('LOAD-01.b', c.err);
        else if (colors >= 2) rec.pass('LOAD-01.b', 'canvas ' + c.w + 'x' + c.h + ' has at least ' + colors + ' distinct colors');
        else rec.fail('LOAD-01.b', 'canvas is one uniform color for 4 samples over 3 s');
        await shot(g, 'boot');
      }
      // WAD-01
      const s = g.firstSnap, start = env.start;
      if (!s) {
        const ev = wadMissingEvidence(g);
        if (ev) { rec.fail('WAD-01.a', 'no first tic and the WAD data is unavailable: ' + ev); rec.fail('WAD-01.b', 'no first tic and the WAD data is unavailable'); }
        else { rec.blockedBy('WAD-01.a', ['LOAD-01.a']); rec.blockedBy('WAD-01.b', ['LOAD-01.a']); }
      } else {
        const ex = start.x * 65536, ey = start.y * 65536;
        if (s.x === null || s.y === null) rec.fail('WAD-01.a', 'DG_TEST_GetPlayerX/Y are not callable');
        else if (s.x === ex && s.y === ey) rec.pass('WAD-01.a', 'first-tic player (' + s.x / 65536 + ',' + s.y / 65536 + ') equals E1M1 start THING (' + start.x + ',' + start.y + ')');
        else rec.fail('WAD-01.a', 'first-tic player (' + s.x / 65536 + ',' + s.y / 65536 + ') != E1M1 start (' + start.x + ',' + start.y + '), raw fixed ' + s.x + ',' + s.y);
        const eang = Math.round(start.angle / 360 * 4294967296) >>> 0;
        if (s.ang === null) rec.fail('WAD-01.b', 'DG_TEST_GetViewAngle is not callable');
        else if (s.ang === eang) rec.pass('WAD-01.b', 'first-tic view angle ' + s.ang + ' equals start angle ' + start.angle + ' deg');
        else rec.fail('WAD-01.b', 'first-tic view angle ' + s.ang + ' != ' + eang + ' (' + start.angle + ' deg)');
      }
      // DEPLOY-01
      if (opts.deploy) {
        const w = g.events.responses.filter((r) => /\.wasm$/i.test(r.path))[0];
        if (!w) rec.fail('DEPLOY-01.a', 'no .wasm response was served to the page under /doom/');
        else if (w.status === 200 && /^application\/wasm\b/i.test(w.type)) rec.pass('DEPLOY-01.a', w.path + ' served as ' + w.type);
        else rec.fail('DEPLOY-01.a', w.path + ' served with status ' + w.status + ' and MIME "' + w.type + '"');
        const lw = [rec.status('LOAD-01.a'), rec.status('WAD-01.a')];
        const broken = g.events.bad.concat(g.events.failed);
        if (lw.includes('FAIL')) rec.fail('DEPLOY-01.b', 'LOAD-01.a / WAD-01.a do not hold under /doom/ (' + (rec.status('LOAD-01.a') === 'FAIL' ? rec.reason('LOAD-01.a') : rec.reason('WAD-01.a')) + ')');
        else if (lw.includes('ERROR') || lw.includes(null)) rec.error('DEPLOY-01.b', 'skipped: LOAD-01.a or WAD-01.a did not produce a result');
        else if (broken.length) rec.fail('DEPLOY-01.b', 'asset request failed under /doom/: ' + JSON.stringify(broken[0]));
        else rec.pass('DEPLOY-01.b', 'LOAD-01.a and WAD-01.a hold under /doom/ with no failed asset requests');
      }
      if (!g.firstSnap) await shot(g, 'nostart');
    });
  } catch (e) {
    rec.errorAll(ids, 'infrastructure: ' + String(e && e.message || e).split('\n')[0]);
  }
}

module.exports = { bootGroup, LOAD_IDS, DEPLOY_IDS };
