// Sets the global Module (no MODULARIZE) before doom.js loads.
(function () {
  var q = new URLSearchParams(location.search);
  var args = ['-iwad', 'doom1.wad'];

  var warp = (q.get('warp') || '').trim();
  var m;
  if ((m = /^E(\d)M(\d)$/i.exec(warp))) {
    args.push('-warp', m[1], m[2]);
  } else if ((m = /^(?:MAP)?(\d{1,2})$/i.exec(warp))) {
    args.push('-warp', String(parseInt(m[1], 10)));
  }
  var skill = q.get('skill');
  if (skill !== null && /^[1-5]$/.test(skill)) args.push('-skill', skill);
  var nm = q.get('nomonsters');
  if (nm && nm !== '0' && nm !== 'false') args.push('-nomonsters');

  var fatalBox = document.getElementById('fatal');
  var lastErr = [];
  function fatal(msg) {
    window.doomFatal = msg;
    fatalBox.textContent = 'Fatal: ' + msg;
    fatalBox.hidden = false;
  }

  // Doom key codes (doomkeys.h) for the supported browser keys.
  var KEYMAP = {
    ArrowUp: 0xaf, ArrowDown: 0xaf, ArrowLeft: 0xac, ArrowRight: 0xae,
    ControlLeft: 0xa3, ControlRight: 0xa3, ShiftLeft: 0x80 + 0x36, ShiftRight: 0x80 + 0x36,
    AltLeft: 0x80 + 0x38, AltRight: 0x80 + 0x38,
    Space: 0xa2, Enter: 13, NumpadEnter: 13, Escape: 27, Tab: 9, Backspace: 0x7f,
    Minus: 0x2d, Equal: 0x3d, Comma: 0xa0, Period: 0xa1, Pause: 0xff,
    F1: 0x80 + 0x3b, F2: 0x80 + 0x3c, F3: 0x80 + 0x3d, F4: 0x80 + 0x3e,
    F5: 0x80 + 0x3f, F6: 0x80 + 0x40, F7: 0x80 + 0x41, F8: 0x80 + 0x42,
    F9: 0x80 + 0x43, F10: 0x80 + 0x44, F11: 0x80 + 0x57, F12: 0x80 + 0x58
  };
  for (var i = 0; i < 26; i++) KEYMAP['Key' + String.fromCharCode(65 + i)] = 97 + i;
  for (var d = 0; d < 10; d++) KEYMAP['Digit' + d] = 48 + d;
  var held = {};
  function push(down, k) {
    var m = window.Module;
    if (m && m._dg_push_key) m._dg_push_key(down ? 1 : 0, k);
  }
  // Web Audio context, created on the first user gesture (autoplay policy).
  var actx = null;
  function audioUnlock() {
    try {
      if (!actx) {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        actx = new AC();
      }
      if (actx.state !== 'running') {
        var p = actx.resume();
        if (p && p.catch) p.catch(function () {});
      }
    } catch (e) { console.warn('audio unavailable', e); }
  }
  window.dgAudioContext = function () { return actx; };
  window.addEventListener('pointerdown', audioUnlock, true);
  function onKey(down, ev) {
    audioUnlock();
    var k = KEYMAP[ev.code];
    if (k === undefined) return;
    ev.preventDefault();
    if (down && ev.repeat) return;
    if (down === !!held[ev.code]) return;
    held[ev.code] = down;
    push(down, k);
  }
  function releaseAll() {
    Object.keys(held).forEach(function (c) {
      if (held[c]) { held[c] = false; push(false, KEYMAP[c]); }
    });
  }
  window.addEventListener('keydown', function (e) { onKey(true, e); });
  window.addEventListener('keyup', function (e) { onKey(false, e); });
  window.addEventListener('blur', releaseAll);
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) releaseAll();
  });
  var cv = document.getElementById('canvas');
  cv.tabIndex = 0;
  cv.style.outline = 'none';
  cv.focus();
  window.addEventListener('load', function () { cv.focus(); });

  window.Module = {
    canvas: document.getElementById('canvas'),
    arguments: args,
    print: function (t) { console.log(t); },
    printErr: function (t) {
      console.error(t);
      lastErr.push(t);
      if (lastErr.length > 5) lastErr.shift();
    },
    // Hand the preloaded package over from a plain XHR instead of the streaming fetch in doom.js.
    getPreloadedPackage: function (name, size) {
      var x = new XMLHttpRequest();
      x.open('GET', name, false);
      x.overrideMimeType('text/plain; charset=x-user-defined');
      x.send(null);
      if (x.status !== 200) return undefined;
      var t = x.responseText, n = t.length, a = new Uint8Array(n);
      for (var i = 0; i < n; i++) a[i] = t.charCodeAt(i) & 0xff;
      return a.buffer;
    },
    onExit: function (code) {
      fatal('engine exited (code ' + code + ')' +
            (lastErr.length ? ': ' + lastErr.join(' ').trim() : ''));
    },
    onAbort: function (what) { fatal('abort: ' + what); }
  };
})();
