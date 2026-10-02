#!/bin/bash
# hil_p0_probe.sh — Sprint27 P0: measure the rpicam / libcamera limits of the image-processing
# controls ON a unit (LADDER.md PART 1). Read-only on the unit: sends no command, changes no
# config; writes only /tmp/p0_<ts>/ on the Pi (clips deleted after their size is recorded).
#
# Purpose:  the Sprint27 session encodes the 7 camera.image_processing.* ranges/enums from these
#           files (Nick Q2 2026-10-01: real limits from rpicam on the unit, never from memory).
# Inputs:   $1 host (e.g. bmcam003); $2 local output dir (e.g. runs/s27_ladder_20261002/p0_rpicam_limits)
#           env HIL_UNIT_USER (pi); P0_DENOISE / P0_HDR (space-separated value lists, override
#           the defaults below); P0_FLOATS_EXTRA (extra values to try on every float control).
# Preconditions (the script REFUSES otherwise): no camera process on the unit (stop the runtime
#           and disarm cron first: hil/procedures/P0_rpicam_probe.md).
# Outputs (in $2):
#   00_env.txt            versions, CMA/mem, uname, camera list
#   01_help_<app>.txt     full --help of rpicam-still / rpicam-vid (+ grep of the 6 options)
#   02_picamera2_controls.json   Picamera2 camera_controls (min, max, default) for every control
#   probes.csv            id, app, args, exit_code, out_bytes, elapsed_s, last_line
#   probe_logs/<id>.txt   full stdout+stderr of each probe
#   still_meta/<id>.json  rpicam-still --metadata output (best effort) for the still probes
#   SUMMARY.md            per control: values tried, which exited 0, which failed
# Example:  hil/tools/hil_p0_probe.sh bmcam003 runs/s27_ladder_20261002/p0_rpicam_limits
# Limits:   an exit code of 0 does NOT prove the value was applied (libcamera may clamp
#           silently); the still metadata and Picamera2 ranges are the cross-check. ~10 min.
#           Default enum lists are ASSUMPTIONS taken from rpicam-apps docs; the --help text in
#           01_help_*.txt is the authority — re-run with P0_DENOISE/P0_HDR if it differs.
set -u
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
H="${1:?host}"; OUT="${2:?local output dir}"; U="${HIL_UNIT_USER:-pi}"
hil_require_host "$H"
mkdir -p "$OUT"
DENOISE="${P0_DENOISE:-auto off cdn_off cdn_fast cdn_hq bogus}"
HDR="${P0_HDR:-off auto sensor single-exp bogus}"
EXTRA="${P0_FLOATS_EXTRA:-}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 $U@$H"

echo "[p0] $(date -u +%FT%TZ) host=$H out=$OUT"
BUSY=$($SSH "pgrep -af '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle|[r]picam-|[l]ibcamera|[f]fmpeg'" < /dev/null)
if [ -n "$BUSY" ]; then
  echo "[p0] REFUSING: camera/runtime processes on $H:"; echo "$BUSY"; exit 2
fi

TS=$(date -u +%Y%m%dT%H%M%SZ)
$SSH "TS=$TS DENOISE='$DENOISE' HDR='$HDR' EXTRA='$EXTRA' bash -s" 2>&1 <<'REMOTE' | sed 's/^/[p0][pi] /'
set -u
# this script arrives on stdin (bash -s): every command that could read stdin gets </dev/null
D=/tmp/p0_$TS; mkdir -p $D/probe_logs $D/still_meta; cd $D
{ date -u +%FT%TZ; hostname; uname -a; rpicam-still --version 2>&1; rpicam-vid --version 2>&1
  grep -E "MemTotal|MemAvailable|CmaTotal|CmaFree" /proc/meminfo; rpicam-hello --list-cameras 2>&1 < /dev/null | head -20; } > 00_env.txt 2>&1
for app in rpicam-still rpicam-vid; do
  { $app --help 2>&1; echo; echo "===== grep"; $app --help 2>&1 | grep -A3 -E -- '--(sharpness|contrast|saturation|brightness|denoise|hdr)'; } > 01_help_$app.txt
done
python3 - > 02_picamera2_controls.json 2> 02_picamera2_controls.err <<'PY'
import json
from picamera2 import Picamera2
c = Picamera2()
out = {}
for k, v in c.camera_controls.items():
    try:
        out[k] = [x if isinstance(x, (int, float, bool, str, type(None))) else str(x) for x in v]
    except TypeError:
        out[k] = str(v)
c.close()
print(json.dumps(out, indent=1, sort_keys=True))
PY
echo "picamera2 rc=$? $(wc -c < 02_picamera2_controls.json) B"

