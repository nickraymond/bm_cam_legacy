#!/bin/bash
# hil_b0_cjxl_guard.sh — Sprint28 B3a bench item B0 (encoder half), run ON a bench Pi (bmcam003/bmcam004).
#
# Purpose:  measure cjxl VarDCT on a 1600x900 linear-RGB PPM under the production encoder guard
#           (oom_score_adj 1000; ulimit -v 250 MB; exec), as DESIGN_B3a.md §1 step 5 / §6 B0 specify,
#           BEFORE the B3a build exists: VmPeak / VmHWM / wall time per encode, 0 guard kills.
# Inputs:   $1 kept raw crop PGM (16-bit, CFA mosaic, e.g. *_raw_crop.pgm from still.raw.keep_crop),
#           env RUNS (default 10), D (default 2.6), E (default 5), SEARCH_D (default "2.6 3.5 4.6"),
#           GUARD_KB (default 256000 = 250 MiB), CFA (default BGGR: rc_raw_jxl's IMX708 DNG note)
# Outputs:  /tmp/b0/: x.ppm (16-bit RGB, bilinear demosaic, black/white from the PGM header maxval),
#           b0.csv (run,d,e,wall_s,vmpeak_kb,vmhwm_kb,rc,bytes), summary lines on stdout
# Example:  scp hil/tools/hil_b0_cjxl_guard.sh pi@bmcam004:/tmp/ &&
#           ssh pi@bmcam004 'bash /tmp/hil_b0_cjxl_guard.sh /home/pi/BM_Devel_Pi/images/<stem>_raw_crop.pgm'
# Assumptions: the demosaic is a simple bilinear one in numpy (not B3a's own prep); WB / colour transform
#           NOT applied (encoder load depends on size and texture, not on the exact colours); the 3-encode
#           search distances are a stand-in for B3a's byte search. VmPeak/VmHWM are sampled every 50 ms
#           from /proc/<pid>/status (a sub-50 ms peak can be missed; VmPeak itself is a high-water mark).
# Limits:   run with the capture cycle STOPPED (deploy window, cron disarmed) so nothing else holds memory.
set -u
case "$(hostname)" in bmcam003|bmcam004) ;; *) echo "[hil-guard] REFUSED: not a bench unit ($(hostname))" >&2; exit 5;; esac
PGM="${1:?kept raw crop PGM}"; RUNS="${RUNS:-10}"; D="${D:-2.6}"; E="${E:-5}"
SEARCH_D="${SEARCH_D:-2.6 3.5 4.6}"; GUARD_KB="${GUARD_KB:-256000}"; CFA="${CFA:-BGGR}"
OUT=/tmp/b0; mkdir -p "$OUT"; CSV="$OUT/b0.csv"
command -v cjxl >/dev/null || { echo "FAIL: cjxl not installed"; exit 2; }
echo "[b0] host=$(hostname) $(cjxl --version 2>&1 | head -1) pgm=$PGM runs=$RUNS d=$D e=$E guard=${GUARD_KB}kB cfa=$CFA"
grep -E "MemAvailable|CmaFree" /proc/meminfo | tr -s ' ' | tr '\n' ' '; echo

t0=$(date +%s.%N)
python3 - "$PGM" "$OUT/x.ppm" "$CFA" <<'EOF' || { echo "FAIL: demosaic"; exit 3; }
import sys, numpy as np
src, dst, cfa = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, "rb") as f:
    hdr = []
    while len(hdr) < 4:
        line = f.readline()
        if line.startswith(b"#"): continue
        hdr += line.split()
    assert hdr[0] == b"P5", hdr
    w, h, maxv = int(hdr[1]), int(hdr[2]), int(hdr[3])
    m = np.frombuffer(f.read(w * h * 2), dtype=">u2").reshape(h, w).astype(np.float32)
pos = {c: [] for c in "RGB"}
for i, c in enumerate(cfa):                      # 2x2 tile, row-major
    pos[c].append((i // 2, i % 2))
k = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], np.float32)
def conv(a):
    p = np.pad(a, 1, mode="reflect"); o = np.zeros_like(a)
    for dy in range(3):
        for dx in range(3):
            o += k[dy, dx] * p[dy:dy + a.shape[0], dx:dx + a.shape[1]]
    return o
rgb = np.empty((h, w, 3), np.uint16)
for ci, c in enumerate("RGB"):
    mask = np.zeros((h, w), np.float32)
    for (y, x) in pos[c]: mask[y::2, x::2] = 1
    rgb[..., ci] = np.clip(conv(m * mask) / np.maximum(conv(mask), 1e-6), 0, maxv).astype(np.uint16)
with open(dst, "wb") as f:
    f.write(b"P6\n%d %d\n%d\n" % (w, h, maxv)); f.write(rgb.astype(">u2").tobytes())
print(f"[b0] demosaic {w}x{h} maxval {maxv} -> {dst}")
EOF
echo "[b0] prep_s=$(awk -v a="$(date +%s.%N)" -v b="$t0" "BEGIN{printf \"%.2f\", a-b}")"
echo "run,d,e,wall_s,vmpeak_kb,vmhwm_kb,rc,bytes" > "$CSV"

encode() {   # $1 label, $2 distance
  local out="$OUT/x_$1.jxl" pk=0 hw=0 v
  local s; s=$(date +%s.%N)
  ( echo 1000 > /proc/self/oom_score_adj; ulimit -v "$GUARD_KB"; exec cjxl "$OUT/x.ppm" "$out" -m 0 -e "$E" -d "$2" --num_threads=0 --quiet ) &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    v=$(awk '/^VmPeak/{p=$2}/^VmHWM/{h=$2}END{print p+0, h+0}' /proc/$pid/status 2>/dev/null)
    [ -n "$v" ] && { set -- "$1" "$2" $v; [ "${3:-0}" -gt "$pk" ] && pk=$3; [ "${4:-0}" -gt "$hw" ] && hw=$4; }
    sleep 0.05
  done
  wait "$pid"; local rc=$?
  local wall; wall=$(awk -v a="$(date +%s.%N)" -v b="$s" "BEGIN{printf \"%.2f\", a-b}")
  echo "$1,$2,$E,$wall,$pk,$hw,$rc,$(stat -c %s "$out" 2>/dev/null || echo 0)" | tee -a "$CSV"
}
for i in $(seq 1 "$RUNS"); do encode "r$i" "$D"; done
st=$(date +%s.%N); for d in $SEARCH_D; do encode "s$d" "$d"; done
echo "[b0] search_total_s=$(awk -v a="$(date +%s.%N)" -v b="$st" "BEGIN{printf \"%.2f\", a-b}") (distances: $SEARCH_D)"
python3 - "$CSV" "$GUARD_KB" <<'EOF'
import csv, statistics, sys
rows = list(csv.DictReader(open(sys.argv[1]))); g = int(sys.argv[2])
r = [x for x in rows if x["run"].startswith("r")]
walls = [float(x["wall_s"]) for x in r]
print(f"[b0] repeat n={len(r)} median_wall_s={statistics.median(walls):.2f} max_wall_s={max(walls):.2f} "
      f"max_vmpeak_kb={max(int(x['vmpeak_kb']) for x in rows)} max_vmhwm_kb={max(int(x['vmhwm_kb']) for x in rows)} "
      f"guard_kb={g} nonzero_rc={sum(x['rc'] != '0' for x in rows)}")
EOF
echo "[b0] dmesg kills: $(dmesg 2>/dev/null | grep -ciE 'killed process|oom' || echo n/a)"
grep -E "MemAvailable|CmaFree" /proc/meminfo | tr -s ' ' | tr '\n' ' '; echo
