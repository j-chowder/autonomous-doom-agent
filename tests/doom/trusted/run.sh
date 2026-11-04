#!/usr/bin/env bash
# Trusted DOOM acceptance runner (contract section 4).
#   run.sh GATE      GATE = build | boot | m1 | video | time | input | audio | final | controls
# Container layout: candidate read-only at /workspace, these tests read-only at /trusted, /out writable.
# Env overrides (for local runs): WORKSPACE, OUT, CANDIDATE_SHA, IMAGE_ID, QA_BUDGET_S.
# Exit: 0 all PASS, 1 any FAIL, 70 no FAIL but at least one ERROR, 2 usage.
set -u

GATE="${1:-}"
case "$GATE" in
  build|boot|m1|video|time|input|audio|final|controls) ;;
  *) echo "usage: run.sh build|boot|m1|video|time|input|audio|final|controls" >&2; exit 2 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WORKSPACE="${WORKSPACE:-/workspace}"
export OUT="${OUT:-/out}"
export WORK="$OUT/work"
export NODE_PATH="${NODE_PATH:-}"

mkdir -p "$OUT" || { echo "ASSERT BUILD-01.a ERROR cannot create $OUT" ; exit 70; }
rm -rf "$WORK" "$OUT/screenshots" "$OUT/logs" "$OUT/controls" "$OUT/results.json"
mkdir -p "$WORK" "$OUT/logs" "$OUT/screenshots"

# Build in a copy so the read-only candidate is never touched. dist/ and build/ are excluded: build.sh recreates them.
if ! ( cd "$WORKSPACE" && tar cf - --exclude=./.git --exclude=./dist --exclude=./build --exclude=./artifacts . ) | ( cd "$WORK" && tar xf - ); then
  echo "ASSERT BUILD-01.a ERROR cannot copy $WORKSPACE to $WORK" ; exit 70
fi

if ! command -v node >/dev/null 2>&1; then
  echo "ASSERT BUILD-01.a ERROR node is not installed in the verification image" ; exit 70
fi

exec node "$HERE/main.js" "$GATE"
