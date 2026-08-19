#!/usr/bin/env bash
# Bring up the Touch Bar display session and leave it running.
# Then push frames with:  scripts/dfr-play.py test | bars | FILE
. "$(dirname "$0")/ibridge-common.sh"
D=$(ibridge_path_or_die) || exit 1
R="$(cd "$(dirname "$0")/.." && pwd)"
for m in apple_ib_tb apple_ib_als apple_ibridge; do rmmod $m 2>/dev/null; done
rmmod barkeep_dfr 2>/dev/null; rmmod barkeep_cfgsel 2>/dev/null
insmod "$R/barkeep-cfgsel/barkeep-cfgsel.ko" config=1 || exit 1
insmod "$R/barkeep-dfr/barkeep-dfr.ko" rect_w=2170 bpp=3 fbmode=1 period=1 colr=0 colg=0 colb=0 || exit 1
echo 2 > /sys/module/barkeep_cfgsel/parameters/config
echo 0 > $D/authorized; sleep 2; echo 1 > $D/authorized; sleep 4
echo "cfg=[$(cat $D/bConfigurationValue 2>/dev/null)] (want 2)"
python3 "$R/scripts/dispon.py" && echo "panel ON"
ls -l /dev/dfr0 2>/dev/null || echo "WARNING: /dev/dfr0 missing"
echo "ready - push frames with: python3 $R/scripts/dfr-play.py test"
