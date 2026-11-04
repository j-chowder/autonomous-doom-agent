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
