#!/usr/bin/env bash
set -Eeuo pipefail

# BM controlled 16:9 crop / ROI probe
# Runs from Mac. Captures one production-style Picamera2 JPEG per ScalerCrop,
# downloads the results, and builds contact sheets / crop overlays locally.
#
# Default design goal:
#   - Keep output at a high production-safe 16:9 size: 2304x1296 (1296p)
#   - Vary only ScalerCrop / ROI, all centered 16:9 crops
#   - Use the live production module: /home/pi/BM_Devel_Pi/process_image_v2.py context
#
# Env overrides:
#   HOST=bmcam001
#   REMOTE_APP=/home/pi/BM_Devel_Pi
#   OUTPUT_SIZE=2304x1296
#   SETTLE_SEC=2.0
#   RUN_TAG=controlled_crop_...
#   LOCAL_BASE=$HOME/Downloads/bm_controlled_crop_probe
#   CROP_SPECS='full_16x9:0:0:4608:2592 crop_75:576:324:3456:1944 crop_67_prod720:768:432:3072:1728 crop_58:960:540:2688:1512 crop_50:1152:648:2304:1296'

HOST="${HOST:-bmcam001}"
REMOTE_USER="${REMOTE_USER:-pi}"
REMOTE_APP="${REMOTE_APP:-/home/pi/BM_Devel_Pi}"
OUTPUT_SIZE="${OUTPUT_SIZE:-2304x1296}"
SETTLE_SEC="${SETTLE_SEC:-2.0}"
RUN_TAG="${RUN_TAG:-controlled_crop_$(date -u +%Y%m%dT%H%M%SZ)}"
LOCAL_BASE="${LOCAL_BASE:-$HOME/Downloads/bm_controlled_crop_probe}"
LOCAL_OUT="$LOCAL_BASE/$RUN_TAG"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"

# Centered 16:9 ScalerCrop candidates in native IMX708 coordinates.
# Format: label:x:y:w:h
CROP_SPECS="${CROP_SPECS:-full_16x9:0:0:4608:2592 crop_75:576:324:3456:1944 crop_67_prod720:768:432:3072:1728 crop_58:960:540:2688:1512 crop_50:1152:648:2304:1296}"

log() { printf '\n[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

ssh_cam() {
  ssh -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" \
      -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=2 \
      "$REMOTE_USER@$HOST" "$@"
}

b64() {
  printf '%s' "$1" | python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())'
}

mkdir -p "$LOCAL_OUT"

cat <<EOF
============================================================
BM CONTROLLED CROP ROI PROBE
============================================================
Host:          $HOST
Remote app:    $REMOTE_APP
Run tag:       $RUN_TAG
Local output:  $LOCAL_OUT
Output size:   $OUTPUT_SIZE
Settle sec:    $SETTLE_SEC
Crop specs:    $CROP_SPECS
Capture path:  Picamera2 create_still_configuration(main={size}) + ScalerCrop control
Purpose:       Hold high output size constant and vary centered 16:9 ROI
============================================================
EOF

OUT_W="${OUTPUT_SIZE%x*}"
OUT_H="${OUTPUT_SIZE#*x}"
CROP_SPECS_B64="$(b64 "$CROP_SPECS")"
RUN_TAG_B64="$(b64 "$RUN_TAG")"
OUTPUT_SIZE_B64="$(b64 "$OUTPUT_SIZE")"
SETTLE_SEC_B64="$(b64 "$SETTLE_SEC")"
REMOTE_APP_B64="$(b64 "$REMOTE_APP")"

log "Creating remote run directory and helper"
REMOTE_INFO="$(ssh_cam "RUN_TAG_B64='$RUN_TAG_B64' REMOTE_APP_B64='$REMOTE_APP_B64' bash -s" <<'REMOTE'
set -Eeuo pipefail
b64d() { printf '%s' "$1" | base64 -d; }
RUN_TAG="$(b64d "$RUN_TAG_B64")"
APP="$(b64d "$REMOTE_APP_B64")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME="$(hostname)"
OUT="$APP/controlled_crop_probe_${HOSTNAME}_${RUN_TAG}_${STAMP}"
mkdir -p "$OUT"
cat > "$OUT/capture_one_crop.py" <<'PY'
#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

