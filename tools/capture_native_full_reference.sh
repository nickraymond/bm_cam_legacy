#!/usr/bin/env bash
set -Eeuo pipefail

# capture_native_full_reference_v2.sh
#
# Mac-side one-shot script to capture and download a full-native JPEG still from a remote
# Bristlemouth/Raspberry Pi camera using the efficient rpicam-still/libcamera-still path.
#
# Why this exists:
#   The production Picamera2 path requests a final output stream size and can allocate
#   large BGR888 buffers. For a high-quality reference baseline, the simpler camera-app
#   path can capture a native full-resolution JPEG efficiently:
#
#     libcamera-still -n -t 500 --quality 95 -o native_full_q95.jpg
#
# CMA note, NOT performed at runtime:
#   Full native IMX708 capture needs enough contiguous CMA memory.
#   Check:
#     ssh pi@bmcam002 'grep -E "MemAvailable|CmaTotal|CmaFree" /proc/meminfo'
#   If CmaTotal is only 65536 kB and native capture fails with
#   "Unable to request buffers: Cannot allocate memory", set cma=128M in cmdline:
#
#     ssh pi@bmcam002 'bash -s' <<'REMOTE'
#     set -euo pipefail
#     if [ -f /boot/firmware/cmdline.txt ]; then CMDLINE=/boot/firmware/cmdline.txt; else CMDLINE=/boot/cmdline.txt; fi
#     sudo cp "$CMDLINE" "${CMDLINE}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
#     if grep -qw "cma=[0-9][0-9]*[MG]" "$CMDLINE"; then
#       sudo sed -i -E "s/\bcma=[0-9]+[MG]\b/cma=128M/" "$CMDLINE"
#     else
#       sudo sed -i "s/$/ cma=128M/" "$CMDLINE"
#     fi
#     sudo reboot
#     REMOTE
#
# Cron/camera ownership note:
#   If a cron-launched production job is running, it will own /dev/video* and libcamera-still
#   will fail with "Pipeline handler in use by another process". This script can temporarily
#   back up and disable the user's crontab and stop common camera processes, then restore
#   the crontab after capture.
#
# Usage:
#   HOST=bmcam002 ./capture_native_full_reference.sh
#
# Optional:
#   HOST=bmcam002 QUALITY=95 TIMEOUT_MS=500 ./capture_native_full_reference.sh
#   HOST=bmcam002 DISABLE_CRON=0 STOP_CAMERA_JOBS=0 ./capture_native_full_reference.sh
#   HOST=bmcam002 RESTORE_CRON=0 ./capture_native_full_reference.sh

HOST="${HOST:-bmcam002}"
REMOTE_USER="${REMOTE_USER:-pi}"
REMOTE_APP="${REMOTE_APP:-/home/pi/BM_Devel_Pi}"
QUALITY="${QUALITY:-95}"
TIMEOUT_MS="${TIMEOUT_MS:-500}"
RUN_TAG="${RUN_TAG:-native_full_reference_$(date -u +%Y%m%dT%H%M%SZ)}"
LOCAL_BASE="${LOCAL_BASE:-$HOME/Downloads/bm_native_reference_captures}"
LOCAL_OUT="$LOCAL_BASE/$RUN_TAG"
DISABLE_CRON="${DISABLE_CRON:-1}"
RESTORE_CRON="${RESTORE_CRON:-1}"
STOP_CAMERA_JOBS="${STOP_CAMERA_JOBS:-1}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-20}"

log() { printf '\n[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

b64() {
  printf '%s' "$1" | python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())'
}

ssh_cam() {
  ssh -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" \
      -o ServerAliveInterval=10 \
      -o ServerAliveCountMax=6 \
      "$REMOTE_USER@$HOST" "$@"
}

cat <<BANNER
============================================================
BM NATIVE FULL-RES REFERENCE CAPTURE v2
============================================================
Host:             $HOST
Remote app:       $REMOTE_APP
Run tag:          $RUN_TAG
Local output:     $LOCAL_OUT
JPEG quality:     $QUALITY
Timeout:          ${TIMEOUT_MS} ms
Disable cron:     $DISABLE_CRON
Restore cron:     $RESTORE_CRON
Stop camera jobs: $STOP_CAMERA_JOBS
Capture path:     rpicam-still/libcamera-still native full JPEG, no width/height
============================================================
BANNER

mkdir -p "$LOCAL_OUT"

RUN_TAG_B64="$(b64 "$RUN_TAG")"
REMOTE_APP_B64="$(b64 "$REMOTE_APP")"
QUALITY_B64="$(b64 "$QUALITY")"
TIMEOUT_MS_B64="$(b64 "$TIMEOUT_MS")"
DISABLE_CRON_B64="$(b64 "$DISABLE_CRON")"
RESTORE_CRON_B64="$(b64 "$RESTORE_CRON")"
STOP_CAMERA_JOBS_B64="$(b64 "$STOP_CAMERA_JOBS")"

