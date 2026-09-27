#!/bin/bash
# Sprint26 S3a bench (runs ON bmcam003): one cycle per (runtime, media) over the real
# BM bus, from bench COPIES (make_bench_dirs.py: halt dry-run, state -> the copy).
# Usage: live_side.sh RUNTIME MODE      (RUNTIME legacy|supervisor, MODE video|stills)
# Every log line is prefixed with the Pi's UTC time (fresh-read latency, W-item timing).
# The live config/state files are never used; --skip-time-window as in S1/S2.
set -u
RUNTIME=$1; MODE=$2
B=/home/pi/s3abench; A=/home/pi/BM_Devel_Pi; L=$B/live; mkdir -p $L
cd $A
LOG=$L/cycle_${RUNTIME}_${MODE}.log
echo "[side] $(hostname) sha=$(cat software_sha.txt) runtime=$RUNTIME mode=$MODE start=$(date -u +%FT%T.%3NZ)"
python3 -u $B/rssrun.py rc_progressive_jpeg.py --runtime $RUNTIME \
    --config-path $B/cfg_$MODE/camera_schedule.yaml --transmit --skip-time-window 2>&1 \
  | while IFS= read -r l; do printf '%s %s\n' "$(date -u +%T.%3N)" "$l"; done > $LOG
echo "[side] end=$(date -u +%FT%T.%3NZ)"
grep -E '\[BENCH\]|\[RUNTIME\]|\[SUP\]|transmit done|time-sync subscribe|spotter UTC decoded|schedule gate|shared UART open|\[PORT\]|cycle end|ERROR|Traceback|media key|\[KEY\]' $LOG | cut -c1-200
