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
    ArrowUp: 0xad, ArrowDown: 0xaf, ArrowLeft: 0xac, ArrowRight: 0xae,
    ControlLeft: 0xa3, ControlRight: 0xa3, ShiftLeft: 0x80 + 0x36, ShiftRight: 0x80 + 0x36,
    Space: 0xa2, Enter: 13, NumpadEnter: 13, Escape: 27
  };
  var keys = [], held = {};
  function onKey(down, ev) {
    var k = KEYMAP[ev.code];
    if (k === undefined) return;
    ev.preventDefault();
    if (down === !!held[ev.code]) return;
    held[ev.code] = down;
    keys.push([down ? 1 : 0, k]);
  }
  window.addEventListener('keydown', function (e) { onKey(true, e); });
  window.addEventListener('keyup', function (e) { onKey(false, e); });
  window.addEventListener('blur', function () {
    Object.keys(held).forEach(function (c) { if (held[c]) { held[c] = false; keys.push([0, KEYMAP[c]]); } });
  });

  window.Module = {
    dgKeys: keys,
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
