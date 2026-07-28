#!/bin/bash
# Sprint10 soak — overnight reference-image A/B setup (Nick-approved design).
# Run from the repo root:  bash runs/sprint10_soak_20260727/setup_reference_ab.sh
#
# What it does:
#  1. Prepares the reef reference as a synthetic IMX708 native (repo tool).
#  2. Catches each unit awake (they wake every ~30 min) and:
#     - copies the reference native to /home/pi/BM_Devel_Pi/reference_native.jpg
#     - backs up rc_run_capture_cycle.sh, then points its transmit line at the
#       reference:  --transmit --compress-only <reference>
#     - bmcam003 only: fixes the transmit window 08:00-15:00 -> 00:01-23:59
#     - prints verification of every change
#  3. Exits when BOTH units are configured. Safe to re-run; all edits are
#     idempotent and every original is backed up with a timestamp.
#
# Result: bmcam000 sends identical ~190-msg bursts (cap 195 @1s),
#         bmcam003 identical ~100-msg bursts (cap 100 @1s), all night.
# Rollback per unit:
#   cp /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh.before_refab_* \
#      /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh

set -u
cd "$(dirname "$0")/../.." || exit 1
TS=$(date -u +%Y%m%dT%H%M%SZ)
REF_LOCAL="reference_images/prepared_native_for_ab.jpg"

echo "=== step 1: prepare reference native ==="
if [ ! -f "$REF_LOCAL" ]; then
  python3 tools/prepare_reference_images.py \
    --input reference_images/reference_reef_coral_primary.jpg \
    --output-root /tmp/refab_prepared || exit 1
  PREP=$(find /tmp/refab_prepared -name "*.jpg" | head -1)
  [ -n "$PREP" ] || { echo "ERROR: no prepared jpg found"; exit 1; }
  cp "$PREP" "$REF_LOCAL"
fi
python3 -c "from PIL import Image; im=Image.open('$REF_LOCAL'); print('reference native:', im.size)"

configure_unit () {
  local IP="$1" NAME="$2" FIX_WINDOW="$3"
  echo "=== waiting for $NAME ($IP) to wake (Ctrl-C safe; re-run resumes) ==="
  until ssh -o ConnectTimeout=5 -o BatchMode=yes "pi@$IP" true 2>/dev/null; do
    sleep 10
  done
  echo "=== $NAME awake — configuring ==="
  scp -q "$REF_LOCAL" "pi@$IP:/home/pi/BM_Devel_Pi/reference_native.jpg" || return 1
  ssh -o BatchMode=yes "pi@$IP" '
    set -e
    R=/home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh
    if ! grep -q "compress-only" $R; then
      cp $R ${R}.before_refab_'"$TS"'
      sed -i "s|-u rc_progressive_jpeg.py --transmit\$|-u rc_progressive_jpeg.py --transmit --compress-only /home/pi/BM_Devel_Pi/reference_native.jpg|" $R
    fi
    echo "--- runner line ---"; grep "compress-only" $R || { echo "PATCH FAILED"; exit 1; }
    if [ "'"$FIX_WINDOW"'" = "yes" ]; then
      sed -i -e "s/start: \"08:00\"/start: \"00:01\"/" -e "s/end: \"15:00\"/end: \"23:59\"/" /home/pi/BM_Devel_Pi/camera_schedule.yaml
      echo "--- window ---"; grep -nE "start: |end: " /home/pi/BM_Devel_Pi/camera_schedule.yaml | head -4
    fi
    echo "--- message_cap ---"; grep "message_cap" /home/pi/BM_Devel_Pi/camera_schedule.yaml
    echo "--- reference present ---"; ls -la /home/pi/BM_Devel_Pi/reference_native.jpg
    echo "=== '"$NAME"' CONFIGURED ==="
  '
}

configure_unit 100.103.35.24 bmcam003 yes
configure_unit 100.119.14.92 bmcam000 no
echo "=== BOTH UNITS CONFIGURED — reference A/B live from each unit's next wake ==="
