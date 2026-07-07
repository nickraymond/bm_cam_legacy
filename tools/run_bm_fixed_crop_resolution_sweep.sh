#!/usr/bin/env bash
set -Eeuo pipefail

# BM fixed-ScalerCrop output-resolution sweep.
# Runs from Mac. SSHes into the Pi, captures one JPEG per output size with a fixed
# production-720-style ScalerCrop, downloads the files, and builds cut sheets.
#
# Default fixed crop:
#   [768, 432, 3072, 1728]
# This is the production 720p ScalerCrop observed in the production-path probe.

HOST="${HOST:-bmcam001}"
REMOTE_USER="${REMOTE_USER:-pi}"
REMOTE_APP="${REMOTE_APP:-/home/pi/BM_Devel_Pi}"
RUN_TAG="${RUN_TAG:-fixed_crop_res_sweep_$(date -u +%Y%m%dT%H%M%SZ)}"
LOCAL_BASE="${LOCAL_BASE:-$HOME/Downloads/bm_fixed_crop_resolution_sweep}"
LOCAL_OUT="$LOCAL_BASE/$RUN_TAG"

# Fixed crop: x:y:w:h in sensor coordinates.
SCALER_CROP="${SCALER_CROP:-768:432:3072:1728}"

# Output sizes to request while holding ScalerCrop constant.
# Highest useful no-upscale size for this crop is 3072x1728.
OUTPUT_SPECS="${OUTPUT_SPECS:-3072x1728 2688x1512 2304x1296 1920x1080 1600x900 1280x720 1024x576 854x480 640x360}"

SETTLE_SEC="${SETTLE_SEC:-2.0}"
JPEG_QUALITY="${JPEG_QUALITY:-95}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"

log() { printf '\n[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

b64() {
  printf '%s' "$1" | python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())'
}

ssh_cam() {
  ssh -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" -o ServerAliveInterval=15 -o ServerAliveCountMax=2 "$REMOTE_USER@$HOST" "$@"
}

cat <<EOF
============================================================
BM FIXED CROP RESOLUTION SWEEP
============================================================
Host:          $HOST
Remote app:    $REMOTE_APP
Run tag:       $RUN_TAG
Local output:  $LOCAL_OUT
ScalerCrop:    $SCALER_CROP
Output specs:  $OUTPUT_SPECS
Settle sec:    $SETTLE_SEC
JPEG quality:  $JPEG_QUALITY
Capture path:  Picamera2 create_still_configuration(main={size}) + fixed ScalerCrop
Purpose:       Hold production-720 crop constant, vary output pixel density
============================================================
EOF

mkdir -p "$LOCAL_OUT"

RUN_TAG_B64="$(b64 "$RUN_TAG")"
REMOTE_APP_B64="$(b64 "$REMOTE_APP")"
OUTPUT_SPECS_B64="$(b64 "$OUTPUT_SPECS")"
SCALER_CROP_B64="$(b64 "$SCALER_CROP")"
SETTLE_SEC_B64="$(b64 "$SETTLE_SEC")"
JPEG_QUALITY_B64="$(b64 "$JPEG_QUALITY")"

log "Creating remote run directory and helper"
REMOTE_CREATE_OUTPUT="$(ssh_cam \
  "RUN_TAG_B64='$RUN_TAG_B64' REMOTE_APP_B64='$REMOTE_APP_B64' OUTPUT_SPECS_B64='$OUTPUT_SPECS_B64' SCALER_CROP_B64='$SCALER_CROP_B64' SETTLE_SEC_B64='$SETTLE_SEC_B64' JPEG_QUALITY_B64='$JPEG_QUALITY_B64' bash -s" <<'REMOTE'
set -Eeuo pipefail

decode_b64() { printf '%s' "$1" | base64 -d; }

