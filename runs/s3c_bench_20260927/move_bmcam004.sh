#!/bin/bash
# S3c step 0: at an :00 bus window, catch bmcam004 (watcher disarms + backs up to
# /home/pi/s3bbench/backup), then follow_bmcam004.sh (deploy development = S3b, verify, re-arm,
# halt). Follow only runs if the watcher's disarm is confirmed. That hour's clip is lost.
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/watcher_s3b.sh" bmcam004 > "$HERE/bmcam004_watcher.log" 2>&1
cat "$HERE/bmcam004_watcher.log"
if grep -q "DISABLED s3bbench" "$HERE/bmcam004_watcher.log"; then
  bash "$HERE/follow_bmcam004.sh" 2>&1 | tee "$HERE/bmcam004_follow.log"
else
  echo "[move] watcher did not confirm disarm; NOT deploying"; exit 1
fi
