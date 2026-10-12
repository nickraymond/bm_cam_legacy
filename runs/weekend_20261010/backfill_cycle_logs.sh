#!/bin/bash
# backfill_cycle_logs.sh: READ-ONLY. Re-pull the final copy of every weekend cycle log whose collector copy lacks the
# "[BOOT] halt" line (the collector's last read lands before the halt). Runs inside one hourly wake window (Pi answers
# ~:00:30-:08:00). Output: pulled/<host>/backfill/<logname> (the unit's cron_logs/ copy, unchanged).
# Usage: bash runs/weekend_20261010/backfill_cycle_logs.sh   (start it at ~:00 of a wake hour)
set -u
cd "$(dirname "$0")"
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)
END=$(( $(date +%s) + 600 ))
for h in bmcam003 bmcam004; do
  mkdir -p pulled/$h/backfill
  names=$(for f in pulled/$h/cycle_2026-10-*.log; do grep -q "halt uptime_s" "$f" || head -1 "$f" | awk '{print $2}'; done | sed 's#cron_logs/##' | sort -u)
  echo "$(date -u +%T) $h: $(echo "$names" | wc -l | tr -d ' ') logs to backfill"
  done_=0
  while [ $done_ = 0 ] && [ "$(date +%s)" -lt $END ]; do
    if echo "$names" | ssh "${O[@]}" pi@$h 'cd /home/pi/BM_Devel_Pi/cron_logs && xargs tar cf - 2>/dev/null' | tar xf - -C pulled/$h/backfill 2>/dev/null \
       && [ "$(ls pulled/$h/backfill | wc -l)" -gt 0 ]; then done_=1; else sleep 15; fi
  done
  echo "$(date -u +%T) $h: got $(ls pulled/$h/backfill | wc -l | tr -d ' ') files, with halt line: $(grep -l 'halt uptime_s' pulled/$h/backfill/* 2>/dev/null | wc -l | tr -d ' ')"
done