RUN_TAG="$(decode_b64 "$RUN_TAG_B64")"
APP="$(decode_b64 "$REMOTE_APP_B64")"
OUTPUT_SPECS="$(decode_b64 "$OUTPUT_SPECS_B64")"
SCALER_CROP="$(decode_b64 "$SCALER_CROP_B64")"
SETTLE_SEC="$(decode_b64 "$SETTLE_SEC_B64")"
JPEG_QUALITY="$(decode_b64 "$JPEG_QUALITY_B64")"

HOSTNAME="$(hostname)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$APP/fixed_crop_res_sweep_${HOSTNAME}_${RUN_TAG}_${STAMP}"
mkdir -p "$OUT"

cat > "$OUT/capture_one_resolution.py" <<'PY'
#!/usr/bin/env python3
import json
import os
import socket
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from picamera2 import Picamera2

def iso_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def json_safe(v):
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    try:
        return float(v)
    except Exception:
        return str(v)

def main():
    out_dir = Path(os.environ["OUT_DIR"]).resolve()
    label = os.environ["LABEL"]
    output_w = int(os.environ["OUTPUT_W"])
    output_h = int(os.environ["OUTPUT_H"])
    crop_x = int(os.environ["CROP_X"])
    crop_y = int(os.environ["CROP_Y"])
    crop_w = int(os.environ["CROP_W"])
    crop_h = int(os.environ["CROP_H"])
    settle_sec = float(os.environ.get("SETTLE_SEC", "2.0"))
    jpeg_quality = int(os.environ.get("JPEG_QUALITY", "95"))

    label_dir = out_dir / label
    label_dir.mkdir(parents=True, exist_ok=True)

    img_name = f"{label}_{output_w}x{output_h}_crop_{crop_x}_{crop_y}_{crop_w}_{crop_h}.jpg"
    img_path = label_dir / img_name
    meta_path = Path(f"{img_path}.capture_metadata.json")
    result_path = label_dir / "result.json"

    requested_crop = [crop_x, crop_y, crop_w, crop_h]
    requested_output = [output_w, output_h]

    result = {
        "label": label,
        "ok": False,
        "hostname": socket.gethostname(),
        "captured_at_utc": iso_utc(),
        "requested_output_width": output_w,
        "requested_output_height": output_h,
        "requested_scaler_crop": requested_crop,
        "image_path": str(img_path),
        "metadata_path": str(meta_path),
    }

    picam2 = None
    try:
        print("=" * 72, flush=True)
        print(f"[REMOTE_RES] label={label} output={output_w}x{output_h} crop={requested_crop}", flush=True)

        picam2 = Picamera2()
        config = picam2.create_still_configuration(main={"size": (output_w, output_h)})
        picam2.configure(config)

        # This is the critical test variable: fixed explicit ROI.
        picam2.set_controls({"ScalerCrop": (crop_x, crop_y, crop_w, crop_h)})

        picam2.start()
        time.sleep(settle_sec)

        capture_metadata = picam2.capture_file(str(img_path))
        if not isinstance(capture_metadata, dict):
            try:
                capture_metadata = picam2.capture_metadata() or {}
            except Exception:
                capture_metadata = {}

        capture_metadata = json_safe(capture_metadata or {})
        meta_path.write_text(json.dumps(capture_metadata, indent=2, sort_keys=True), encoding="utf-8")

        with Image.open(img_path) as im:
            actual_w, actual_h = im.size

        size_bytes = img_path.stat().st_size
        scaler_crop = capture_metadata.get("ScalerCrop")

        # Useful for interpreting pixel density.
        sensor_px_per_output_px_x = crop_w / output_w
        sensor_px_per_output_px_y = crop_h / output_h
        output_px_per_sensor_px_x = output_w / crop_w
        output_px_per_sensor_px_y = output_h / crop_h

        result.update({
            "ok": True,
            "actual_width": actual_w,
            "actual_height": actual_h,
            "image_size_bytes": size_bytes,
            "image_size_kb": round(size_bytes / 1024, 3),
            "metadata_scaler_crop": scaler_crop,
            "exposure_time": capture_metadata.get("ExposureTime"),
            "analogue_gain": capture_metadata.get("AnalogueGain"),
            "digital_gain": capture_metadata.get("DigitalGain"),
            "colour_gains": capture_metadata.get("ColourGains"),
            "lens_position": capture_metadata.get("LensPosition"),
            "af_state": capture_metadata.get("AfState"),
            "focus_fom": capture_metadata.get("FocusFoM"),
            "lux": capture_metadata.get("Lux"),
            "sensor_px_per_output_px_x": round(sensor_px_per_output_px_x, 4),
            "sensor_px_per_output_px_y": round(sensor_px_per_output_px_y, 4),
            "output_px_per_sensor_px_x": round(output_px_per_sensor_px_x, 4),
            "output_px_per_sensor_px_y": round(output_px_per_sensor_px_y, 4),
        })

        print(
            f"[REMOTE_RES] ok=true label={label} actual={actual_w}x{actual_h} "
            f"size_kb={result['image_size_kb']} ScalerCrop={scaler_crop} "
            f"sensor/output={sensor_px_per_output_px_x:.3f}",
            flush=True,
        )

    except Exception as exc:
        result.update({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": "\n".join(traceback.format_exc().splitlines()[-12:]),
        })
        print(f"[REMOTE_RES] ok=false label={label} error={result['error']}", flush=True)
    finally:
        try:
            if picam2 is not None:
                picam2.stop()
        except Exception:
            pass
        try:
            if picam2 is not None:
                picam2.close()
        except Exception:
            pass

    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if result.get("ok") else 2

