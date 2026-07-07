#!/usr/bin/env bash
set -Eeuo pipefail

# One-shot production-path ROI probe for BM legacy cameras.
# Runs from Mac. Captures one image per production resolution key using the
# actual production Python function process_image_v2.capture_image(...), downloads
# results, and builds local cut sheets + metadata CSV.
# v4 fixes v3 child import path and local CSV error handling.

HOST="${HOST:-bmcam001}"
REMOTE_USER="${REMOTE_USER:-pi}"
REMOTE_APP="${REMOTE_APP:-/home/pi/BM_Devel_Pi}"
RUN_TAG="${RUN_TAG:-prod_roi_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_BASE="${OUT_BASE:-$HOME/Downloads/bm_prod_roi_probe}"
LOCAL_OUT="$OUT_BASE/$RUN_TAG"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"
CAPTURE_TIMEOUT_SEC="${CAPTURE_TIMEOUT_SEC:-120}"
SETTLE_BETWEEN_KEYS_SEC="${SETTLE_BETWEEN_KEYS_SEC:-4}"

# Default: all production keys. For faster focused debug:
#   HOST=bmcam001 KEYS="1296p 1080p 720p 480p 360p XGA SVGA VGA" ./run_bm_production_roi_probe_v4.sh
KEYS="${KEYS:-native_12mp 12MP 4k 2.7k 1296p 1080p 720p 480p 360p 4_3_full_crop 4_3_8mp 8MP 4_3_5mp 5MP 4_3_3mp 4_3_2mp 4_3_1080 XGA SVGA VGA}"

mkdir -p "$LOCAL_OUT"

log() { printf '\n[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
ssh_cam() {
  ssh -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" -o ServerAliveInterval=15 -o ServerAliveCountMax=2 "$REMOTE_USER@$HOST" "$@"
}

# Portable base64 helpers using Python so this works on macOS and Linux.
b64_encode() {
  python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())'
}

cat <<EOF
============================================================
BM PRODUCTION ROI PROBE v4
============================================================
Host:          $HOST
Remote app:    $REMOTE_APP
Run tag:       $RUN_TAG
Local output:  $LOCAL_OUT
Keys:          $KEYS
Capture path:  new Python process per key -> import process_image_v2; capture_image(...)
Why v4:        v3 key passing retained; child Python sys.path fixed; CSV extras fixed
============================================================
EOF

log "Creating remote run directory and helper"
REMOTE_DIR="$(ssh_cam "RUN_TAG='$RUN_TAG' REMOTE_APP='$REMOTE_APP' bash -s" <<'REMOTE'
set -Eeuo pipefail
APP="$REMOTE_APP"
HOSTNAME="$(hostname)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$APP/prod_roi_probe_${HOSTNAME}_${RUN_TAG}_${STAMP}"
mkdir -p "$OUT"
cat > "$OUT/prod_capture_one.py" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations
import json, os, sys, time, traceback
from pathlib import Path

try:
    from PIL import Image
except Exception:
    Image = None

key = sys.argv[1]
out_dir = Path(sys.argv[2]).resolve()
out_dir.mkdir(parents=True, exist_ok=True)

app_dir = Path(os.environ.get('REMOTE_APP', '/home/pi/BM_Devel_Pi')).resolve()
os.chdir(app_dir)
# When this helper is executed from the probe output directory, sys.path[0]
# is that output directory, not /home/pi/BM_Devel_Pi. Add the live runtime
# folder explicitly so import process_image_v2 uses the production module.
if str(app_dir) not in sys.path:
    sys.path.insert(0, str(app_dir))
result = {
    'key': key,
    'ok': False,
    'start_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'out_dir': str(out_dir),
    'app_dir': str(app_dir),
}


