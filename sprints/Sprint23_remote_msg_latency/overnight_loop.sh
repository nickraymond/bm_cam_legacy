#!/bin/bash
# Sprint23 — unattended hourly repeat of the Step C live-arrival test.
#
# Purpose : for each command id in [$1..$2], send ONE remote `ping` through
#           the local tester (tools/remote_msg_tester, 127.0.0.1:8771), then
#           run watch_delivery.sh until it is delivered and (not) acked.
#           One command in flight at a time; the next send happens only after
#           the previous watcher returns, so the rate is ~1 per hour.
# Inputs  : $1 first id, $2 last id. Tester server + serial monitor running.
# Output  : appends everything to runs/remote_msg_latency/overnight_<date>.log
#           and prints it. Stops (exit 1) on the first send the tester refuses,
#           or if the watcher reports the monitor died.
# Example : ./overnight_loop.sh 2308 2312
# Limits  : sends `ping` only — a stateless command. Does not touch the bus
#           schedule, the Spotter config, or the Pi.
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
LOG="$REPO/runs/remote_msg_latency/overnight_$(date -u +%Y%m%d).log"
SPOT="SPOT-31593C"
for id in $(seq "$1" "$2"); do
  # NOTE: resp must be set in THIS shell, not inside a `{ } | tee` pipeline —
  # a pipeline runs in a subshell and the variable is lost (bug hit 09:51Z).
  echo "################ id $id — send $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
  resp=$(curl -s -m 60 -X POST localhost:8771/api/send -H "Content-Type: application/json" \
    -d "{\"spotter_id\":\"$SPOT\",\"message\":\"bm pub bmcam/cmd {\\\"id\\\":$id,\\\"c\\\":\\\"ping\\\"} 1 1\"}")
  echo "tester: $resp" | tee -a "$LOG"
  echo "$resp" | grep -q '"result"' || { echo "SEND REFUSED — stopping" | tee -a "$LOG"; exit 1; }
  "$HERE/watch_delivery.sh" "$id" "$SPOT" | cut -c1-175 | tee -a "$LOG"
  grep -q "MONITOR DIED" "$LOG" && exit 1
  sleep 70   # Sofar: 1 request/min/Spotter
done
echo "################ loop done $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