# float ranges: from Picamera2 (min, max) when available, else the probe still runs on fixed values
vals() {  # $1 = libcamera control name, $2 = fallback list
  python3 -c "
import json,sys
try:
    c = json.load(open('02_picamera2_controls.json'))['$1']; lo, hi = float(c[0]), float(c[1])
    span = hi - lo; s = sorted({lo, hi, lo - 0.1*span - 0.01, hi + 0.1*span + 0.01, (lo+hi)/2, 0.0, 1.0})
    print(' '.join(('%g' % v) for v in s))
except Exception:
    print('$2')
"
}
echo "id,app,args,exit_code,out_bytes,elapsed_s,last_line" > probes.csv
n=0
probe() {  # $1 app, rest = args
  local app=$1; shift; n=$((n+1)); local id=$(printf 'p%03d' $n)
  local o; [ $app = rpicam-still ] && o=/tmp/p0_out.jpg || o=/tmp/p0_out.h264
  rm -f $o
  local t0=$(date +%s.%N)
  if [ $app = rpicam-still ]; then
    timeout 60 $app -n -t 1000 -o $o --metadata still_meta/$id.json --metadata-format json "$@" > probe_logs/$id.txt 2>&1 < /dev/null
  else
    timeout 60 $app -t 2000 -n -o $o "$@" > probe_logs/$id.txt 2>&1 < /dev/null
  fi
  local rc=$?
  local el=$(python3 -c "import time;print(round(time.time()-$t0,1))")
  local sz=$( [ -f $o ] && stat -c %s $o || echo 0)
  local last=$(grep -v '^\s*$' probe_logs/$id.txt | tail -1 | tr ',"' ';'"'" | cut -c1-160)
  echo "$id,$app,\"$*\",$rc,$sz,$el,\"$last\"" >> probes.csv
  echo "$id rc=$rc bytes=$sz ${el}s $app $*"
  rm -f $o
}
VID="--mode 2304:1296:10:P --width 1280 --height 720"
probe rpicam-vid $VID                                   # baseline
probe rpicam-vid $VID --denoise cdn_off --denoise cdn_fast
probe rpicam-vid $VID --sharpness 1.0 --sharpness 2.0
for pair in "sharpness Sharpness" "contrast Contrast" "saturation Saturation" "brightness Brightness"; do
  set -- $pair
  for v in $(vals $2 "-1 0 0.5 1 2 8 16 32 33") $EXTRA; do probe rpicam-vid $VID --$1 $v; done
done
for v in $DENOISE; do probe rpicam-vid $VID --denoise $v; done
for v in $HDR; do
  probe rpicam-vid --mode 2304:1296:10:P --width 1000 --height 562 --hdr $v
  probe rpicam-vid --mode 4608:2592:10:P --width 1000 --height 562 --hdr $v
done
probe rpicam-still                                      # baseline still
for v in $HDR; do probe rpicam-still --hdr $v; done
for v in $DENOISE; do probe rpicam-still --denoise $v; done
for pair in "sharpness Sharpness" "contrast Contrast" "saturation Saturation" "brightness Brightness"; do
  set -- $pair
  for v in $(vals $2 "-1 0 1 16 33"); do probe rpicam-still --$1 $v; done
done
echo "probes=$n done $(date -u +%FT%TZ)"
grep -E "CmaFree" /proc/meminfo
REMOTE
RC=${PIPESTATUS[0]}
echo "[p0] remote rc=$RC; fetching /tmp/p0_$TS"
scp -q -r -o BatchMode=yes "$U@$H:/tmp/p0_$TS/." "$OUT/" || { echo "[p0] FETCH FAILED: files stay on $H:/tmp/p0_$TS (cleared on reboot)"; exit 1; }

# SUMMARY.md: per control, which values exited 0
python3 - "$OUT" <<'PY'
import csv, os, re, sys, collections
out = sys.argv[1]
rows = list(csv.DictReader(open(os.path.join(out, "probes.csv"))))
by = collections.defaultdict(list)
for r in rows:
    m = re.findall(r"--(sharpness|contrast|saturation|brightness|denoise|hdr) (\S+)", r["args"])
    mode = re.search(r"--mode (\S+)", r["args"])
    if not m:
        by[(r["app"], "baseline")].append((r["args"], r["exit_code"], r["out_bytes"], r["last_line"]))
    elif len(m) > 1:
        by[(r["app"], "duplicate --" + m[0][0])].append((r["args"], r["exit_code"], r["out_bytes"], r["last_line"]))
    else:
        key = m[0][0] + (" @" + mode.group(1) if mode and m[0][0] == "hdr" else "")
        by[(r["app"], key)].append((m[0][1], r["exit_code"], r["out_bytes"], r["last_line"]))
lines = ["# P0 rpicam limits — summary", "",
         f"Source: `probes.csv` ({len(rows)} probes), logs in `probe_logs/`. Exit 0 with output bytes > 0 = ran;",
         "it does NOT prove the value was applied (libcamera may clamp): cross-check `02_picamera2_controls.json`",
         "and `still_meta/*.json`.", "",
         "| app | control | ran (exit 0, bytes > 0) | failed (exit / last line) |", "|---|---|---|---|"]
for (app, key), vs in sorted(by.items()):
    ok = [v for v, rc, b, _ in vs if rc == "0" and int(b or 0) > 0]
    bad = [f"{v} (rc {rc}: {ll[:60]})" for v, rc, b, ll in vs if not (rc == "0" and int(b or 0) > 0)]
    lines.append(f"| {app} | {key} | {' '.join(ok) or '—'} | {'; '.join(bad) or '—'} |")
open(os.path.join(out, "SUMMARY.md"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
PY
N=$(($(wc -l < "$OUT/probes.csv") - 1))
echo "[p0] $N probes recorded in $OUT/probes.csv; summary $OUT/SUMMARY.md"
[ "$N" -gt 0 ] || { echo "[p0] FAIL: no probes recorded"; exit 1; }
$SSH "rm -rf /tmp/p0_$TS" < /dev/null
exit 0
