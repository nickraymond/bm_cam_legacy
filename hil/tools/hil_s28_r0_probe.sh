#!/bin/bash
# hil_s28_r0_probe.sh — Sprint28 R0 (sprints/Sprint28_raw_jxl/LADDER.md): can THIS unit capture
# --raw at its CMA and encode the nrjxl planes in time and memory? Read-only on the unit's
# runtime: sends no command, changes no config, never touches /boot or cron (the operator stops
# the runtime and disarms cron FIRST, LADDER R0 steps 1-3); writes only /tmp/s28r0_<ts>/ on the
# Pi and deletes it at the end (unless S28R0_KEEP=1).
#
# Purpose:  R0.1 no failed CMA allocation during --raw captures (/sys/kernel/mm/cma/*/
#           alloc_pages_fail snapshots per phase; CmaFree every 0.1 s is logged as info),
#           R0.2 capture time with vs without --raw, R0.3 encode time / peak RSS per crop preset
#           (1600x900 FIRST: it gates the feature), R0.4 tools present (cjxl, numpy, SD free).
# Inputs:   $1 host (bmcam003 | bmcam004), $2 local output dir (the run folder, e.g.
#           runs/s28_ladder_20261005); env S28R0_N (captures per mode, default 10), S28R0_ENC_N
#           (encodes per preset, default 10), S28R0_PRESETS (default "1600x900 2000x1124
#           2400x1350"), S28R0_KEEP=1 (keep /tmp/s28r0_* on the Pi).
#           The encoder is the branch's BM_Devel_Pi/rc_raw_jxl.py (+ config_registry.py,
#           config_validate.py), copied to the Pi's /tmp dir by scp: NOT a deploy.
# Preconditions (the script REFUSES otherwise): no camera / runtime process on the unit.
# Outputs (in $2): pulled/<host>_r0/{env.txt, captures.csv, cma_samples.csv, encodes.csv,
#           dmesg_tail.txt, enc_<preset>_<i>/result.json, one capture metadata JSON};
#           then run hil/tools/hil_s28_r0_analyze.py to write analysis/r0_*.csv + verdicts.
# Example:  hil/tools/hil_s28_r0_probe.sh bmcam003 runs/s28_ladder_20261005
# Limits:   ~25 min per unit (20 captures + 30 encodes + rests). The capture command is the
#           production one (rc_capture.native_capture_command) WITHOUT camera controls; the
#           rest of the cycle (prep, ladder, transmit) is not run. Peak RSS is wait4 ru_maxrss
#           of the cjxl child (the largest of the 4 planes).
set -u
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
H="${1:?host}"; OUT="${2:?local run dir}"; U="${HIL_UNIT_USER:-pi}"
hil_require_host "$H"
REPO_ROOT="$(dirname "$HIL_DIR")"
N="${S28R0_N:-10}"; ENC_N="${S28R0_ENC_N:-10}"
PRESETS="${S28R0_PRESETS:-1600x900 2000x1124 2400x1350}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 $U@$H"
TS=$(date -u +%Y%m%dT%H%M%SZ); D=/tmp/s28r0_$TS; P="$OUT/pulled/${H}_r0"
mkdir -p "$P" "$OUT/analysis"
log() { echo "$(date -u +%FT%TZ) [$H] $*" | tee -a "$OUT/gate.log"; }

log "R0 probe start: N=$N ENC_N=$ENC_N presets=$PRESETS remote=$D"
BUSY=$($SSH "pgrep -af '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle|[r]picam-|[l]ibcamera|[c]jxl'" < /dev/null)
if [ -n "$BUSY" ]; then
  log "REFUSING: camera/runtime processes on $H (stop the runtime + disarm cron first):"
  echo "$BUSY"; exit 2
fi
$SSH "mkdir -p $D" < /dev/null || { log "ssh mkdir failed"; exit 1; }
scp -q -o BatchMode=yes "$REPO_ROOT/BM_Devel_Pi/rc_raw_jxl.py" "$REPO_ROOT/BM_Devel_Pi/config_registry.py" \
    "$REPO_ROOT/BM_Devel_Pi/config_validate.py" "$U@$H:$D/" || { log "scp of the encoder failed"; exit 1; }
log "encoder copied: $(cd "$REPO_ROOT" && git rev-parse --short HEAD) rc_raw_jxl.py sha256=$(shasum -a 256 "$REPO_ROOT/BM_Devel_Pi/rc_raw_jxl.py" | cut -c1-16)"

