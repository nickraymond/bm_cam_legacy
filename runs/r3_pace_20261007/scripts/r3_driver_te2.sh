#!/bin/bash
# TE2: detached R3 switch driver (session crons did not fire on 2026-10-07). Runs r3_switch_at_te2.sh for each coming hour in
# turn (each call waits for HH:01:30Z itself); stops when the switch tool returns 3 (stopped/complete) or after 24 h.
# Log: runs/r3_pace_20261007/driver_te2.log. Stop: pkill -f '[r]3_driver_te2.sh' (and '[r]3_switch_at_te2.sh').
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
HH=$(( ( $(date -u +%-H) + 1 ) % 24 ))
for i in $(seq 1 24); do
  echo "$(date -u +%FT%TZ) driver: launching switch for wake ${HH}Z"
  bash runs/r3_pace_20261007/scripts/r3_switch_at_te2.sh $HH; rc=$?
  echo "$(date -u +%FT%TZ) driver: wake ${HH}Z rc $rc"
  [ $rc = 3 ] && { echo "driver: schedule stopped/complete -> exit"; exit 0; }
  HH=$(( (HH + 1) % 24 ))
done
