#!/bin/bash
# Sprint23 — watch one remote command from Ebox arrival to camera ack.
#
# Purpose : block until the Spotter console shows "Remote message received"
#           for command id $1, then report bus state at arrival, whether the
#           Ebox HELD it ("Queuing serial command") or passed it LIVE, whether
#           an ack-sized transmit followed, and what the Pi's daemon logged.
# Inputs  : $1 = command id (the "id":N inside the bm pub JSON)
#           $2 = Spotter id (default SPOT-31593C)   $3 = Pi host (default bmcam003)
#           console logs from tools/spotter_serial_monitor.py in ~/spotter_logs
# Output  : plain text on stdout (pasted into RESULTS.md by hand). Read-only:
#           it sends nothing to the Spotter and only reads logs on the Pi.
# Example : ./watch_delivery.sh 2306
# Limits  : ack detection = a transmit-data message of Len 110-119 after the
#           arrival line; that is the ping-ack size seen on 2026-09-21, other
#           commands may differ. Exits early if the serial monitor dies.
ID="$1"; SPOT="${2:-SPOT-31593C}"; PI="${3:-bmcam003}"
[ -z "$ID" ] && { echo "usage: $0 <command_id> [spotter_id] [pi_host]"; exit 2; }
DIR=~/spotter_logs/$SPOT
pat="Remote message received.*\"id\":$ID[,}]"

until grep -hqE "$pat" "$DIR"/console_*.log 2>/dev/null; do
  pgrep -f spotter_serial_monitor >/dev/null || { echo "MONITOR DIED — no result for id $ID"; exit 1; }
  sleep 5
done
F=$(grep -lE "$pat" "$DIR"/console_*.log | tail -1)
n=$(grep -nE "$pat" "$F" | tail -1 | cut -d: -f1)

echo "== id $ID arrival ($F:$n)"
sed -n "${n},$((n+1))p" "$F" | cut -c1-170
echo "== last bus power event before arrival"
sed -n "1,${n}p" "$F" | grep "Bridge bus power" | tail -1 | cut -c1-110
echo "== last standard report + mailbox check before arrival"
sed -n "1,${n}p" "$F" | grep -E "MS_Q_LEGACY|Checking for Rx" | tail -2 | cut -c1-130

# Give the camera up to ~7 min (a held command waits for the next bus-on + boot).
i=0
until sed -n "${n},\$p" "$F" | grep -qE "transmit-data.*Len: 11[0-9]" || [ $i -gt 84 ]; do sleep 5; i=$((i+1)); done
echo "== held or live, next bus-on, first ack-sized transmit"
sed -n "${n},\$p" "$F" | grep -E "Queuing serial command.*\"id\":$ID|Bridge bus power: 1|Len: 11[0-9]" | head -4 | cut -c1-170
echo "== Pi daemon (newest two cycle logs)"
ssh -o BatchMode=yes -o ConnectTimeout=8 "pi@$PI" \
  'for L in $(ls -t ~/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -2); do echo "-- $L"; grep -E "spotter UTC|applied id|ack sent|\[CMD\] stopped" $L | cut -c1-160; done' 2>&1
date -u +"== watcher done %Y-%m-%dT%H:%M:%SZ"
