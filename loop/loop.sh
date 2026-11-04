#!/bin/bash
# Entry point: ./loop/loop.sh CONFIG.json   (controller-owned config; see loop/controller.py)
set -eu
cd "$(dirname "$0")/.."
exec python3 -m loop.controller "$@"
