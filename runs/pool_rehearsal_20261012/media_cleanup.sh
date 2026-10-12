#!/bin/bash
# media_cleanup.sh: delete MEDIA ONLY on one bench camera (Nick's request 2026-10-12 11:05 AM, relayed by the EM).
# FOR NICK TO RUN HIMSELF (TE2 does not hard-delete files). Bench units only.
#
# Deletes:  files under /home/pi/BM_Devel_Pi/images/ and /home/pi/BM_Devel_Pi/videos/ older than 15 min
#           (captures, sidecars, rpicam logs, mp4 + thumbs + per-video json). The 15-min guard keeps the
#           current wake's capture safe if the unit is mid-cycle.
# Keeps:    sent/ (self-heal records, ~10 MB: heals for 58331/58313 keep working), code, YAML, command state,
#           cron_logs/, crontab, /dev/shm (that is the live CONFIG render, not media; wiping it is the bm #97 bug).
# Snapshot of what this removes: delete_snapshot_<host>_20261012T18xxZ.txt (same folder).
# Run while the unit is awake (hourly wake :01-:07, or with the bus held on).
# Usage:   bash runs/pool_rehearsal_20261012/media_cleanup.sh bmcam003      (then bmcam004)
set -u
H=${1:?bmcam003 or bmcam004}
case $H in bmcam003|bmcam004) ;; *) echo "bench units only (bmcam003/bmcam004)"; exit 2;; esac
ssh -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=6 pi@$H 'set -u
cd /home/pi/BM_Devel_Pi || exit 1
echo "== $(hostname) $(date -u +%FT%TZ) BEFORE"; df -h / | tail -1
for d in images videos; do echo "$d: $(find $d -type f -mmin +15 | wc -l) files to delete, $(du -sh $d | cut -f1) total"; done
find images videos -type f -mmin +15 -delete
echo "== AFTER"; df -h / | tail -1
for d in images videos sent; do echo "$d: $(find $d -type f | wc -l) files left, $(du -sh $d | cut -f1)"; done' </dev/null
