#!/usr/bin/env bash
# Build both modules. Run from anywhere: bash scripts/build.sh
set -e
cd "$(dirname "$0")/.."
for d in ibridge-cfg dfr-probe; do
  echo "== $d =="
  make -C "$d" clean >/dev/null 2>&1 || true
  make -C "$d"
done
echo "built: $(ls ibridge-cfg/*.ko dfr-probe/*.ko)"
