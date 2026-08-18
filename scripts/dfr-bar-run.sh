#!/usr/bin/env bash
# Start the Touch Bar UI in the background. sudo bash scripts/dfr-bar-run.sh [args]
R="$(cd "$(dirname "$0")/.." && pwd)"
pkill -f dfr-bar.py 2>/dev/null; sleep 1
: "${SUDO_USER:=buzzkill}"; export SUDO_USER
setsid python3 -u "$R/scripts/dfr-bar.py" "$@" >/tmp/dfr-bar.log 2>&1 < /dev/null &
sleep 3
echo "started pid=$(pgrep -f dfr-bar.py | head -1)"
grep -v Deprecation /tmp/dfr-bar.log | head -5
