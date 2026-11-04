'use strict';
// WAD reader and WAD-derived references for the trusted tests. Everything here comes from assets/doom1.wad
// and from the engine source constants cited next to each use.
const fs = require('fs');

function loadWad(file) {
  const buf = fs.readFileSync(file);
  if (buf.toString('latin1', 0, 4) !== 'IWAD') throw new Error('not an IWAD: ' + file);
  const n = buf.readInt32LE(4), off = buf.readInt32LE(8);
  const dir = [];
  for (let i = 0; i < n; i++) {
    const o = off + 16 * i;
    dir.push({ pos: buf.readInt32LE(o), size: buf.readInt32LE(o + 4), name: buf.toString('latin1', o + 8, o + 16).replace(/\0.*$/, '') });
  }
  const find = (name) => dir.findIndex((l) => l.name === name);
  const lump = (name) => { const i = find(name); if (i < 0) throw new Error('lump not found: ' + name); const l = dir[i]; return buf.subarray(l.pos, l.pos + l.size); };
  const mapLump = (map, name) => {
    const m = find(map); if (m < 0) throw new Error('map not found: ' + map);
    for (let i = m + 1; i < Math.min(dir.length, m + 12); i++) if (dir[i].name === name) return buf.subarray(dir[i].pos, dir[i].pos + dir[i].size);
    throw new Error('map lump not found: ' + map + '/' + name);
  };
  return { buf, dir, lump, mapLump, find };
}

// First row of gammatable[5][256] (tables.c:2129+); usegamma is 0 by default (i_video.c:122).
function loadGamma0(tablesC) {
  const src = fs.readFileSync(tablesC, 'latin1');
  const m = /gammatable\s*\[\s*5\s*\]\s*\[\s*256\s*\]\s*=\s*\{\s*\{([^}]*)\}/.exec(src);
  if (!m) throw new Error('gammatable not found in ' + tablesC);
  const row = (m[1].match(/\d+/g) || []).map(Number);
  if (row.length !== 256) throw new Error('gammatable row 0 has ' + row.length + ' entries');
  return row;
}

// PLAYPAL palette 0 after I_SetPalette's gamma step (i_video.c:408-412). Returns 256 [r,g,b].
function palette0(wad, gamma0) {
  const p = wad.lump('PLAYPAL');
  const out = [];
  for (let i = 0; i < 256; i++) out.push([gamma0[p[3 * i]], gamma0[p[3 * i + 1]], gamma0[p[3 * i + 2]]]);
  return out;
}

// Decode a picture lump: columns of posts. px[y*w+x] = palette index or -1.
function patch(wad, name) {
  const b = wad.lump(name);
  const w = b.readInt16LE(0), h = b.readInt16LE(2), lo = b.readInt16LE(4), to = b.readInt16LE(6);
  const px = new Int16Array(w * h).fill(-1);
  for (let x = 0; x < w; x++) {
    let q = b.readInt32LE(8 + 4 * x);
    while (b[q] !== 255) {
      const top = b[q], len = b[q + 1]; q += 3;
      for (let k = 0; k < len; k++) if (top + k < h) px[(top + k) * w + x] = b[q + k];
      q += len + 1;
    }
  }
  return { w, h, lo, to, px };
}

// E1M1 player 1 start (THINGS type 1). Angle is degrees; ANG45*(angle/45) in P_SpawnPlayer => BAM = angle/360*2^32.
function playerStart(wad, map) {
  const t = wad.mapLump(map || 'E1M1', 'THINGS');
  for (let i = 0; i + 10 <= t.length; i += 10) {
    if (t.readInt16LE(i + 6) === 1) return { x: t.readInt16LE(i), y: t.readInt16LE(i + 2), angle: t.readInt16LE(i + 4) };
  }
  throw new Error('no player 1 start in ' + (map || 'E1M1'));
}

