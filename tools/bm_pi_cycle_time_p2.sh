#!/bin/bash
# BM Sprint07 P2 — on-device cycle-time measurement (bmcam000, Pi Zero 2W)
#
# Purpose
# -------
# Measure the REAL parts of the per-image cycle on the Pi:
#   capture (production rpicam-still command) -> crop/downsample/encode
#   (tools/bm_pi_jpeg_encode.py, Sprint07 frozen geometry + shortlist cells)
# and record memory behavior (meminfo snapshots + CmaFree sampled during
# capture). Transmit time is NOT measured here — it is modeled Mac-side as
# message_count * 5 s (Sprint06 chunk model); no Spotter/ebox involved.
#
# The capture command replicates BM_Devel_Pi/process_image_v2.py
# _capture_native (image_pipeline: rpicam backend, 4608x2592 q95, -n,
# --timeout 2000, --metadata). Focus/camera-control args are NOT added
# (camera_schedule.yaml sets none on bmcam000 today).
#
# Field-ops guardrails baked in:
#   - aborts if any camera process is running (production owns /dev/video*)
#   - aborts if the boot capture-cycle lock is held
#   - backs up crontab into the run folder + ~/bmcam_cron_backups (nothing
#     is disabled — the only cron entry is @reboot; we do not reboot)
#   - read-only elsewhere; all outputs go to the timestamped run folder
#
# Usage (on the Pi, from the repo root):
#   bash tools/bm_pi_cycle_time_p2.sh [output_parent]
# Output: <output_parent|~/bm_sprint07_runs>/p2_cycle_<UTC>/
#   preflight.txt, crontab_backup.txt, capture_times.csv, cma_samples.csv,
#   meminfo_{before,after}.txt, native_capture_*.jpg(+metadata), encode/ run,
#   cycle_log.txt
#
# Known limitations: single-scene bench capture (whatever the camera sees);
# capture wall time includes the fixed --timeout 2000 ms AE/AWB settle.
set -u

PARENT="${1:-$HOME/bm_sprint07_runs}"
RUN="$PARENT/p2_cycle_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RUN"
LOG="$RUN/cycle_log.txt"
say() { echo "[p2-cycle] $*" | tee -a "$LOG"; }

say "run=$RUN"
say "host=$(hostname) user=$(whoami)"
cd "$(dirname "$0")/.." || exit 1
say "repo=$(pwd) rev=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

# ---- preflight guardrails ---------------------------------------------------
{
  echo "utc=$(date -u --iso-8601=seconds)"
  echo "--- board ---"; tr -d '\0' < /proc/device-tree/model; echo
  echo "--- camera procs ---"; ps aux | grep -E "main_pi_camera|libcamera|rpicam|picamera" | grep -v grep || echo none
  echo "--- cameras ---"; rpicam-still --list-cameras 2>&1 || libcamera-still --list-cameras 2>&1
} > "$RUN/preflight.txt" 2>&1

if ps aux | grep -E "main_pi_camera|libcamera-still|rpicam-still|picamera" | grep -v grep > /dev/null; then
  say "ABORT: camera process already running (see preflight.txt) — production may own /dev/video*"
  exit 1
fi
if ! flock -n /tmp/bmcam_capture.lock true 2>/dev/null; then
  say "ABORT: /tmp/bmcam_capture.lock is held — boot capture cycle active"
  exit 1
fi
if ! grep -qi "imx708\|Available cameras" "$RUN/preflight.txt"; then
  say "ABORT: no camera detected (see preflight.txt); rerun with a timed native load instead"
  exit 1
fi
crontab -l > "$RUN/crontab_backup.txt" 2>&1
mkdir -p ~/bmcam_cron_backups && cp "$RUN/crontab_backup.txt" ~/bmcam_cron_backups/crontab_p2_$(date -u +%Y%m%dT%H%M%SZ).txt
say "preflight OK: camera present, no camera procs, capture lock free, crontab backed up (nothing disabled)"

grep -E "MemTotal|MemAvailable|CmaTotal|CmaFree" /proc/meminfo > "$RUN/meminfo_before.txt"

# ---- CmaFree sampler (runs during captures) ---------------------------------
( echo "utc_epoch_s,CmaFree_kB,MemAvailable_kB"
  while [ ! -f "$RUN/.sampler_stop" ]; do
    cma=$(awk '/CmaFree/{print $2}' /proc/meminfo)
    mem=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
    echo "$(date +%s.%N),$cma,$mem"
    sleep 0.2
  done ) > "$RUN/cma_samples.csv" &
SAMPLER_PID=$!

# ---- timed production-style native captures ---------------------------------
CAPCMD="$(command -v rpicam-still || command -v libcamera-still)"
say "capture command: $CAPCMD (production args: -n --timeout 2000 4608x2592 q95 --metadata)"
echo "attempt,wall_s,bytes,exit_code" > "$RUN/capture_times.csv"
for i in 1 2 3; do
  out="$RUN/native_capture_$i.jpg"
  t0=$(date +%s.%N)
  "$CAPCMD" -n --timeout 2000 --width 4608 --height 2592 --quality 95 \
    --metadata "$RUN/native_capture_$i.metadata.json" -o "$out" \
    >> "$RUN/capture_stdout.log" 2>> "$RUN/capture_stderr.log"
  rc=$?
  t1=$(date +%s.%N)
  wall=$(echo "$t1 $t0" | awk '{printf "%.3f", $1-$2}')
  bytes=$(stat -c %s "$out" 2>/dev/null || echo 0)
  echo "$i,$wall,$bytes,$rc" >> "$RUN/capture_times.csv"
  say "capture $i: ${wall}s ${bytes}B rc=$rc"
  [ "$rc" -ne 0 ] && say "WARNING capture $i failed (rc=$rc)"
  sleep 1
done
touch "$RUN/.sampler_stop"; wait $SAMPLER_PID 2>/dev/null; rm -f "$RUN/.sampler_stop"

NATIVE="$RUN/native_capture_3.jpg"
if [ ! -s "$NATIVE" ]; then
  say "ABORT: no usable native capture"
  exit 1
fi

# ---- crop/downsample/encode on the real captured frame ----------------------
# Scene-centered Sprint07 frozen geometry; shortlist qualities x both modes.
say "encode pass: bm_pi_jpeg_encode.py on $(basename "$NATIVE")"
python3 tools/bm_pi_jpeg_encode.py \
  --images coral --coral-path "$NATIVE" \
  --crop-native 1504 846 1600 900 --output-width 1000 \
  --modes baseline progressive --qualities 9 13 15 \
  --timing-repeats 3 --output "$RUN/encode" 2>&1 | tee -a "$LOG"
enc_rc=${PIPESTATUS[0]}

grep -E "MemTotal|MemAvailable|CmaTotal|CmaFree" /proc/meminfo > "$RUN/meminfo_after.txt"

if [ "$enc_rc" -ne 0 ] || [ ! -s "$RUN/encode/results/encode_results.csv" ]; then
  say "FAIL: encode pass rc=$enc_rc or empty results"
  exit 1
fi
say "complete: $RUN"
