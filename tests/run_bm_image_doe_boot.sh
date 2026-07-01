#!/usr/bin/env bash
set -Eeuo pipefail

APP="/home/pi/BM_Devel_Pi"
PY_SCRIPT="$APP/doe_capture_quality_sweep.py"
LOG_DIR="$APP/doe_boot_logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME="$(hostname)"
LOG_FILE="$LOG_DIR/${HOSTNAME}_doe_boot_${STAMP}.log"

mkdir -p "$LOG_DIR" "$APP/doe_runs"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "============================================================"
echo "[DOE_BOOT] BM image DOE boot test starting"
echo "[DOE_BOOT] start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[DOE_BOOT] hostname=$HOSTNAME"
echo "[DOE_BOOT] app_dir=$APP"
echo "[DOE_BOOT] log_file=$LOG_FILE"
echo "[DOE_BOOT] python=$(/usr/bin/python3 --version 2>&1)"
echo "============================================================"

if [ ! -f "$PY_SCRIPT" ]; then
  echo "[DOE_BOOT] ERROR: missing DOE script: $PY_SCRIPT"
  exit 1
fi

# Let camera stack, filesystem, and power rail settle after boot.
BOOT_DELAY_SEC="${DOE_BOOT_DELAY_SEC:-45}"
echo "[DOE_BOOT] sleeping ${BOOT_DELAY_SEC}s before camera test"
sleep "$BOOT_DELAY_SEC"

cd "$APP"

echo "[DOE_BOOT] checking DOE script syntax"
/usr/bin/python3 -m py_compile "$PY_SCRIPT"

echo "[DOE_BOOT] running JPEG-only underwater DOE"
set +e
/usr/bin/python3 -u "$PY_SCRIPT" \
  --tag underwater_jpeg_v1 \
  --resolutions 480p 720p 420sq 720sq \
  --source-modes jpeg \
  --qualities 25 35 45 55 65 75 \
  --source-jpeg-quality 95 \
  --link-throughput-kbps 0.361 \
  --target-transmit-min 16 \
  --hard-transmit-min 18
EXIT_CODE=$?
set -e

echo "[DOE_BOOT] doe_exit_code=$EXIT_CODE"
echo "[DOE_BOOT] end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sync
echo "============================================================"

exit "$EXIT_CODE"