if __name__ == "__main__":
    raise SystemExit(main())
PY

chmod +x "$OUT/capture_one_resolution.py"

cat > "$OUT/run_info.json" <<JSON
{
  "run_tag": "$RUN_TAG",
  "hostname": "$HOSTNAME",
  "created_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "remote_app": "$APP",
  "output_specs": "$OUTPUT_SPECS",
  "scaler_crop": "$SCALER_CROP",
  "settle_sec": "$SETTLE_SEC",
  "jpeg_quality": "$JPEG_QUALITY",
  "note": "Fixed ScalerCrop resolution sweep. One fresh Picamera2 process per output size."
}
JSON

echo "REMOTE_RES_SWEEP_DIR=$OUT"
REMOTE
)"
echo "$REMOTE_CREATE_OUTPUT"
REMOTE_DIR="$(echo "$REMOTE_CREATE_OUTPUT" | awk -F= '/^REMOTE_RES_SWEEP_DIR=/{print $2}' | tail -1)"
if [[ -z "$REMOTE_DIR" ]]; then
  echo "ERROR: failed to parse REMOTE_RES_SWEEP_DIR" >&2
  exit 1
fi

log "Running fixed-crop output-resolution captures on $HOST"
ssh_cam \
  "REMOTE_DIR='$REMOTE_DIR' OUTPUT_SPECS_B64='$OUTPUT_SPECS_B64' SCALER_CROP_B64='$SCALER_CROP_B64' SETTLE_SEC_B64='$SETTLE_SEC_B64' JPEG_QUALITY_B64='$JPEG_QUALITY_B64' bash -s" <<'REMOTE'
set -Eeuo pipefail
decode_b64() { printf '%s' "$1" | base64 -d; }

OUTPUT_SPECS="$(decode_b64 "$OUTPUT_SPECS_B64")"
SCALER_CROP="$(decode_b64 "$SCALER_CROP_B64")"
SETTLE_SEC="$(decode_b64 "$SETTLE_SEC_B64")"
JPEG_QUALITY="$(decode_b64 "$JPEG_QUALITY_B64")"

IFS=':' read -r CROP_X CROP_Y CROP_W CROP_H <<< "$SCALER_CROP"

