#!/bin/bash
# Sprint26 S3a bench driver (Mac side): four live cycles on bmcam003 from bench copies
# (halt dry-run), one per (runtime, media), with the bus held on. Console pings on
# SPOT-33507C (bmcam/cmd, harmless) show the command path on hardware:
#   26301  sent BEFORE the supervisor stills cycle (no subscriber yet; the mote caches
#          it) -> does the W4 boot drain apply it this boot?
#   26302  ~120 s into the supervisor stills cycle (mid-burst) -> W2: ack after END
#   26303  ~120 s into the legacy stills cycle (mid-burst) -> legacy: ack mid-burst
# Output: bench.log (+ per-cycle logs pulled from the Pi into live/).
HERE="$(cd "$(dirname "$0")" && pwd)"
cyc() {   # runtime mode [ping_id_at_120s]
  echo "=== $1 $2 start $(date -u +%FT%TZ)"
  if [ -n "${3:-}" ]; then
    ( sleep 120; "$HERE/console.sh" SPOT-33507C "bm pub bmcam/cmd {\"id\":$3,\"c\":\"ping\"} 1 1" 3 > /dev/null; echo "[ping] $3 sent $(date -u +%T)" ) &
  fi
  ssh -o BatchMode=yes pi@bmcam003 "bash /home/pi/s3abench/live_side.sh $1 $2" < /dev/null 2>&1
  wait
  echo "=== $1 $2 end $(date -u +%FT%TZ)"
}
cyc legacy video
cyc supervisor video
cyc legacy stills 26303
"$HERE/console.sh" SPOT-33507C "bm pub bmcam/cmd {\"id\":26301,\"c\":\"ping\"} 1 1" 3 > /dev/null
echo "[ping] 26301 sent $(date -u +%T) (before the supervisor stills cycle)"
sleep 5
cyc supervisor stills 26302
mkdir -p "$HERE/live" && scp -q "pi@bmcam003:/home/pi/s3abench/live/*.log" "$HERE/live/" && echo "[bench] logs pulled"
echo "[bench] DONE $(date -u +%FT%TZ)"