from PIL import Image
from picamera2 import Picamera2

SENSOR_W = 4608
SENSOR_H = 2592


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return float(value)
    except Exception:
        return str(value)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--app-dir', required=True)
    p.add_argument('--out-dir', required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--crop', required=True, help='x,y,w,h in native sensor coordinates')
    p.add_argument('--output-size', required=True, help='WxH, e.g. 2304x1296')
    p.add_argument('--settle-sec', type=float, default=2.0)
    args = p.parse_args()

    app_dir = Path(args.app_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(app_dir)
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    # Import production module so the probe runs in the same module context/version.
    # We do not call capture_image() here because that function does not expose ScalerCrop.
    import process_image_v2 as prod  # noqa: F401

    out_w, out_h = [int(x) for x in args.output_size.lower().split('x', 1)]
    crop = tuple(int(v) for v in args.crop.split(','))
    if len(crop) != 4:
        raise ValueError('crop must be x,y,w,h')
    x, y, w, h = crop

    label_dir = out_dir / args.label
    label_dir.mkdir(parents=True, exist_ok=True)
    img_path = label_dir / f'{args.label}_{out_w}x{out_h}_crop_{x}_{y}_{w}_{h}.jpg'
    meta_path = Path(str(img_path) + '.capture_metadata.json')
    result_path = label_dir / 'result.json'

    result = {
        'label': args.label,
        'ok': False,
        'app_dir': str(app_dir),
        'output_size_requested': [out_w, out_h],
        'sensor_size_assumed': [SENSOR_W, SENSOR_H],
        'scaler_crop_requested': [x, y, w, h],
        'sensor_to_output_x': round(w / out_w, 6) if out_w else None,
        'sensor_to_output_y': round(h / out_h, 6) if out_h else None,
        'output_to_crop_fraction_x': round(out_w / w, 6) if w else None,
        'output_to_crop_fraction_y': round(out_h / h, 6) if h else None,
        'image_path': str(img_path),
        'metadata_path': str(meta_path),
    }

    picam2 = None
    try:
        picam2 = Picamera2()
        config = picam2.create_still_configuration(main={'size': (out_w, out_h)})
        result['camera_config'] = json_safe(config)
        picam2.configure(config)
        picam2.start()
        time.sleep(0.4)
        picam2.set_controls({'ScalerCrop': crop})
        time.sleep(args.settle_sec)
        metadata = picam2.capture_file(str(img_path))
        if not isinstance(metadata, dict):
            try:
                metadata = picam2.capture_metadata() or {}
            except Exception:
                metadata = {}
        metadata = json_safe(metadata or {})
        meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding='utf-8')

        with Image.open(img_path) as im:
            actual_w, actual_h = im.size
        result.update({
            'ok': True,
            'actual_size': [actual_w, actual_h],
            'image_size_bytes': img_path.stat().st_size,
            'image_size_kb': round(img_path.stat().st_size / 1024, 3),
            'scaler_crop_metadata': metadata.get('ScalerCrop'),
            'ExposureTime': metadata.get('ExposureTime'),
            'AnalogueGain': metadata.get('AnalogueGain'),
            'DigitalGain': metadata.get('DigitalGain'),
            'ColourGains': metadata.get('ColourGains'),
            'LensPosition': metadata.get('LensPosition'),
            'AfState': metadata.get('AfState'),
            'FocusFoM': metadata.get('FocusFoM'),
            'Lux': metadata.get('Lux'),
        })
        print(f"[REMOTE_CROP] ok=true label={args.label} actual={actual_w}x{actual_h} size_kb={result['image_size_kb']} ScalerCrop={result['scaler_crop_metadata']}", flush=True)
    except Exception as exc:
        result.update({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
            'traceback_tail': traceback.format_exc().splitlines()[-12:],
        })
        print(f"[REMOTE_CROP] ok=false label={args.label} error={result['error']}", flush=True)
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
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')

    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
PY
chmod +x "$OUT/capture_one_crop.py"
echo "REMOTE_CROP_PROBE_DIR=$OUT"
REMOTE
)"