echo "[REMOTE] hostname=$(hostname)"
echo "[REMOTE] out=$REMOTE_DIR"
echo "[REMOTE] date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[REMOTE] scaler_crop=$SCALER_CROP"
echo "[REMOTE] output_specs=$OUTPUT_SPECS"

for spec in $OUTPUT_SPECS; do
  clean="$(printf '%s' "$spec" | tr '[:upper:]' '[:lower:]')"
  if [[ "$clean" != *x* ]]; then
    echo "[REMOTE] WARN: skipping invalid output spec: $spec"
    continue
  fi
  OUTPUT_W="${clean%x*}"
  OUTPUT_H="${clean#*x}"
  LABEL="out_${OUTPUT_W}x${OUTPUT_H}"

  set +e
  OUT_DIR="$REMOTE_DIR" LABEL="$LABEL" OUTPUT_W="$OUTPUT_W" OUTPUT_H="$OUTPUT_H" \
    CROP_X="$CROP_X" CROP_Y="$CROP_Y" CROP_W="$CROP_W" CROP_H="$CROP_H" \
    SETTLE_SEC="$SETTLE_SEC" JPEG_QUALITY="$JPEG_QUALITY" \
    /usr/bin/python3 -u "$REMOTE_DIR/capture_one_resolution.py"
  RC=$?
  set -e
  echo "[REMOTE_RES] rc=$RC label=$LABEL"
  sync
  sleep 1
done

echo
echo "[REMOTE] done"
echo "REMOTE_RES_SWEEP_DIR=$REMOTE_DIR"
REMOTE

log "Remote output size before download"
ssh_cam "du -sh '$REMOTE_DIR' || true; find '$REMOTE_DIR' -type f | wc -l"

log "Downloading remote probe folder with progress"
LOCAL_RUN_DIR="$LOCAL_OUT/$(basename "$REMOTE_DIR")"
mkdir -p "$LOCAL_RUN_DIR"

if command -v rsync >/dev/null 2>&1; then
  rsync -avh --progress --partial \
    -e "ssh -o ConnectTimeout=$SSH_CONNECT_TIMEOUT -o ServerAliveInterval=15 -o ServerAliveCountMax=2" \
    "$REMOTE_USER@$HOST:$REMOTE_DIR/" \
    "$LOCAL_RUN_DIR/"
else
  echo "rsync not found; falling back to verbose scp"
  scp -v -r "$REMOTE_USER@$HOST:$REMOTE_DIR" "$LOCAL_OUT/"
fi

log "Building local CSV and cut sheets"
LOCAL_RUN_DIR_B64="$(b64 "$LOCAL_RUN_DIR")"
python3 - <<PY
import base64, csv, json, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

run_dir = Path(base64.b64decode("$LOCAL_RUN_DIR_B64").decode()).resolve()
cut_dir = run_dir / "cut_sheets"
cut_dir.mkdir(exist_ok=True)

rows = []
for p in sorted(run_dir.glob("*/result.json")):
    with p.open("r", encoding="utf-8") as f:
        rows.append(json.load(f))

# Sort by output pixel count descending.
rows.sort(key=lambda r: int(r.get("requested_output_width") or 0) * int(r.get("requested_output_height") or 0), reverse=True)

fieldnames = [
    "label", "ok", "requested_output_width", "requested_output_height",
    "actual_width", "actual_height", "image_size_kb",
    "requested_scaler_crop", "metadata_scaler_crop",
    "sensor_px_per_output_px_x", "sensor_px_per_output_px_y",
    "output_px_per_sensor_px_x", "output_px_per_sensor_px_y",
    "exposure_time", "analogue_gain", "digital_gain", "lens_position",
    "af_state", "focus_fom", "lux", "image_path", "metadata_path", "error"
]
summary_csv = run_dir / "summary.csv"
with summary_csv.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)

