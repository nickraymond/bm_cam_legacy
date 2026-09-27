#!/bin/bash
# S3b: READ-ONLY. While armed bmcam003 is awake (01:00Z window), pull the 23:00Z 2026-09-26
# cycle evidence for RESULTS F3 (where console ping 26305 landed). Cron log names use the
# pre-sync Pi clock (~22:2x), so list them all and grep by content; copy, don't touch.
HERE="$(cd "$(dirname "$0")" && pwd)"; h=bmcam003; n=0
until ssh -o ConnectTimeout=3 -o BatchMode=yes pi@$h true < /dev/null 2>/dev/null; do
  n=$((n+1)); sleep 2; [ $n -gt 300 ] && { echo "[f3] $h not reachable"; exit 1; }; done
echo "[f3] $h reachable $(date -u +%T)"
mkdir -p "$HERE/bmcam003_f3"
ssh -o BatchMode=yes pi@$h 'cd /home/pi/BM_Devel_Pi && ls -la --time-style=+%FT%T cron_logs | tail -8; echo ---; grep -l 26305 cron_logs/rc_cycle_*.log 2>/dev/null; echo ---; tail -3 supervisor_actions.jsonl 2>/dev/null' < /dev/null
for f in $(ssh -o BatchMode=yes pi@$h 'cd /home/pi/BM_Devel_Pi && grep -l 26305 cron_logs/rc_cycle_*.log 2>/dev/null' < /dev/null); do
  scp -q -o BatchMode=yes pi@$h:/home/pi/BM_Devel_Pi/$f "$HERE/bmcam003_f3/"; done
scp -q -o BatchMode=yes pi@$h:/home/pi/BM_Devel_Pi/supervisor_actions.jsonl "$HERE/bmcam003_f3/" 2>/dev/null
ls -la "$HERE/bmcam003_f3"; echo "[f3] done $(date -u +%T)"