try:
    import process_image_v2 as prod
    result['production_module'] = str(Path(prod.__file__).resolve())
    result['requested_resolution'] = list(prod.RESOLUTIONS.get(key, (None, None)))
    if key not in prod.RESOLUTIONS:
        raise KeyError(f'Unknown production resolution key: {key}')

    before = {p.resolve() for p in out_dir.glob('*.jpg')}
    returned = prod.capture_image(resolution_key=key, directory_path=str(out_dir))
    after = [p for p in out_dir.glob('*.jpg') if p.resolve() not in before]
    if not after:
        # fallback: newest jpg in key folder
        after = sorted(out_dir.glob('*.jpg'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not after:
        raise RuntimeError('capture_image returned without creating a jpg')

    image_path = sorted(after, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    meta_path = Path(str(image_path) + '.capture_metadata.json')

    result['ok'] = True
    result['returned'] = str(returned)
    result['image_path'] = str(image_path)
    result['image_filename'] = image_path.name
    result['image_bytes'] = image_path.stat().st_size
    result['image_size_kb'] = round(image_path.stat().st_size / 1024, 3)

    if Image is not None:
        with Image.open(image_path) as im:
            result['actual_width'] = im.width
            result['actual_height'] = im.height

    if meta_path.exists():
        result['metadata_path'] = str(meta_path)
        try:
            metadata = json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            metadata = {}
        result['metadata_keys'] = sorted(metadata.keys())
        for field in [
            'ScalerCrop', 'SensorCrop', 'PixelArraySize', 'CameraSensorInfo',
            'ExposureTime', 'AnalogueGain', 'DigitalGain', 'ColourGains',
            'ColourTemperature', 'LensPosition', 'AfState', 'AfMode', 'FocusFoM',
            'Lux', 'FrameDuration', 'SensorTemperature'
        ]:
            if field in metadata:
                result[field] = metadata[field]
    else:
        result['metadata_path'] = ''

except Exception as exc:
    result['ok'] = False
    result['error_type'] = type(exc).__name__
    result['error'] = str(exc)
    result['traceback_tail'] = traceback.format_exc().splitlines()[-12:]

result['end_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
result_path = out_dir / 'result.json'
result_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding='utf-8')
print('RESULT_JSON=' + json.dumps(result, sort_keys=True, default=str), flush=True)
sys.exit(0 if result.get('ok') else 1)
PY
chmod +x "$OUT/prod_capture_one.py"
echo "$OUT"
REMOTE
)"

echo "REMOTE_PROD_ROI_DIR=$REMOTE_DIR"

log "Running production-path captures on $HOST"
# v4: Do NOT pipe keys into ssh while also using a heredoc; the heredoc becomes ssh stdin.
# Pass the key list via base64 environment variable instead.
KEYS_B64="$(printf '%s' "$KEYS" | b64_encode)"
ssh_cam "REMOTE_DIR='$REMOTE_DIR' REMOTE_APP='$REMOTE_APP' KEYS_B64='$KEYS_B64' CAPTURE_TIMEOUT_SEC='$CAPTURE_TIMEOUT_SEC' SETTLE_BETWEEN_KEYS_SEC='$SETTLE_BETWEEN_KEYS_SEC' bash -s" <<'REMOTE'
set -Eeuo pipefail
KEYS_FROM_ENV="$(printf '%s' "$KEYS_B64" | python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode())')"
echo "[REMOTE] hostname=$(hostname)"
echo "[REMOTE] out=$REMOTE_DIR"
echo "[REMOTE] date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[REMOTE] keys=$KEYS_FROM_ENV"
echo

if [ -z "${KEYS_FROM_ENV// }" ]; then
  echo "[REMOTE] ERROR: key list decoded empty; refusing to produce an empty probe" >&2
  exit 20
fi

for key in $KEYS_FROM_ENV; do
  echo "========================================================================"
  echo "[REMOTE_PROBE] key=$key"
  KEY_DIR="$REMOTE_DIR/$key"
  mkdir -p "$KEY_DIR"
  set +e
  timeout "$CAPTURE_TIMEOUT_SEC" /usr/bin/python3 -u "$REMOTE_DIR/prod_capture_one.py" "$key" "$KEY_DIR" \
    > "$KEY_DIR/capture_stdout.log" 2> "$KEY_DIR/capture_stderr.log"
  rc=$?
  set -e
  echo "[REMOTE_PROBE] rc=$rc"
  if [ -f "$KEY_DIR/result.json" ]; then
    python3 - <<PY
import json
from pathlib import Path
p=Path('$KEY_DIR/result.json')
d=json.loads(p.read_text())
print('[REMOTE_PROBE] ok=' + str(d.get('ok')).lower())
print('[REMOTE_PROBE] requested=' + str(d.get('requested_resolution')))
print('[REMOTE_PROBE] actual=' + str((d.get('actual_width'), d.get('actual_height'))))
print('[REMOTE_PROBE] size_kb=' + str(d.get('image_size_kb')))
print('[REMOTE_PROBE] ScalerCrop=' + str(d.get('ScalerCrop')))
if not d.get('ok'):
    print('[REMOTE_PROBE] error=' + str(d.get('error_type')) + ': ' + str(d.get('error')))
PY
  else
    echo "[REMOTE_PROBE] no result.json; stdout tail:"
    tail -20 "$KEY_DIR/capture_stdout.log" 2>/dev/null || true
    echo "[REMOTE_PROBE] stderr tail:"
    tail -20 "$KEY_DIR/capture_stderr.log" 2>/dev/null || true
  fi
  sleep "$SETTLE_BETWEEN_KEYS_SEC"
done

echo
find "$REMOTE_DIR" -maxdepth 2 -name result.json -print | sort > "$REMOTE_DIR/result_json_files.txt"
echo "[REMOTE] done"
echo "REMOTE_PROD_ROI_DIR=$REMOTE_DIR"
REMOTE

log "Downloading remote probe folder"
scp -q -r "$REMOTE_USER@$HOST:$REMOTE_DIR" "$LOCAL_OUT/"
DOWNLOADED_DIR="$LOCAL_OUT/$(basename "$REMOTE_DIR")"

log "Building local CSV and cut sheets"
python3 - "$DOWNLOADED_DIR" <<'PY'
from __future__ import annotations
import csv, json, math, re, sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception as exc:
    raise SystemExit('Missing Pillow. Install with: python3 -m pip install pillow\n' + str(exc))

root = Path(sys.argv[1]).resolve()
out = root / 'cut_sheets'
out.mkdir(exist_ok=True)

rows=[]
for rp in sorted(root.glob('*/result.json')):
    try:
        d=json.loads(rp.read_text())
    except Exception as exc:
        d={'key': rp.parent.name, 'ok': False, 'error': f'result json read failed: {exc}'}
    d['local_key_dir'] = str(rp.parent)
    # Convert image path to local path if present.
    if d.get('image_filename'):
        lp = rp.parent / d['image_filename']
        if lp.exists():
            d['local_image_path'] = str(lp)
    rows.append(d)

# Deterministic order: preserve requested production-ish order if present.
order = ['native_12mp','12MP','4k','2.7k','1296p','1080p','720p','480p','360p','4_3_full_crop','4_3_8mp','8MP','4_3_5mp','5MP','4_3_3mp','4_3_2mp','4_3_1080','XGA','SVGA','VGA']
idx={k:i for i,k in enumerate(order)}
rows.sort(key=lambda r: idx.get(r.get('key',''), 999))

# Write summary CSV.
fields=[]
preferred=['key','ok','requested_resolution','actual_width','actual_height','image_size_kb','image_bytes','ScalerCrop','SensorCrop','PixelArraySize','CameraSensorInfo','ExposureTime','AnalogueGain','DigitalGain','ColourGains','ColourTemperature','LensPosition','AfState','AfMode','FocusFoM','Lux','FrameDuration','SensorTemperature','error_type','error','local_image_path','metadata_path']
for f in preferred:
    if any(f in r for r in rows): fields.append(f)
for r in rows:
    for k in r.keys():
        if k not in fields and k not in ('metadata_keys','traceback_tail'):
            fields.append(k)
with (root/'summary.csv').open('w', newline='', encoding='utf-8') as f:
    w=csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader(); w.writerows(rows)

# Fonts.
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

f_title=font(26, True); f_head=font(16, True); f_small=font(13); f_tiny=font(11)

# Contact sheet.
success=[r for r in rows if r.get('ok') and r.get('local_image_path') and Path(r['local_image_path']).exists()]
fail=[r for r in rows if not r.get('ok')]
cols=3
thumb_w=520; thumb_h=300; label_h=105; margin=24; header_h=92
n=max(1,len(rows)); rows_n=math.ceil(n/cols)
sheet=Image.new('RGB',(margin*2+cols*thumb_w, margin*2+header_h+rows_n*(thumb_h+label_h)),(245,247,250))
d=ImageDraw.Draw(sheet)
d.text((margin,margin),'BM production ROI probe · visual contact sheet',font=f_title,fill=(25,40,55))
d.text((margin,margin+38),f'folder={root.name}   success={len(success)} failed={len(fail)}',font=f_small,fill=(80,95,110))
for i,r in enumerate(rows):
    col=i%cols; rr=i//cols
    x=margin+col*thumb_w; y=margin+header_h+rr*(thumb_h+label_h)
    d.rectangle((x,y,x+thumb_w-8,y+thumb_h+label_h-8),fill=(255,255,255),outline=(205,215,225))
    if r.get('local_image_path') and Path(r['local_image_path']).exists():
        with Image.open(r['local_image_path']) as im:
            im=im.convert('RGB')
            im.thumbnail((thumb_w-18,thumb_h-14), Image.Resampling.LANCZOS)
            canvas=Image.new('RGB',(thumb_w-18,thumb_h-14),(232,236,240))
            canvas.paste(im,((canvas.width-im.width)//2,(canvas.height-im.height)//2))
            sheet.paste(canvas,(x+9,y+7))
    else:
        d.text((x+20,y+50),'NO IMAGE / FAILED',font=f_head,fill=(170,60,60))
    ly=y+thumb_h+6
    req=r.get('requested_resolution')
    actual=f"{r.get('actual_width','?')}×{r.get('actual_height','?')}" if r.get('actual_width') else '—'
    lines=[
        f"{r.get('key')}  ok={r.get('ok')}",
        f"requested={req}  actual={actual}  size={r.get('image_size_kb','—')} KB",
        f"ScalerCrop={r.get('ScalerCrop','—')}",
    ]
    if not r.get('ok'):
        lines.append(f"error={str(r.get('error',''))[:80]}")
    for j,line in enumerate(lines):
        d.text((x+12,ly+j*18),line,font=f_tiny if j>0 else f_small,fill=(45,60,75) if r.get('ok') else (150,50,50))
sheet.save(out/'production_roi_contact_sheet.jpg',quality=92)

# Metadata table image.
row_h=34; width=1900; height=90+row_h*(len(rows)+1)
tab=Image.new('RGB',(width,height),(245,247,250)); td=ImageDraw.Draw(tab)
td.text((24,20),'BM production ROI probe · metadata summary',font=f_title,fill=(25,40,55))
headers=['key','ok','requested','actual','KB','ScalerCrop','FocusFoM','Lux','error']
xs=[24,190,245,395,520,610,1030,1130,1230]
y=72
for h,x in zip(headers,xs): td.text((x,y),h,font=f_head,fill=(30,45,60))
y+=30
for r in rows:
    fill=(255,255,255) if r.get('ok') else (255,235,235)
    td.rectangle((18,y-4,width-18,y+row_h-6),fill=fill,outline=(220,226,232))
    vals=[
        r.get('key',''), str(r.get('ok','')),
        str(r.get('requested_resolution','')),
        f"{r.get('actual_width','')}×{r.get('actual_height','')}" if r.get('actual_width') else '',
        str(r.get('image_size_kb','')),
        str(r.get('ScalerCrop',''))[:60],
        str(r.get('FocusFoM','')),
        str(r.get('Lux',''))[:9],
        (str(r.get('error_type',''))+': '+str(r.get('error','')))[:80] if not r.get('ok') else '',
    ]
    for val,x in zip(vals,xs): td.text((x,y),val,font=f_tiny,fill=(35,50,65))
    y+=row_h
tab.save(out/'production_roi_metadata_table.jpg',quality=92)

# ScalerCrop overlay on best available full-frame-ish background.
def parse_crop(v):
    if v is None or v=='': return None
    if isinstance(v, (list,tuple)) and len(v)==4:
        return tuple(float(x) for x in v)
    s=str(v)
    nums=[float(x) for x in re.findall(r'-?\d+(?:\.\d+)?',s)]
    # Supports [x,y,w,h] or (x,y)/WxH strings.
    if len(nums)>=4:
        return tuple(nums[:4])
    return None

base=None
for key in ['native_12mp','12MP','1296p','1080p','720p','480p','360p']:
    cand=[r for r in success if r.get('key')==key and r.get('local_image_path')]
    if cand:
        base=cand[0]; break
if base:
    with Image.open(base['local_image_path']) as im:
        bg=im.convert('RGB')
    # Downscale overlay for readability if huge.
    max_w=1800
    scale_disp=min(max_w/bg.width, 1.0)
    if scale_disp<1:
        bg=bg.resize((int(bg.width*scale_disp), int(bg.height*scale_disp)), Image.Resampling.LANCZOS)
    od=ImageDraw.Draw(bg)
    od.text((20,20),f"ScalerCrop overlay on {base.get('key')} background",font=f_head,fill=(255,255,255),stroke_width=2,stroke_fill=(0,0,0))
    colors=[(255,0,0),(0,180,0),(0,110,255),(255,140,0),(180,0,220),(0,180,180),(220,80,0)]
    seen={}
    legend=[]
    for r in success:
        c=parse_crop(r.get('ScalerCrop'))
        if not c: continue
        x,y,w,h=c
        crop_key=(round(x),round(y),round(w),round(h))
        seen.setdefault(crop_key,[]).append(r.get('key'))
    for i,(crop,keys) in enumerate(seen.items()):
        x,y,w,h=crop
        color=colors[i%len(colors)]
        # Sensor coordinate system for IMX708 native full.
        sx=bg.width/4608.0; sy=bg.height/2592.0
        rect=(x*sx,y*sy,(x+w)*sx,(y+h)*sy)
        od.rectangle(rect,outline=color,width=4)
        label=', '.join(keys)
        od.text((rect[0]+8, rect[1]+8+i*22), label, font=f_small, fill=color, stroke_width=2, stroke_fill=(0,0,0))
        legend.append((color, label, crop))
    bg.save(out/'production_roi_scalercrop_overlay.jpg',quality=92)

print('summary_csv=' + str(root/'summary.csv'))
print('contact_sheet=' + str(out/'production_roi_contact_sheet.jpg'))
print('metadata_table=' + str(out/'production_roi_metadata_table.jpg'))
print('scalercrop_overlay=' + str(out/'production_roi_scalercrop_overlay.jpg'))
PY

cat <<EOF

DONE
------------------------------------------------------------------------
LOCAL_PROD_ROI_DIR=$DOWNLOADED_DIR
SUMMARY_CSV=$DOWNLOADED_DIR/summary.csv
CONTACT_SHEET=$DOWNLOADED_DIR/cut_sheets/production_roi_contact_sheet.jpg
METADATA_TABLE=$DOWNLOADED_DIR/cut_sheets/production_roi_metadata_table.jpg
SCALERCROP_OVERLAY=$DOWNLOADED_DIR/cut_sheets/production_roi_scalercrop_overlay.jpg
------------------------------------------------------------------------
EOF

open "$DOWNLOADED_DIR/cut_sheets" >/dev/null 2>&1 || true