echo "$REMOTE_INFO"
REMOTE_DIR="$(echo "$REMOTE_INFO" | awk -F= '/^REMOTE_CROP_PROBE_DIR=/{print $2}' | tail -1)"
if [[ -z "$REMOTE_DIR" ]]; then
  echo "ERROR: failed to parse remote output dir" >&2
  exit 1
fi

log "Running controlled crop captures on $HOST"
ssh_cam \
  "REMOTE_DIR='$REMOTE_DIR' REMOTE_APP_B64='$REMOTE_APP_B64' CROP_SPECS_B64='$CROP_SPECS_B64' OUTPUT_SIZE_B64='$OUTPUT_SIZE_B64' SETTLE_SEC_B64='$SETTLE_SEC_B64' bash -s" <<'REMOTE'
set -Eeuo pipefail
b64d() { printf '%s' "$1" | base64 -d; }
APP="$(b64d "$REMOTE_APP_B64")"
CROP_SPECS="$(b64d "$CROP_SPECS_B64")"
OUTPUT_SIZE="$(b64d "$OUTPUT_SIZE_B64")"
SETTLE_SEC="$(b64d "$SETTLE_SEC_B64")"

echo "[REMOTE] hostname=$(hostname)"
echo "[REMOTE] app=$APP"
echo "[REMOTE] out=$REMOTE_DIR"
echo "[REMOTE] output_size=$OUTPUT_SIZE"
echo "[REMOTE] date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[REMOTE] crop_specs=$CROP_SPECS"

for spec in $CROP_SPECS; do
  IFS=: read -r label x y w h <<EOF
$spec
EOF
  crop="$x,$y,$w,$h"
  echo
  echo "========================================================================"
  echo "[REMOTE_CROP] label=$label crop=$crop output=$OUTPUT_SIZE"
  set +e
  /usr/bin/python3 -u "$REMOTE_DIR/capture_one_crop.py" \
    --app-dir "$APP" \
    --out-dir "$REMOTE_DIR" \
    --label "$label" \
    --crop "$crop" \
    --output-size "$OUTPUT_SIZE" \
    --settle-sec "$SETTLE_SEC"
  rc=$?
  set -e
  echo "[REMOTE_CROP] rc=$rc label=$label"
  # Give libcamera/Picamera2 a moment to fully release the device.
  sleep 2
 done

echo
echo "[REMOTE] done"
echo "REMOTE_CROP_PROBE_DIR=$REMOTE_DIR"
REMOTE

log "Downloading remote probe folder"
mkdir -p "$LOCAL_OUT"
scp -q -r "$REMOTE_USER@$HOST:$REMOTE_DIR" "$LOCAL_OUT/"
LOCAL_RUN_DIR="$LOCAL_OUT/$(basename "$REMOTE_DIR")"

log "Building local CSV and cut sheets"
python3 - "$LOCAL_RUN_DIR" <<'PY'
import csv
import json
import math
import os
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception as exc:
    raise SystemExit(f"Missing Pillow. Install with: python3 -m pip install pillow\n{exc}")

run_dir = Path(sys.argv[1]).expanduser().resolve()
cut_dir = run_dir / 'cut_sheets'
cut_dir.mkdir(exist_ok=True)

results = []
for p in sorted(run_dir.glob('*/result.json')):
    try:
        r = json.loads(p.read_text())
    except Exception as exc:
        r = {'label': p.parent.name, 'ok': False, 'error': str(exc)}
    r['local_dir'] = str(p.parent)
    results.append(r)

# Preserve the configured order where possible by directory mtime/name.
order = {name: i for i, name in enumerate(['full_16x9','crop_75','crop_67_prod720','crop_58','crop_50'])}
results.sort(key=lambda r: order.get(r.get('label',''), 999))

