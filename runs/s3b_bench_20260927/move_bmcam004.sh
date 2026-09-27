#!/bin/bash
# S3b step 0: at the 01:00Z bus window, catch bmcam004 (watcher disarms + backs up), then
# follow_bmcam004.sh (deploy development = S3a, commands.runtime: supervisor, verify,
# re-arm, halt). Follow only runs if the watcher's disarm is confirmed.
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/watcher_host.sh" bmcam004 > "$HERE/bmcam004_watcher.log" 2>&1
cat "$HERE/bmcam004_watcher.log"
if grep -q "DISABLED s3abench" "$HERE/bmcam004_watcher.log"; then
  bash "$HERE/follow_bmcam004.sh" 2>&1 | tee "$HERE/bmcam004_follow.log"
else
  echo "[move] watcher did not confirm disarm; NOT deploying"; exit 1
fi
