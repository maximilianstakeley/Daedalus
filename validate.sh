#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NODE_BIN="${NODE_BIN:-node}"

printf '\n[1/3] Backend tests\n'
(
  cd "$ROOT/backend"
  DAEDALUS_DISABLE_POLLING=1 PYTHONPATH="$ROOT/backend" "$PYTHON_BIN" -m pytest -q
)

printf '\n[2/3] Python compilation\n'
(
  cd "$ROOT"
  "$PYTHON_BIN" -m py_compile \
    backend/daedalus/*.py \
    backend/daedalus/providers/*.py \
    backend/daedalus/workers/*.py \
    integrations/ros2/*.py \
    tools/*.py
)

printf '\n[3/3] Frontend JavaScript syntax\n'
(
  cd "$ROOT/frontend"
  "$NODE_BIN" --version
  npm run check
)

printf '\nAll local checks passed. This does not run npm install/build, Docker services, GPU reconstruction, or hardware-in-loop tests.\n'
