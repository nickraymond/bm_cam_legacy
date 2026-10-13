#!/bin/bash
# 1.22 m v3-card production-crop comparison setup (EM plan 2026-10-13 ~2:55 PM; SWAPPED pairing: bmcam003<->SPOT-31593C, bmcam004<->SPOT-33507C)
# hil_bus_always_on.sh -> pool_mode on + start (fresh run; ~/pool_args lp 0.8). Then console status + one preview.
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..; source hil/hil.env
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
one() {
  H=$1; S=$2; OUT=$RUN/t122_setup_${H}_$(date -u +%Y%m%dT%H%MZ).txt
  { HIL_RUN_DIR=$RUN hil/tools/hil_bus_always_on.sh $S; rc=$?; echo "hil_bus_always_on rc=$rc"; [ $rc = 0 ] || return 1
    ssh "${O[@]}" pi@$H 'cat ~/pool_args; bash /home/pi/pool_mode.sh on 2>&1 | tail -3; bash /home/pi/pool_mode.sh start 2>&1 | tail -1; sleep 10; bash /home/pi/pool_mode.sh status 2>&1 | grep -E "newest run|cfg=|pool process"' </dev/null; } > "$OUT" 2>&1
}
one bmcam003 SPOT-31593C & one bmcam004 SPOT-33507C & wait
grep -hE "rc=|bus held|newest run|cfg=|pool process|REFUSED|FAIL" $RUN/t122_setup_*.txt
ssh "${O[@]}" pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both status --wait 30; python3 /home/pi/pool_tools/pool_cmd.py both preview --wait 60' </dev/null 2>&1 | grep -E "OK id|ERR" | grep -v "\.\. 20" | cut -c1-200
