#!/bin/bash
# Controller-owned self-test of the sandbox. Runs INSIDE the toolchain container:
#   /workspace candidate (read-only)   /trusted this folder (read-only)   /out evidence (read-write)   no network
# It checks the toolchain and the isolation itself. It is NOT a DOOM acceptance test.
# A failed check exits 70 = ERROR (the environment is not valid), never FAIL.
set -u
bad() { echo "SMOKE-ERROR: $*"; exit 70; }

v=$(emcc --version | head -1)
case "$v" in *4.0.18*) echo "ok: $v" ;; *) bad "emcc is not 4.0.18: $v" ;; esac
emcc /trusted/hello.c -o /out/hello.js || bad "emcc could not compile hello.c"
node -e "const fs=require('fs');process.exit(WebAssembly.validate(fs.readFileSync('/out/hello.wasm'))?0:1)" || bad "hello.wasm is not valid WebAssembly"
node /out/hello.js | grep -q 'hello from wasm' || bad "compiled program did not run under node"
echo "ok: emcc compiled hello.c; node validated and ran it"
node /trusted/browser_smoke.js || bad "chromium smoke failed"

touch /workspace/.sandbox_probe 2>/dev/null && bad "candidate mount is writable"
touch /trusted/.sandbox_probe 2>/dev/null && bad "trusted mount is writable"
touch /out/.sandbox_probe 2>/dev/null || bad "/out is not writable"
echo "ok: candidate and trusted mounts are read-only; /out is writable"

node -e "const s=require('net').connect({host:'1.1.1.1',port:443});s.setTimeout(3000);s.on('connect',()=>process.exit(1));s.on('error',()=>process.exit(0));s.on('timeout',()=>process.exit(0))" || bad "network is reachable"
echo "ok: outbound network is blocked"

env | grep -qiE 'anthropic|claude|oauth|api_key|auth_token' && bad "credential-like environment variable present"
[ -n "$(ls -A /root/.claude 2>/dev/null)" ] && bad "/root/.claude is present"
echo "ok: no credential-like environment variables or ~/.claude"
echo "SMOKE-OK"