// Free distance (map units) along a ray from (px,py) before a blocking linedef. A line blocks when it is
// one-sided or flagged impassable (flag 1), the floor step is > 24 (p_map.c P_TryMove 24-unit step-up),
// or the opening is < 56 (player height, p_local.h). `lateral` offsets the ray sideways to cover the player radius (16).
function freeDistance(wad, map, px, py, deg, lateral) {
  const V = wad.mapLump(map, 'VERTEXES'), LD = wad.mapLump(map, 'LINEDEFS'), SD = wad.mapLump(map, 'SIDEDEFS'), SE = wad.mapLump(map, 'SECTORS');
  const dx = Math.round(Math.cos(deg * Math.PI / 180) * 1e6) / 1e6, dy = Math.round(Math.sin(deg * Math.PI / 180) * 1e6) / 1e6;
  const ox = px - dy * lateral, oy = py + dx * lateral;
  const vert = (i) => [V.readInt16LE(4 * i), V.readInt16LE(4 * i + 2)];
  const sector = (side) => { const s = SD.readInt16LE(30 * side + 28); return { floor: SE.readInt16LE(26 * s), ceil: SE.readInt16LE(26 * s + 2) }; };
  let best = Infinity;
  for (let o = 0; o + 14 <= LD.length; o += 14) {
    const a = LD.readInt16LE(o), b = LD.readInt16LE(o + 2), flags = LD.readInt16LE(o + 4);
    const right = LD.readInt16LE(o + 10), left = LD.readInt16LE(o + 12);
    let blocking = (flags & 1) !== 0 || right === -1 || left === -1;
    if (!blocking) {
      const sr = sector(right), sl = sector(left);
      blocking = Math.abs(sr.floor - sl.floor) > 24 || Math.min(sr.ceil, sl.ceil) - Math.max(sr.floor, sl.floor) < 56;
    }
    if (!blocking) continue;
    const [x1, y1] = vert(a), [x2, y2] = vert(b);
    const ex = x2 - x1, ey = y2 - y1, den = dx * ey - dy * ex;
    if (den === 0) continue;
    const t = ((x1 - ox) * ey - (y1 - oy) * ex) / den;
    const u = ((x1 - ox) * dy - (y1 - oy) * dx) / den;
    if (t > 0 && u >= 0 && u <= 1 && t < best) best = t;
  }
  return best;
}

// Status bar reference (st_stuff.c). The bar is drawn at ST_Y = 168 (st_stuff.h:32) on the 320x200 screen;
// i_video.c:277-282 picks fb_scaling = 2 for 640x400, so each Doom pixel is a 2x2 canvas block.
// Rectangles (x0,x1,y0,y1 in bar coordinates, y relative to ST_Y) that widgets, face, arms, keys or ammo
// counters may overwrite, from the ST_*X / ST_*Y constants at st_stuff.c:84-215. Everything else must equal STBAR.
const BAR_OVERLAYS = [
  [2, 44, 3, 19],     // ready-ammo number: ST_AMMOX 44, 3 digits x 14 wide, y 171-168
  [48, 104, 3, 19],   // health number + percent: ST_HEALTHX 90 (+14 percent sign)
  [104, 144, 0, 32],  // arms panel: ST_ARMSBGX 104, STARMS 40 wide
  [140, 180, 0, 32],  // face: ST_FACESX 143, ST_FACESY 168
  [176, 236, 3, 19],  // armor number + percent: ST_ARMORX 221
  [236, 250, 0, 32],  // keys: ST_KEY0X 239
  [266, 320, 0, 32],  // ammo/max-ammo counters: ST_AMMO0X 288, ST_MAXAMMO0X 314
];
const ST_Y = 168;

function statusBarRefs(wad, pal) {
  const bar = patch(wad, 'STBAR');
  const bg = [];
  for (let y = 0; y < bar.h; y++) for (let x = 0; x < bar.w; x++) {
    const idx = bar.px[y * bar.w + x];
    if (idx < 0) continue;
    if (BAR_OVERLAYS.some((r) => x >= r[0] && x < r[1] && y >= r[2] && y < r[3])) continue;
    bg.push({ x, y: ST_Y + y, c: pal[idx] });
  }
  // Widgets drawn at level start with the pistol ready: ammo 50, health 100%, armor 0% (STlib_drawNum, st_lib.c:77-120).
  const num = []; for (let d = 0; d < 10; d++) num.push(patch(wad, 'STTNUM' + d));
  const pct = patch(wad, 'STTPRCNT');
  const w = num[0].w;
  const widgets = [];
  const put = (p, x, y) => {
    const x0 = x - p.lo, y0 = y - p.to;
    for (let j = 0; j < p.h; j++) for (let i = 0; i < p.w; i++) { const idx = p.px[j * p.w + i]; if (idx >= 0) widgets.push({ x: x0 + i, y: y0 + j, c: pal[idx] }); }
  };
  const drawNum = (value, x, y, digits) => { // right-aligned like STlib_drawNum
    if (value === 0) { put(num[0], x - w, y); return; }
    let n = value;
    while (n && digits--) { x -= w; put(num[n % 10], x, y); n = Math.floor(n / 10); }
  };
  drawNum(50, 44, 171, 3);        // ST_AMMOX, ST_AMMOY
  drawNum(100, 90, 171, 3); put(pct, 90, 171);   // ST_HEALTHX, ST_HEALTHY + percent sign
  drawNum(0, 221, 171, 3); put(pct, 221, 171);   // ST_ARMORX, ST_ARMORY + percent sign
  return { bg, widgets };
}

module.exports = { loadWad, loadGamma0, palette0, patch, playerStart, freeDistance, statusBarRefs, BAR_OVERLAYS, ST_Y };