log "Running remote capture sequence"
REMOTE_OUTPUT="$(ssh_cam \
  "RUN_TAG_B64='$RUN_TAG_B64' REMOTE_APP_B64='$REMOTE_APP_B64' QUALITY_B64='$QUALITY_B64' TIMEOUT_MS_B64='$TIMEOUT_MS_B64' DISABLE_CRON_B64='$DISABLE_CRON_B64' RESTORE_CRON_B64='$RESTORE_CRON_B64' STOP_CAMERA_JOBS_B64='$STOP_CAMERA_JOBS_B64' bash -s" <<'REMOTE'
set -Eeuo pipefail

decode_b64() { printf '%s' "$1" | base64 -d; }

RUN_TAG="$(decode_b64 "$RUN_TAG_B64")"
APP_DIR="$(decode_b64 "$REMOTE_APP_B64")"
QUALITY="$(decode_b64 "$QUALITY_B64")"
TIMEOUT_MS="$(decode_b64 "$TIMEOUT_MS_B64")"
DISABLE_CRON="$(decode_b64 "$DISABLE_CRON_B64")"
RESTORE_CRON="$(decode_b64 "$RESTORE_CRON_B64")"
STOP_CAMERA_JOBS="$(decode_b64 "$STOP_CAMERA_JOBS_B64")"

HOSTNAME="$(hostname)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$APP_DIR/high_quality_reference_baseline_${HOSTNAME}_${RUN_TAG}_${STAMP}"
mkdir -p "$OUT"
cd "$OUT"

echo "[REMOTE] hostname=$HOSTNAME"
echo "[REMOTE] out=$OUT"
echo "[REMOTE] date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[REMOTE] cma_before:"
grep -E "MemAvailable|CmaTotal|CmaFree" /proc/meminfo || true

CAM_APP=""
if command -v rpicam-still >/dev/null 2>&1; then
  CAM_APP="$(command -v rpicam-still)"
elif command -v libcamera-still >/dev/null 2>&1; then
  CAM_APP="$(command -v libcamera-still)"
else
  echo "[REMOTE] ERROR: neither rpicam-still nor libcamera-still found" >&2
  exit 1
fi
echo "[REMOTE] cam_app=$CAM_APP"

CRON_BACKUP=""
if [ "$DISABLE_CRON" = "1" ]; then
  mkdir -p /home/pi/cron_backups
  CRON_BACKUP="/home/pi/cron_backups/crontab_before_native_reference_${STAMP}.txt"
  crontab -l > "$CRON_BACKUP" 2>/dev/null || true
  echo "[REMOTE] crontab_backup=$CRON_BACKUP"
  crontab -r 2>/dev/null || true
  echo "[REMOTE] user crontab temporarily disabled"
fi

if [ "$STOP_CAMERA_JOBS" = "1" ]; then
  echo "[REMOTE] stopping likely camera-owning processes"
  sudo pkill -f "main_pi_camera.py" || true
  sudo pkill -f "process_image_v2.py" || true
  sudo pkill -f "capture_one_resolution.py" || true
  sudo pkill -f "capture_one_crop.py" || true
  sudo pkill -f "libcamera-still" || true
  sudo pkill -f "rpicam-still" || true
  sleep 3
fi

echo "[REMOTE] camera processes before capture:"
ps aux | grep -E "main_pi_camera|process_image|capture_one|picamera|libcamera-still|rpicam-still" | grep -v grep || true

IMAGE="native_full_q${QUALITY}.jpg"
META="native_full_q${QUALITY}.metadata.json"
STDOUT_LOG="native_full_q${QUALITY}.stdout.log"
STDERR_LOG="native_full_q${QUALITY}.stderr.log"

META_ARGS=()
if "$CAM_APP" --help 2>&1 | grep -q -- "--metadata"; then
  META_ARGS=(--metadata "$META")
fi

echo "[REMOTE] capture command:"
printf '  %q' "$CAM_APP" -n -t "$TIMEOUT_MS" --quality "$QUALITY" "${META_ARGS[@]}" -o "$IMAGE"
printf '\n'

set +e
"$CAM_APP" \
  -n \
  -t "$TIMEOUT_MS" \
  --quality "$QUALITY" \
  "${META_ARGS[@]}" \
  -o "$IMAGE" \
  > "$STDOUT_LOG" \
  2> "$STDERR_LOG"
RC=$?
set -e

echo "[REMOTE] capture_rc=$RC"

if [ "$RC" != "0" ] || [ ! -s "$IMAGE" ]; then
  echo "[REMOTE] ERROR: capture failed or produced empty image" >&2
  echo "[REMOTE] stderr tail:" >&2
  tail -80 "$STDERR_LOG" >&2 || true

  if [ "$DISABLE_CRON" = "1" ] && [ "$RESTORE_CRON" = "1" ] && [ -n "$CRON_BACKUP" ] && [ -s "$CRON_BACKUP" ]; then
    crontab "$CRON_BACKUP" || true
    echo "[REMOTE] crontab restored after failed capture"
  fi

  echo "REMOTE_REFERENCE_DIR=$OUT"
  exit 2