fields = []
for r in results:
    for k in r.keys():
        if k not in fields and k not in ('camera_config', 'traceback_tail'):
            fields.append(k)
summary = run_dir / 'summary.csv'
with summary.open('w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader(); w.writerows(results)

# Fonts
def font(size, bold=False):
    candidates = [
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf' if bold else '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/Library/Fonts/Arial Bold.ttf' if bold else '/Library/Fonts/Arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for c in candidates:
        if c and Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()

f_title=font(24, True); f_head=font(16, True); f_small=font(12); f_body=font(14)

ok_results = [r for r in results if r.get('ok')]

# Contact sheet of captured images.
tile_w, img_h, label_h = 480, 270, 135
margin, gap = 24, 14
cols = 2
rows = max(1, math.ceil(len(results)/cols))
width = margin*2 + cols*tile_w + (cols-1)*gap
height = margin*2 + 80 + rows*(img_h+label_h) + (rows-1)*gap
sheet = Image.new('RGB', (width,height), (245,247,250))
d = ImageDraw.Draw(sheet)
d.text((margin, margin), 'Controlled 16:9 ScalerCrop ROI probe', font=f_title, fill=(30,45,60))
d.text((margin, margin+34), f'run={run_dir.name}', font=f_body, fill=(80,95,110))
for idx,r in enumerate(results):
    row, col = divmod(idx, cols)
    x = margin + col*(tile_w+gap)
    y = margin + 80 + row*(img_h+label_h+gap)
    d.rectangle((x,y,x+tile_w,y+img_h+label_h), fill=(255,255,255), outline=(205,215,225))
    if r.get('ok') and r.get('image_path'):
        img_name = Path(r['image_path']).name
        local_img = Path(r['local_dir']) / img_name
        if local_img.exists():
            try:
                with Image.open(local_img) as im:
                    im = im.convert('RGB')
                    im.thumbnail((tile_w-16,img_h-12), Image.Resampling.LANCZOS)
                    canvas = Image.new('RGB', (tile_w-16,img_h-12), (232,236,240))
                    canvas.paste(im, ((canvas.width-im.width)//2,(canvas.height-im.height)//2))
                    sheet.paste(canvas, (x+8,y+6))
            except Exception as exc:
                d.text((x+10,y+20), f'open failed: {exc}', font=f_small, fill=(160,60,60))
    else:
        d.text((x+10,y+20), 'CAPTURE FAILED', font=f_head, fill=(160,60,60))
    ly = y + img_h + 8
    crop = r.get('scaler_crop_requested')
    meta_crop = r.get('scaler_crop_metadata')
    lines = [
        f"{r.get('label','')}  out={r.get('actual_size') or r.get('output_size_requested')}",
        f"crop_req={crop}",
        f"crop_meta={meta_crop}",
        f"sensor/out={r.get('sensor_to_output_x')}  size={r.get('image_size_kb')} KB",
        f"FocusFoM={r.get('FocusFoM')}  Lux={r.get('Lux')}",
    ]
    if not r.get('ok'):
        lines.append(str(r.get('error',''))[:70])
    for i,line in enumerate(lines):
        d.text((x+10, ly+i*18), line, font=f_small, fill=(45,60,75))
contact = cut_dir / 'controlled_crop_contact_sheet.jpg'
sheet.save(contact, quality=92)

# Metadata table image.
table_w = 1550
row_h = 44
header_h = 80
height = header_h + max(1,len(results)+1)*row_h + 30
table = Image.new('RGB', (table_w,height), (245,247,250))
td=ImageDraw.Draw(table)
td.text((20,20), 'Controlled crop ROI metadata summary', font=f_title, fill=(30,45,60))
cols_info = [
    ('label', 20, 170), ('ok', 190, 60), ('actual', 255, 130), ('KB', 390, 80),
    ('ScalerCrop requested', 480, 300), ('ScalerCrop metadata', 790, 300),
    ('sensor/out', 1100, 110), ('FocusFoM', 1220, 100), ('Lux', 1330, 130),
]
y=header_h
for name,x,w in cols_info:
    td.text((x,y), name, font=f_head, fill=(30,45,60))
y += row_h
for r in results:
    fill = (255,255,255) if r.get('ok') else (255,235,235)
    td.rectangle((12,y-8,table_w-12,y+row_h-10), fill=fill, outline=(215,222,230))
    vals = {
        'label': r.get('label',''),
        'ok': str(r.get('ok')),
        'actual': str(r.get('actual_size','')),
        'KB': str(r.get('image_size_kb','')),
        'ScalerCrop requested': str(r.get('scaler_crop_requested','')),
        'ScalerCrop metadata': str(r.get('scaler_crop_metadata','')),
        'sensor/out': str(r.get('sensor_to_output_x','')),
        'FocusFoM': str(r.get('FocusFoM','')),
        'Lux': str(r.get('Lux',''))[:10],
    }
    for name,x,w in cols_info:
        td.text((x,y), vals[name][:42], font=f_small, fill=(35,50,65))
    y += row_h
metadata_table = cut_dir / 'controlled_crop_metadata_table.jpg'
table.save(metadata_table, quality=92)

# ScalerCrop overlay on the full_16x9 image if available; otherwise first ok image.
base = None
for r in ok_results:
    if r.get('label') == 'full_16x9':
        base = r; break
if base is None and ok_results:
    base = ok_results[0]

overlay_path = None
if base:
    base_img_path = Path(base['local_dir']) / Path(base['image_path']).name
    with Image.open(base_img_path) as im:
        bg = im.convert('RGB')
    od = ImageDraw.Draw(bg)
    actual = base.get('actual_size') or [bg.width, bg.height]
    sx = actual[0] / 4608.0
    sy = actual[1] / 2592.0
    palette = [(0,180,80),(0,120,255),(255,140,0),(180,80,255),(220,40,40),(40,160,160)]
    for i,r in enumerate(results):
        crop = r.get('scaler_crop_requested')
        if not crop or len(crop) != 4:
            continue
        x,y,w,h = crop
        box = (x*sx, y*sy, (x+w)*sx, (y+h)*sy)
        color = palette[i % len(palette)]
        od.rectangle(box, outline=color, width=5)
        label = f"{r.get('label')} {w}x{h}"
        od.rectangle((box[0]+4, box[1]+4, box[0]+4+len(label)*8, box[1]+28), fill=(255,255,255))
        od.text((box[0]+8, box[1]+8), label, font=f_small, fill=color)
    overlay_path = cut_dir / 'controlled_crop_scalercrop_overlay.jpg'
    bg.save(overlay_path, quality=92)

manifest = {
    'run_dir': str(run_dir),
    'summary_csv': str(summary),
    'contact_sheet': str(contact),
    'metadata_table': str(metadata_table),
    'scalercrop_overlay': str(overlay_path) if overlay_path else None,
    'result_count': len(results),
    'ok_count': len(ok_results),
}
(run_dir/'local_outputs_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('summary_csv=' + str(summary))
print('contact_sheet=' + str(contact))
print('metadata_table=' + str(metadata_table))
if overlay_path:
    print('scalercrop_overlay=' + str(overlay_path))
PY

cat <<EOF

DONE
------------------------------------------------------------------------
LOCAL_CROP_PROBE_DIR=$LOCAL_RUN_DIR
SUMMARY_CSV=$LOCAL_RUN_DIR/summary.csv
CONTACT_SHEET=$LOCAL_RUN_DIR/cut_sheets/controlled_crop_contact_sheet.jpg
METADATA_TABLE=$LOCAL_RUN_DIR/cut_sheets/controlled_crop_metadata_table.jpg
SCALERCROP_OVERLAY=$LOCAL_RUN_DIR/cut_sheets/controlled_crop_scalercrop_overlay.jpg
------------------------------------------------------------------------
EOF

open "$LOCAL_RUN_DIR/cut_sheets" >/dev/null 2>&1 || true