$SSH "D=$D N=$N ENC_N=$ENC_N PRESETS='$PRESETS' bash -s" 2>&1 <<'REMOTE' | sed 's/^/[r0][pi] /' | tee -a "$OUT/gate.log"
set -u
# this script arrives on stdin (bash -s): every command that could read stdin gets </dev/null
cd "$D"
{ date -u +%FT%TZ; hostname; uname -a; cat /etc/os-release | head -3
  rpicam-still --version 2>&1 < /dev/null | head -2
  echo "cjxl: $(command -v cjxl) $(cjxl --version 2>&1 < /dev/null | head -1)"
  python3 -c "import numpy; print('numpy', numpy.__version__)" 2>&1
  grep -E "MemTotal|MemAvailable|CmaTotal|CmaFree" /proc/meminfo
  echo "cmdline: $(cat /proc/cmdline)"
  df -h "$HOME" /tmp | tail -2; } > env.txt 2>&1
# R0.4: the encoder guard (sh: oom_score_adj 1000 + ulimit -v 250 MB, then exec) must KILL a
# 400 MB allocation on this kernel, or rfb=mem can never fire and an overrun swaps the Zero.
python3 -c "import json, rc_raw_jxl as X; print('guard', json.dumps(X.run_capped(['python3', '-c', 'x = bytearray(400 * 2 ** 20)'], timeout_s=60, stdout_path='guard.out', stderr_path='guard.err')))" >> env.txt 2>&1 < /dev/null
cat env.txt

# >>> sampler (tests/test_s28_hil_r0_analyze.py runs this block as-is with a fake meminfo)
# CMA sampler: label,t,CmaFree_kB,MemAvailable_kB every 0.1 s; the label file names the phase.
# Written to a FILE first: in `python3 - <<'PY' ... < /dev/null &` the later redirection
# replaces the heredoc, python runs an EMPTY program and every CMA row is lost (bm #120 bug,
# found by the rig study on nereus002, 2026-10-03).
cat > sampler.py <<'PY'
import os
import time
MEMINFO = os.environ.get("S28R0_MEMINFO", "/proc/meminfo")   # test hook only
print("label,t,cma_free_kb,mem_available_kb", flush=True)
while True:
    vals = {}
    with open(MEMINFO) as fh:
        for line in fh:
            k, v = line.split(":", 1)
            if k in ("CmaFree", "MemAvailable"):
                vals[k] = int(v.split()[0])
    try:
        label = open("label.txt").read().strip()
    except OSError:
        label = "?"
    print(f"{label},{time.time():.2f},{vals.get('CmaFree', -1)},{vals.get('MemAvailable', -1)}",
          flush=True)
    if label == "stop":
        break
    time.sleep(0.1)
PY
echo idle > label.txt
python3 sampler.py > cma_samples.csv 2> sampler.err < /dev/null &
SAMPLER=$!
sleep 1
ROWS=$(( $(wc -l < cma_samples.csv) - 1 ))
if [ "$ROWS" -lt 3 ] || ! grep -q '^idle,' cma_samples.csv; then
  echo "FATAL: the CMA sampler wrote $ROWS row(s) in 1 s (want >= 3); R0.1 cannot be measured. sampler.err: $(tail -3 sampler.err 2>/dev/null)"
  echo stop > label.txt; kill $SAMPLER 2>/dev/null
  exit 3
fi
if grep -q '^idle,[^,]*,-1,' cma_samples.csv; then
  echo "FATAL: /proc/meminfo has no CmaFree on this kernel; R0.1 cannot be measured"
  echo stop > label.txt; kill $SAMPLER 2>/dev/null
  exit 3
fi
echo "CMA sampler running: pid $SAMPLER, $ROWS rows in the first second"
# <<< sampler