fi

python3 - <<PY
from pathlib import Path
from PIL import Image
import json

image = Path("$IMAGE")
info = {
    "hostname": "$HOSTNAME",
    "captured_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "camera_app": "$CAM_APP",
    "quality": int("$QUALITY"),
    "timeout_ms": int("$TIMEOUT_MS"),
    "image_path": str(image.resolve()),
    "image_size_bytes": image.stat().st_size,
    "image_size_kb": round(image.stat().st_size / 1024, 3),
    "cron_backup": "$CRON_BACKUP",
    "crontab_restored": False,
}
with Image.open(image) as im:
    info["width"] = im.size[0]
    info["height"] = im.size[1]

try:
    mem = Path("/proc/meminfo").read_text()
    for key in ["MemAvailable", "CmaTotal", "CmaFree"]:
        for line in mem.splitlines():
            if line.startswith(key + ":"):
                info[key] = line.split(":", 1)[1].strip()
except Exception:
    pass

Path("capture_summary.json").write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
print("[REMOTE] image_size=", (info["width"], info["height"]))
print("[REMOTE] file_size_kb=", info["image_size_kb"])
PY

if [ "$DISABLE_CRON" = "1" ] && [ "$RESTORE_CRON" = "1" ]; then
  if [ -n "$CRON_BACKUP" ] && [ -s "$CRON_BACKUP" ]; then
    crontab "$CRON_BACKUP" || true
    python3 - <<'PY'
import json
from pathlib import Path
p = Path("capture_summary.json")
d = json.loads(p.read_text())
d["crontab_restored"] = True
p.write_text(json.dumps(d, indent=2, sort_keys=True))
PY
    echo "[REMOTE] crontab restored"
  else
    echo "[REMOTE] no non-empty crontab backup to restore"
  fi
fi

echo "[REMOTE] final files:"
ls -lh

echo "REMOTE_REFERENCE_DIR=$OUT"
REMOTE
)"
echo "$REMOTE_OUTPUT"

REMOTE_DIR="$(echo "$REMOTE_OUTPUT" | awk -F= '/^REMOTE_REFERENCE_DIR=/{print $2}' | tail -1)"
if [[ -z "$REMOTE_DIR" ]]; then
  echo "ERROR: failed to parse REMOTE_REFERENCE_DIR" >&2
  exit 1
fi

log "Downloading remote reference folder"
LOCAL_RUN_DIR="$LOCAL_OUT/$(basename "$REMOTE_DIR")"
mkdir -p "$LOCAL_RUN_DIR"

ssh_cam "cd '$REMOTE_DIR' && find . -type f | sort" > "$LOCAL_OUT/file_list.txt"

while IFS= read -r rel; do
  rel="${rel#./}"
  mkdir -p "$LOCAL_RUN_DIR/$(dirname "$rel")"
  echo "Downloading $rel"
  success=0
  for attempt in 1 2 3 4 5; do
    if scp \
      -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" \
      -o ServerAliveInterval=10 \
      -o ServerAliveCountMax=6 \
      "$REMOTE_USER@$HOST:${REMOTE_DIR}/${rel}" \
      "$LOCAL_RUN_DIR/${rel}"; then
      success=1
      break
    fi
    echo "Retry $attempt failed for $rel"
    sleep 5
  done
  if [ "$success" != "1" ]; then
    echo "ERROR: failed to download $rel after retries" >&2
    exit 3
  fi
done < "$LOCAL_OUT/file_list.txt"

log "Local download complete"
du -sh "$LOCAL_RUN_DIR" || true
find "$LOCAL_RUN_DIR" -type f | sort

log "Summary"
python3 - <<PY
from pathlib import Path
import json
from PIL import Image

run_dir = Path("$LOCAL_RUN_DIR")
summary = run_dir / "capture_summary.json"
if summary.exists():
    d = json.loads(summary.read_text())
    print(json.dumps(d, indent=2, sort_keys=True))
else:
    for p in run_dir.glob("*.jpg"):
        with Image.open(p) as im:
            print(p.name, im.size, round(p.stat().st_size/1024, 1), "KB")
PY

IMAGE_PATH="$(find "$LOCAL_RUN_DIR" -maxdepth 1 -name 'native_full_q*.jpg' | head -1)"
cat <<EOF

DONE
------------------------------------------------------------------------
LOCAL_REFERENCE_DIR=$LOCAL_RUN_DIR
REMOTE_REFERENCE_DIR=$REMOTE_DIR
IMAGE=$IMAGE_PATH
SUMMARY=$LOCAL_RUN_DIR/capture_summary.json
------------------------------------------------------------------------
EOF

open "$LOCAL_RUN_DIR" >/dev/null 2>&1 || true
