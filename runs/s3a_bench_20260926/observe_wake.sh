#!/bin/bash
# READ-ONLY observer for bmcam003's armed production wake (supervisor, real halt):
# wait until it is reachable, then follow the newest cron log until "cycle end" and keep a
# copy (the unit halts itself right after). Never writes to the unit.
HERE="$(cd "$(dirname "$0")" && pwd)"; OUT="$HERE/armed_wake_bmcam003.log"; n=0
until ssh -o ConnectTimeout=3 -o BatchMode=yes pi@bmcam003 true < /dev/null 2>/dev/null; do
  n=$((n+1)); sleep 3; [ $n -gt 1000 ] && { echo "[observe] not reachable"; exit 1; }; done
echo "[observe] reachable $(date -u +%T)"
for i in $(seq 1 60); do
  # only a cycle log newer than 22:30Z (the 21:08 log already ends in "cycle end")
  ssh -o ConnectTimeout=4 -o BatchMode=yes pi@bmcam003 'f=$(ls /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | sort | tail -1); case "$f" in *rc_cycle_20260926T2[3-9]*|*rc_cycle_2026092[7-9]*) cat "$f";; esac' < /dev/null > "$OUT.tmp" 2>/dev/null \
    && [ -s "$OUT.tmp" ] && mv "$OUT.tmp" "$OUT"
  grep -qE "cycle end|halt=" "$OUT" 2>/dev/null && { echo "[observe] cycle end seen $(date -u +%T)"; break; }
  sleep 15
done
ssh -o ConnectTimeout=4 -o BatchMode=yes pi@bmcam003 'tail -1 /home/pi/BM_Devel_Pi/cron_logs/supervisor_actions.jsonl' < /dev/null >> "$HERE/armed_wake_action.jsonl" 2>/dev/null
grep -E "RUNTIME|\[SUP\]|shared UART|time-sync|UTC decoded|SETCLOCK|set_system_clock|schedule gate|2630[45]|boot drain|applied id|transmit done|post-transmit|cycle end|halt|ERROR|Traceback|\[PORT\]" "$OUT" | cut -c1-170
echo "[observe] DONE $(date -u +%T)"
