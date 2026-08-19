#!/usr/bin/env bash
# Build both modules. Run from anywhere: bash scripts/build.sh
set -e
cd "$(dirname "$0")/.."
for d in barkeep-cfgsel barkeep-dfr; do
  echo "== $d =="
  make -C "$d" clean >/dev/null 2>&1 || true
  make -C "$d"
done
echo "built: $(ls barkeep-cfgsel/*.ko barkeep-dfr/*.ko)"
