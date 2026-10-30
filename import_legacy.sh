#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone --depth 1 https://github.com/maximilianstakeley/Daedalus.git "$TMP/Daedalus"
mkdir -p "$ROOT/legacy"
cp "$TMP/Daedalus/bridge.py" "$ROOT/legacy/bridge.py"
cp "$TMP/Daedalus/daedalusengine2.py" "$ROOT/legacy/daedalusengine2.py"
cp "$TMP/Daedalus/index.html" "$ROOT/legacy/index.html"
cp "$TMP/Daedalus/daedalussimv4.html" "$ROOT/legacy/daedalussimv4.html"
echo "Legacy Phase 1–4 sources copied into $ROOT/legacy"
echo "The original source files are preserved there; patch and test a copy before replacing anything."