# R0.1 (EM 2026-10-05): the CMA allocation counters are the criterion (a failed allocation
# is a real CMA shortage); CmaFree above is info. Absent on a kernel without CONFIG_CMA_SYSFS:
# the analyzer then falls back to the kernel's "cma_alloc ... failed" dmesg lines.
echo "label,area,alloc_pages_success,alloc_pages_fail" > cma_counters.csv
cma_counters() {  # $1 phase label
  for a in /sys/kernel/mm/cma/*/; do
    [ -d "$a" ] || continue
    echo "$1,$(basename "$a"),$(cat "$a/alloc_pages_success" 2>/dev/null || echo NA),$(cat "$a/alloc_pages_fail" 2>/dev/null || echo NA)" >> cma_counters.csv
  done
}
cma_counters start
echo "CMA counters: $(( $(wc -l < cma_counters.csv) - 1 )) area(s) $(tail -1 cma_counters.csv)"

echo "mode,i,rc,elapsed_s,jpeg_bytes,dng_bytes" > captures.csv
cap() {  # $1 mode (plain|raw), $2 i
  local extra=""; [ "$1" = raw ] && extra="--raw"
  echo "cap_$1_$2" > label.txt
  rm -f c.jpg c.dng c.json
  local t0=$(date +%s.%N)
  timeout 30 rpicam-still -n --timeout 2000 --width 4608 --height 2592 --quality 95 \
      --metadata c.json $extra -o c.jpg > cap.out 2> cap.err < /dev/null
  local rc=$?
  local t1=$(date +%s.%N)
  echo "idle" > label.txt
  local jb=$(stat -c %s c.jpg 2>/dev/null || echo 0); local db=$(stat -c %s c.dng 2>/dev/null || echo 0)
  echo "$1,$2,$rc,$(awk "BEGIN{print $t1 - $t0}"),$jb,$db" >> captures.csv
  echo "capture $1 #$2 rc=$rc jpeg=$jb dng=$db"
}
for i in $(seq 1 "$N"); do cap plain "$i"; sleep 3; done
cma_counters after_plain
for i in $(seq 1 "$N"); do
  cap raw "$i"
  if [ "$i" = 1 ] && [ -s c.dng ]; then cp c.dng keep.dng; cp c.json keep.json; fi
  sleep 3
done
cma_counters after_raw
rm -f c.jpg c.dng
{ dmesg 2>/dev/null || sudo -n dmesg 2>/dev/null; } < /dev/null | tail -60 > dmesg_tail.txt

# R0.3: path [B] on the kept DNG, per preset, one rung (rung 1 of the S0 calibration)
echo "preset,i,rc,wall_s,bytes,distance,cjxl_s_sum,peak_rss_kb,rfb" > encodes.csv
crop_of() { case "$1" in 1600x900) echo 1504,846,1600,900;; 2000x1124) echo 1304,734,2000,1124;;
                         2400x1350) echo 1104,620,2400,1350;; esac; }
dist_of() { case "$1" in 1600x900) echo 3.8;; 2000x1124) echo 7.3;; 2400x1350) echo 9.0;; esac; }
if [ -s keep.dng ]; then
  for preset in $PRESETS; do
    for i in $(seq 1 "$ENC_N"); do
      echo "enc_${preset}_$i" > label.txt
      t0=$(date +%s.%N)
      python3 rc_raw_jxl.py --dng keep.dng --metadata keep.json --crop "$(crop_of "$preset")" \
          --distances "$(dist_of "$preset")" --target-fill 0 --encode-max-s 120 --message-cap 500 \
          --allow-any-crop --out "enc_${preset}_$i" > "enc_${preset}_$i.log" 2>&1 < /dev/null
      rc=$?; t1=$(date +%s.%N); echo idle > label.txt
      python3 - "$preset" "$i" "$rc" "$(awk "BEGIN{print $t1 - $t0}")" "enc_${preset}_$i/result.json" >> encodes.csv <<'PY'
import json, sys
preset, i, rc, wall, path = sys.argv[1:6]
try:
    r = json.load(open(path))
except Exception:
    r = {}
log = (r.get("attempt_log") or [{}])[-1]
print(",".join(str(v) for v in (preset, i, rc, wall, log.get("bytes", ""), log.get("distance", ""),
                                round(sum(log.get("cjxl_seconds", []) or [0]), 3),
                                log.get("peak_rss_kb", ""), r.get("rfb", ""))))
PY
      tail -1 encodes.csv
      sleep 2
    done
  done
else
  echo "NO DNG from any --raw capture: R0.3 not run (R0.1 FAILS)"
fi
cma_counters after_encode
echo stop > label.txt; sleep 0.5; kill $SAMPLER 2>/dev/null
df -h "$HOME" | tail -1 >> env.txt
REMOTE
REMOTE_RC=${PIPESTATUS[0]}

scp -q -r -o BatchMode=yes "$U@$H:$D/env.txt" "$U@$H:$D/captures.csv" "$U@$H:$D/cma_samples.csv" \
    "$U@$H:$D/cma_counters.csv" \
    "$U@$H:$D/encodes.csv" "$U@$H:$D/dmesg_tail.txt" "$P/" 2>/dev/null
scp -q -r -o BatchMode=yes "$U@$H:$D/keep.json" "$P/capture_metadata_raw1.json" 2>/dev/null
scp -q -r -o BatchMode=yes "$U@$H:$D/enc_*" "$P/" 2>/dev/null
find "$P" -name "*.nrjxl" -size +200k -delete 2>/dev/null   # keep the small blobs only
if [ "${S28R0_KEEP:-0}" != 1 ]; then $SSH "rm -rf $D" < /dev/null; fi
# Fail loudly: an empty CMA log means R0.1 was never measured (not a FAIL of the unit).
CMA_ROWS=$(( $( (wc -l < "$P/cma_samples.csv") 2>/dev/null || echo 0) - 1 ))
if [ "$REMOTE_RC" != 0 ] || [ "$CMA_ROWS" -lt 10 ]; then
  log "R0 probe FAILED TO MEASURE: remote exit $REMOTE_RC, $CMA_ROWS CMA rows pulled (want >= 10); see gate.log [r0][pi] lines. Do NOT score R0.1 from this run."
  exit 3
fi
log "R0 probe done: pulled to $P ($(ls "$P" | wc -l | tr -d ' ') entries); next: hil/tools/hil_s28_r0_analyze.py $OUT $H"