def font(size, bold=False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()

f_title = font(28, True)
f_head = font(17, True)
f_small = font(13)

def local_image_path(r):
    p = Path(r.get("image_path") or "")
    return run_dir / r.get("label", "") / p.name

# Fit-to-page contact sheet. This is intentionally downsampled for layout.
tile_w, img_h, label_h = 520, 292, 138
margin = 24
cols = 2
tile_h = img_h + label_h
sheet_w = margin * 2 + cols * tile_w
sheet_h = margin * 2 + 95 + max(1, math.ceil(len(rows) / cols)) * tile_h
sheet = Image.new("RGB", (sheet_w, sheet_h), (245,247,250))
d = ImageDraw.Draw(sheet)
d.text((margin, margin), "BM fixed crop · output resolution sweep", font=f_title, fill=(30,50,70))
d.text((margin, margin+38), f"{run_dir.name}", font=f_small, fill=(80,100,120))
d.text((margin, margin+58), "This overview sheet downscales thumbnails. Use the 1:1 sheet or open JPEGs for pixel quality.", font=f_small, fill=(120,70,50))

for i, r in enumerate(rows):
    x = margin + (i % cols) * tile_w
    y = margin + 95 + (i // cols) * tile_h
    d.rectangle((x, y, x+tile_w-8, y+tile_h-8), fill=(255,255,255), outline=(200,210,220))
    img_path = local_image_path(r)
    if r.get("ok") and img_path.exists():
        with Image.open(img_path) as im:
            im = im.convert("RGB")
            im.thumbnail((tile_w-20, img_h-12), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (tile_w-20, img_h-12), (230,235,240))
            canvas.paste(im, ((canvas.width-im.width)//2, (canvas.height-im.height)//2))
            sheet.paste(canvas, (x+10, y+6))
    else:
        d.text((x+20, y+40), f"FAILED\\n{r.get('error','')}", font=f_small, fill=(160,60,60))
    label_y = y + img_h
    lines = [
        f"{r.get('label')}   {r.get('actual_width')}×{r.get('actual_height')}",
        f"crop: {r.get('metadata_scaler_crop') or r.get('requested_scaler_crop')}",
        f"JPEG: {r.get('image_size_kb')} KB",
        f"sensor/output px: {r.get('sensor_px_per_output_px_x')}×",
        f"FocusFoM: {r.get('focus_fom')}   Lux: {r.get('lux')}",
    ]
    for j, line in enumerate(lines):
        d.text((x+12, label_y + 8 + j*22), line, font=f_small, fill=(40,60,80))

contact_sheet = cut_dir / "fixed_crop_resolution_contact_sheet_fit.jpg"
sheet.save(contact_sheet, quality=92)

# 1:1 center crop sheet. This is the more important sheet for pixel-density comparison.
# It takes a native-pixel center crop from each output image without rescaling it.
crop_w, crop_h = 720, 405
label_h2 = 92
tile_w2 = crop_w
tile_h2 = crop_h + label_h2
cols2 = 1
sheet_w2 = margin * 2 + tile_w2
sheet_h2 = margin * 2 + 105 + len(rows) * tile_h2
one = Image.new("RGB", (sheet_w2, sheet_h2), (245,247,250))
od = ImageDraw.Draw(one)
od.text((margin, margin), "BM fixed crop · 1:1 center crops", font=f_title, fill=(30,50,70))
od.text((margin, margin+38), "No scaling inside each tile. This is the sheet to inspect true pixel density/sharpness.", font=f_small, fill=(120,70,50))
od.text((margin, margin+58), f"Center crop window: {crop_w}×{crop_h} output pixels", font=f_small, fill=(80,100,120))

y = margin + 105
for r in rows:
    od.rectangle((margin, y, margin+tile_w2, y+tile_h2-8), fill=(255,255,255), outline=(200,210,220))
    img_path = local_image_path(r)
    if r.get("ok") and img_path.exists():
        with Image.open(img_path) as im:
            im = im.convert("RGB")
            iw, ih = im.size
            cw = min(crop_w, iw)
            ch = min(crop_h, ih)
            left = max(0, (iw - cw)//2)
            top = max(0, (ih - ch)//2)
            center = im.crop((left, top, left+cw, top+ch))
            canvas = Image.new("RGB", (crop_w, crop_h), (230,235,240))
            canvas.paste(center, ((crop_w-cw)//2, (crop_h-ch)//2))
            one.paste(canvas, (margin, y))
    else:
        od.text((margin+20, y+40), f"FAILED\\n{r.get('error','')}", font=f_small, fill=(160,60,60))

    ly = y + crop_h + 8
    lines = [
        f"{r.get('label')} · {r.get('actual_width')}×{r.get('actual_height')} · JPEG {r.get('image_size_kb')} KB",
        f"ScalerCrop {r.get('metadata_scaler_crop') or r.get('requested_scaler_crop')} · sensor/output px {r.get('sensor_px_per_output_px_x')}×",
        f"FocusFoM {r.get('focus_fom')} · Lux {r.get('lux')}",
    ]
    for j, line in enumerate(lines):
        od.text((margin+10, ly + j*22), line, font=f_small, fill=(40,60,80))
    y += tile_h2

one_to_one_sheet = cut_dir / "fixed_crop_resolution_center_1to1_sheet.jpg"
one.save(one_to_one_sheet, quality=94)

# Metadata table
row_h = 30
table_w = 1500
table_h = 90 + row_h * (len(rows) + 1)
table = Image.new("RGB", (table_w, table_h), (245,247,250))
td = ImageDraw.Draw(table)
td.text((24, 20), "BM fixed crop resolution sweep · metadata summary", font=f_title, fill=(30,50,70))
headers = ["label", "ok", "actual", "JPEG KB", "ScalerCrop", "sensor/out", "FocusFoM", "Lux", "error"]
xs = [24, 230, 300, 430, 540, 880, 1020, 1130, 1230]
y = 72
for x, h in zip(xs, headers):
    td.text((x, y), h, font=f_head, fill=(30,50,70))
y += row_h
for r in rows:
    vals = [
        str(r.get("label","")),
        str(r.get("ok","")),
        f"{r.get('actual_width')}×{r.get('actual_height')}",
        str(r.get("image_size_kb","")),
        str(r.get("metadata_scaler_crop") or r.get("requested_scaler_crop")),
        str(r.get("sensor_px_per_output_px_x","")),
        str(r.get("focus_fom","")),
        str(r.get("lux","")),
        str(r.get("error",""))[:42],
    ]
    for x, val in zip(xs, vals):
        td.text((x, y), val, font=f_small, fill=(40,60,80))
    y += row_h

metadata_table = cut_dir / "fixed_crop_resolution_metadata_table.jpg"
table.save(metadata_table, quality=92)

print(f"summary_csv={summary_csv}")
print(f"fit_contact_sheet={contact_sheet}")
print(f"one_to_one_sheet={one_to_one_sheet}")
print(f"metadata_table={metadata_table}")
PY

cat <<EOF

DONE
------------------------------------------------------------------------
LOCAL_RES_SWEEP_DIR=$LOCAL_RUN_DIR
SUMMARY_CSV=$LOCAL_RUN_DIR/summary.csv
FIT_CONTACT_SHEET=$LOCAL_RUN_DIR/cut_sheets/fixed_crop_resolution_contact_sheet_fit.jpg
ONE_TO_ONE_SHEET=$LOCAL_RUN_DIR/cut_sheets/fixed_crop_resolution_center_1to1_sheet.jpg
METADATA_TABLE=$LOCAL_RUN_DIR/cut_sheets/fixed_crop_resolution_metadata_table.jpg
------------------------------------------------------------------------
EOF

open "$LOCAL_RUN_DIR/cut_sheets" >/dev/null 2>&1 || true
