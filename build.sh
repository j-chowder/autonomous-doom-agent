#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

rm -rf dist build
mkdir -p build dist/doom

ENGINE=third_party/doomgeneric
# Every engine .c is a portable core file; mus2mid's main() is STANDALONE-only.
SRCS=("$ENGINE"/*.c platform/*.c)

EXPORTS='["_main","_DG_TEST_GetGameTic","_DG_TEST_GetFrameCount","_DG_TEST_GetPlayerX","_DG_TEST_GetPlayerY","_DG_TEST_GetViewAngle","_DG_TEST_GetAmmo"]'

emcc -O2 -w -I"$ENGINE" -Iplatform "${SRCS[@]}" \
  -o build/doom.js \
  -sALLOW_MEMORY_GROWTH=1 \
  -sEXPORTED_FUNCTIONS="$EXPORTS" \
  -sEXPORTED_RUNTIME_METHODS='["ccall","cwrap"]' \
  -sENVIRONMENT=web \
  --preload-file assets/doom1.wad@/doom1.wad

cp build/doom.js build/doom.wasm build/doom.data dist/doom/
cp -R web/. dist/doom/
