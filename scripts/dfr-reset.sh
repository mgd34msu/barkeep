#!/usr/bin/env bash
# Undo everything, restore the stock function row.  sudo bash ~/dfr-reset.sh
. "$(dirname "$0")/ibridge-common.sh"
D=$(ibridge_path_or_die) || exit 1
rmmod barkeep_dfr 2>/dev/null; rmmod barkeep_cfgsel 2>/dev/null
[ "$(cat $D/bConfigurationValue 2>/dev/null)" != "1" ] && echo 1 > $D/bConfigurationValue && sleep 3
echo on > $D/power/control 2>/dev/null
modprobe apple-ibridge; modprobe apple-ib-tb; modprobe apple-ib-als
/usr/local/bin/touchbar-rebind 2>&1 | tail -1
echo "cfg=[$(cat $D/bConfigurationValue 2>/dev/null)]"
