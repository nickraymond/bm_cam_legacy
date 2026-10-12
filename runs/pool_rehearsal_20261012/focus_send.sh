#!/bin/bash
# focus_send.sh: console-lane focus lock (Nick's OK 2026-10-12, item a): set camera.focus.mode manual + lens_position 1.82
# on one bench unit. Waits until that unit's cycle log shows the post-transmit listen window open (daemon listening, burst
# done), then publishes ONE bm pub via hil_cmd.sh and prints the console answer. Read-only on the unit (cycle log cat).
# Usage: bash focus_send.sh <bmcam003|bmcam004> <console id>
set -u
cd "$(dirname "$0")/../.."
H=$1; ID=$2; case $H in bmcam003) S=SPOT-33507C;; bmcam004) S=SPOT-31593C;; *) echo "bench units only"; exit 2;; esac
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)
END=$(( $(date +%s) + 540 ))
while [ "$(date +%s)" -lt $END ]; do
  st=$(ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; f=$(ls -t cron_logs/rc_cycle_*.log | head -1); grep -c "post-transmit listen window: " $f; grep -c "post-transmit listen window done" $f' </dev/null 2>/dev/null | tr '\n' ' ')
  case "$st" in "1 0 ") break;; esac
  sleep 5
done
echo "$(date -u +%FT%TZ) $H listen-window state: '$st'"
[ "$st" = "1 0 " ] || { echo "$H: listen window not seen open; NOT sending"; exit 3; }
source hil/hil.env
HIL_RUN_DIR=runs/pool_rehearsal_20261012 hil/tools/hil_cmd.sh FOCUS.$H "{\"id\":$ID,\"c\":\"set\",\"kv\":{\"camera.focus.mode\":\"manual\",\"camera.focus.lens_position\":1.82}}" 12 $S
