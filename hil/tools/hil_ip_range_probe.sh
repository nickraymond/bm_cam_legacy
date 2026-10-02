#!/bin/bash
# hil_ip_range_probe.sh — find the USABLE range of camera.image_processing.{contrast,saturation,
# brightness} (F-G3-5): stills via the runtime's own rpicam-still argv, one key at a time, the others
# at default; mean luma, luma sd and mean saturation per still; a contact sheet.
#
# Purpose:  Sprint27 narrows these ranges so blanking values are refused (Nick, F-G3-5); the limits
#           must come from measured stills in daylight, not from memory.
# Inputs:   $1 host (bench rig, guarded), $2 local output dir
#           env P_CONTRAST / P_SATURATION / P_BRIGHTNESS (space-separated values; defaults below)
# Preconditions (REFUSES otherwise): no camera/runtime process on the unit (pause the runtime and
#           disarm cron first, P0 pattern in hil/procedures/TAKEOVER.md §2/§6). Daylight scene.
# Outputs (in $2): stats.csv (key, value, mean_luma, sd_luma, mean_sat, bytes, rc), thumbs/*.jpg
#           (400 px wide), base_argv.txt (the runtime's last native capture argv with every camera-control
#           flag stripped: AE/AWB/AF at rpicam defaults),
#           contact_sheet.jpg, SUMMARY.md (per key: last value before blank; blank = sd < 2 and
#           mean < 10 or > 245).
# Example:  hil/tools/hil_ip_range_probe.sh bmcam003 runs/s27_ip_range_probe_20261002
# Limits:   uses full-res native stills (4608x2592, ~5 s each, ~4 min total); auto exposure means
#           the AE loop partly compensates brightness/contrast: that is the production behaviour.
#           Stills only (video applies the same libcamera controls, not re-measured here).
set -u
H="${1:?host}"; OUT="${2:?local output dir}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_host "$H"
C="${P_CONTRAST:-0.25 0.5 0.75 1 1.5 2 3 4 6 8}"
S="${P_SATURATION:-0.25 0.5 1 2 3 4 6 8 12 16}"
B="${P_BRIGHTNESS:--0.75 -0.5 -0.25 -0.1 0 0.1 0.25 0.5 0.75}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 pi@$H"
mkdir -p "$OUT/thumbs"
BUSY=$($SSH "pgrep -af '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle|[r]picam-|[f]fmpe[g]'" < /dev/null)
[ -n "$BUSY" ] && { echo "[probe] REFUSING: runtime/camera busy on $H:"; echo "$BUSY"; exit 2; }
TS=$(date -u +%Y%m%dT%H%M%SZ)
$SSH "C='$C' S='$S' B='$B' TS=$TS bash -s" 2>&1 <<'REMOTE' | sed 's/^/[probe][pi] /'
set -u
D=/tmp/ipprobe_$TS; mkdir -p $D/thumbs; cd $D
# base argv = the runtime's last native still capture (size, quality, timeout) minus output/metadata AND every
# camera-control flag: auto exposure / AWB / AF defaults, so each probe changes exactly one image-processing key
L=$(grep -h "Running native capture command" /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | tail -1)
BASE=$(echo "$L" | sed 's/.*attempt [0-9]*\/[0-9]*: //' | python3 -c "
import shlex,sys
a=shlex.split(sys.stdin.read()); out=[]; skip={'-o','--output','--metadata','--metadata-format','--contrast','--saturation','--brightness','--sharpness','--denoise','--hdr','--shutter','--gain','--ev','--awb','--awbgains','--autofocus-mode','--lens-position','--metering','--exposure'}
i=1
while i < len(a):
    if a[i] in skip: i+=2; continue
    out.append(a[i]); i+=1
print(' '.join(shlex.quote(x) for x in out))")
echo "$BASE" > base_argv.txt; echo "base: rpicam-still $BASE"
echo "key,value,mean_luma,sd_luma,mean_sat,bytes,rc" > stats.csv
shot() {  # $1 key, $2 value
  local f=$D/$1_$2.jpg; local extra=""; [ "$1" != base ] && extra="--$1 $2"
  eval timeout 60 rpicam-still $BASE $extra -o $f > /dev/null 2>&1 < /dev/null; local rc=$?
  python3 - "$f" "$1" "$2" "$rc" >> stats.csv <<'PY'
import sys, os
from PIL import Image, ImageStat
f, k, v, rc = sys.argv[1:]
try:
    im = Image.open(f); im.thumbnail((400, 400)); im.save(os.path.join(os.path.dirname(f), "thumbs", os.path.basename(f)))
    L = ImageStat.Stat(im.convert("L")); Sa = ImageStat.Stat(im.convert("HSV"))
    print(f"{k},{v},{L.mean[0]:.1f},{L.stddev[0]:.1f},{Sa.mean[1]:.1f},{os.path.getsize(f)},{rc}")
except Exception as e:
    print(f"{k},{v},,,,0,{rc}")
PY
  rm -f $f; tail -1 stats.csv
}
shot base 1
for v in $C; do shot contrast $v; done
for v in $S; do shot saturation $v; done
for v in $B; do shot brightness $v; done
shot base 2
REMOTE
scp -q -r "pi@$H:/tmp/ipprobe_$TS/." "$OUT/" || { echo "[probe] FETCH FAILED (files on $H:/tmp/ipprobe_$TS)"; exit 1; }
$SSH "rm -rf /tmp/ipprobe_$TS" < /dev/null
python3 - "$OUT" <<'PY'
import csv, os, sys
out = sys.argv[1]
rows = list(csv.DictReader(open(os.path.join(out, "stats.csv"))))
def blank(r):
    try: m, sd = float(r["mean_luma"]), float(r["sd_luma"])
    except ValueError: return True
    return sd < 2 and (m < 10 or m > 245)
lines = ["# image_processing usable-range probe", "", "blank = sd_luma < 2 and (mean < 10 or > 245). "
         "Usable range = the contiguous run of non-blank values around the default.", "",
         "| key | values (mean/sd/sat) | usable min | usable max |", "|---|---|---|---|"]
default = {"contrast": 1.0, "saturation": 1.0, "brightness": 0.0}
for k in ("contrast", "saturation", "brightness"):
    rs = sorted([r for r in rows if r["key"] == k], key=lambda r: float(r["value"]))
    vals = [float(r["value"]) for r in rs]
    ok = [not blank(r) for r in rs]
    # walk outward from the value closest to the default
    i0 = min(range(len(vals)), key=lambda i: abs(vals[i] - default[k])) if vals else None
    lo = hi = None
    if i0 is not None and ok[i0]:
        lo = hi = i0
        while lo - 1 >= 0 and ok[lo - 1]: lo -= 1
        while hi + 1 < len(vals) and ok[hi + 1]: hi += 1
    cells = " ".join(f"{r['value']}:{r['mean_luma']}/{r['sd_luma']}/{r['mean_sat']}{'(BLANK)' if blank(r) else ''}" for r in rs)
    lines.append(f"| {k} | {cells} | {vals[lo] if lo is not None else '?'} | {vals[hi] if hi is not None else '?'} |")
lines.append(""); lines.append("Baselines: " + "; ".join(f"{r['mean_luma']}/{r['sd_luma']}/{r['mean_sat']}" for r in rows if r["key"] == "base"))
open(os.path.join(out, "SUMMARY.md"), "w").write("\n".join(lines) + "\n"); print("\n".join(lines))
try:
    from PIL import Image, ImageDraw
    th = [r for r in rows if os.path.exists(os.path.join(out, "thumbs", f"{r['key']}_{r['value']}.jpg"))]
    W, Hh, cols = 400, 225, 6
    sheet = Image.new("RGB", (W * cols, (Hh + 18) * ((len(th) + cols - 1) // cols)), "white")
    for i, r in enumerate(th):
        im = Image.open(os.path.join(out, "thumbs", f"{r['key']}_{r['value']}.jpg")).resize((W, Hh))
        x, y = (i % cols) * W, (i // cols) * (Hh + 18)
        sheet.paste(im, (x, y + 18))
        ImageDraw.Draw(sheet).text((x + 4, y + 2), f"{r['key']} {r['value']}  mean {r['mean_luma']} sd {r['sd_luma']} sat {r['mean_sat']}", fill="black")
    sheet.save(os.path.join(out, "contact_sheet.jpg"), quality=80)
    print(f"[probe] contact sheet: {out}/contact_sheet.jpg (thumbnails scaled to 400x225, NOT 1:1)")
except Exception as e:
    print(f"[probe] no contact sheet ({e})")
PY
