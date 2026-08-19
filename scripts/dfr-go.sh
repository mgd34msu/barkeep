#!/usr/bin/env bash
# Touch Bar bring-up with the byte-exact DFRDisplayKm frame format.
#   sudo bash ~/dfr-go.sh          (log: /tmp/dfr-go.log)
exec > >(tee /tmp/dfr-go.log) 2>&1
. "$(dirname "$0")/ibridge-common.sh"
D=$(ibridge_path_or_die) || exit 1
P=/sys/module/dfr_probe/parameters

echo "== 1. apple-ibridge OUT (appleib_hid_probe forces config 1 and kills the session) =="
for m in apple_ib_tb apple_ib_als apple_ibridge; do rmmod $m 2>/dev/null && echo "   rmmod $m"; done

echo "== 2. load modules =="
rmmod dfr_probe 2>/dev/null; rmmod ibridge_cfg 2>/dev/null
insmod /home/buzzkill/Projects/linux-t1-touch/ibridge-cfg/ibridge-cfg.ko config=1 || exit 1
insmod /home/buzzkill/Projects/linux-t1-touch/dfr-probe/dfr-probe.ko rect_w=2170 bpp=3 fbmode=1 period=1 \
       colr=255 colg=0 colb=0 || exit 1

echo "== 3. enter config 2 =="
echo 2 > /sys/module/ibridge_cfg/parameters/config
echo 0 > $D/authorized; sleep 2; echo 1 > $D/authorized; sleep 4
echo "   cfg=[$(cat $D/bConfigurationValue 2>/dev/null)]  (want 2)"
echo "   acks=$(journalctl -k --since '40 sec ago' --no-pager 2>/dev/null | grep -c ACK/response)"

echo "== 4. panel enable =="
python3 /home/buzzkill/Projects/linux-t1-touch/scripts/dispon.py && echo "   display ON sent" || echo "   display ON FAILED"

echo "== 5. RED / GREEN / BLUE, 12s each - WATCH THE BAR =="
echo "   RED";   echo 255 > $P/colr; echo 0   > $P/colg; echo 0   > $P/colb; sleep 12
echo "   GREEN"; echo 0   > $P/colr; echo 255 > $P/colg; echo 0   > $P/colb; sleep 12
echo "   BLUE";  echo 0   > $P/colr; echo 0   > $P/colg; echo 255 > $P/colb; sleep 12
echo "== done. cfg=[$(cat $D/bConfigurationValue 2>/dev/null)] =="
