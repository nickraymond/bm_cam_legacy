#!/bin/bash
# hil_b0_b3a_run.sh — Sprint28 B3a bench item B0 with the REAL pipeline, run ON a bench Pi (bmcam003/bmcam004).
#
# Purpose:  LADDER.md "B3a bench: B0": N× `rc_raw_jxl.py --layout rgb` (production numpy strip demosaic + cjxl
#           VarDCT in the guarded child: oom_score_adj 1000, ulimit -v 250 MiB) on one DNG, at effort 5 and 4.
#           Records per run: rfb / distance / attempts / encode_s / rgb prep (result.json), cjxl VmPeak + VmHWM
#           (sampled from /proc every 50 ms by a side loop), Pi CPU time (bash `time`: user+sys incl. children), wall.
# Inputs:   env APP (default /home/pi/BM_Devel_Pi: the DEPLOYED app, which must contain the B3a rc_raw_jxl.py),
#           DNG + META (rpicam --metadata JSON); if DNG is unset, ONE capture is taken:
#           `rpicam-still -n --raw -t 1000 --metadata cap.json -o cap.jpg` (the capture cycle must be stopped).
#           RUNS_E5 (default 10), RUNS_E4 (default 2), OUT (default /home/pi/b0_b3a: survives a reboot, so a second
#           bus window RESUMES: finished runs are skipped, the same DNG is reused), DEADLINE (epoch s: no new run starts
#           after it; set it ~2.5 min before the bus hard-cut at :10)
# Outputs:  $OUT/run_e<E>_<n>/{result.json,stdout.txt,time.txt,vm.txt}, $OUT/b0_summary.csv, summary on stdout
# Example:  scp hil/tools/hil_b0_b3a_run.sh pi@bmcam004:/tmp/ && ssh pi@bmcam004 'bash /tmp/hil_b0_b3a_run.sh'
#           LOWGAIN=1 (default): before the encodes, one AGC pair with the camera free: `rpicam-still` auto, then
#           low_gain (args from the deployed rc_exposure_profile.py on a scratch island YAML, state dir in $OUT/ep,
#           NOT the app's own state dir); records ExposureTime / AnalogueGain and the "tuning file" line → lg_pair.txt
# Limits:   a sub-50 ms VmPeak spike can be missed by the sampler (VmPeak itself is a high-water mark, so the last
#           sample of a run is representative); a night capture is a noisy scene (harder for the encoder than day).
set -u
case "$(hostname)" in bmcam003|bmcam004) ;; *) echo "[hil-guard] REFUSED: not a bench unit ($(hostname))" >&2; exit 5;; esac
APP="${APP:-/home/pi/BM_Devel_Pi}"; OUT="${OUT:-/home/pi/b0_b3a}"; DEADLINE="${DEADLINE:-0}"; RUNS_E5="${RUNS_E5:-10}"; RUNS_E4="${RUNS_E4:-2}"
mkdir -p "$OUT"
grep -q "layout" "$APP/rc_raw_jxl.py" || { echo "FAIL: $APP/rc_raw_jxl.py has no --layout (not the B3a build)"; exit 2; }
pgrep -f 'rc_progressive_jpeg.py|rc_run_capture_cycle.sh' >/dev/null && { echo "FAIL: capture cycle running"; exit 3; }
echo "[b0] host=$(hostname) sha=$(cat "$APP/software_sha.txt" 2>/dev/null | head -1) $(cjxl --version 2>&1 | head -1)"
grep -E "MemTotal|MemAvailable|CmaTotal|CmaFree" /proc/meminfo | tr -s ' ' | tr '\n' ' '; echo
[ -z "${DNG:-}" ] && [ -s "$OUT/cap.dng" ] && { DNG="$OUT/cap.dng"; META="$OUT/cap.json"; echo "[b0] resuming with $DNG"; }
if [ -z "${DNG:-}" ]; then
  echo "[b0] no DNG given: one capture"
  rpicam-still -n --raw -t 1000 --metadata "$OUT/cap.json" -o "$OUT/cap.jpg" > "$OUT/cap.log" 2>&1 || { echo "FAIL: capture"; tail -5 "$OUT/cap.log"; exit 4; }
  DNG="$OUT/cap.dng"; META="$OUT/cap.json"
fi
[ -s "$DNG" ] && [ -s "${META:?META}" ] || { echo "FAIL: DNG/META missing"; exit 4; }
if [ "${LOWGAIN:-1}" = 1 ] && [ ! -s "$OUT/lg_pair.txt" ]; then
  printf 'exposure_profile:\n  profile: low_gain\n  max_shutter_us: 30000\n  max_gain: 16.0\n' > "$OUT/lg.yaml"
  LGARGS=$(cd "$APP" && python3 rc_exposure_profile.py --config "$OUT/lg.yaml" --state-dir "$OUT/ep" 2>"$OUT/lg_profile.err" \
           | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('args') or []))")
  echo "[b0] low_gain args: ${LGARGS:-<none: see lg_profile.err>}"
  for m in auto lowgain; do
    A=""; [ "$m" = lowgain ] && A="$LGARGS"
    rpicam-still -n -t 2000 $A --metadata "$OUT/lg_$m.json" -o "$OUT/lg_$m.jpg" > "$OUT/lg_$m.log" 2>&1
    python3 - "$OUT/lg_$m.json" "$m" "$OUT/lg_$m.log" <<'PY' | tee -a "$OUT/lg_pair.txt"
import json, re, sys
j = json.load(open(sys.argv[1])); log = open(sys.argv[3]).read()
t = re.findall(r"[Tt]uning file[^\n]*", log)
print(f"[b0][agc] {sys.argv[2]}: ExposureTime={j.get('ExposureTime')} AnalogueGain={j.get('AnalogueGain')} "
      f"DigitalGain={j.get('DigitalGain')} Lux={j.get('Lux')} | {t[-1] if t else 'no tuning-file line'}")
PY
  done
fi
echo "[b0] dng=$DNG ($(stat -c %s "$DNG") B) meta=$META"

run() {   # $1 effort, $2 n
  local d="$OUT/run_e$1_$2"
  [ -s "$d/rc_wall.txt" ] && return 0                                   # done in an earlier window
  if [ "$DEADLINE" -gt 0 ] && [ "$(date +%s)" -ge "$DEADLINE" ]; then echo "[b0] deadline: e$1 #$2 not started"; return 0; fi
  mkdir -p "$d"
  ( pk=0; hw=0; while :; do
      for p in $(pgrep -x cjxl); do
        read -r a b < <(awk '/^VmPeak/{p=$2}/^VmHWM/{h=$2}END{print p+0, h+0}' /proc/$p/status 2>/dev/null)
        [ "${a:-0}" -gt "$pk" ] && pk=$a; [ "${b:-0}" -gt "$hw" ] && hw=$b
      done
      echo "$pk $hw" > "$d/vm.txt"; sleep 0.05
    done ) & local smp=$!
  local s; s=$(date +%s.%N)
  { time ( cd "$APP" && python3 -u rc_raw_jxl.py --dng "$DNG" --metadata "$META" --layout rgb --effort "$1" \
            --encode-max-s 30 --out "$d" > "$d/stdout.txt" 2>&1 ); } 2> "$d/time.txt"
  local rc=$?
  kill "$smp" 2>/dev/null; wait "$smp" 2>/dev/null
  echo "$rc $(awk -v a="$(date +%s.%N)" -v b="$s" 'BEGIN{printf "%.2f", a-b}')" > "$d/rc_wall.txt"
  echo "[b0] e$1 #$2 rc=$rc wall=$(cut -d' ' -f2 "$d/rc_wall.txt") s vm(peak,hwm kB)=$(cat "$d/vm.txt") $(tail -1 "$d/stdout.txt" | cut -c1-110)"
}
for i in $(seq 1 "$RUNS_E5"); do run 5 "$i"; done
for i in $(seq 1 "$RUNS_E4"); do run 4 "$i"; done
ls -d "$OUT"/run_e* >/dev/null 2>&1 && python3 - "$OUT" <<'EOF'
import csv, glob, json, os, re, statistics, sys
out = sys.argv[1]; rows = []
for d in sorted(glob.glob(os.path.join(out, "run_e*"))):
    if not os.path.exists(os.path.join(d, "rc_wall.txt")): continue
    e, n = re.search(r"run_e(\d)_(\d+)", d).groups()
    r = json.load(open(os.path.join(d, "result.json"))) if os.path.exists(os.path.join(d, "result.json")) else {}
    rc, wall = open(os.path.join(d, "rc_wall.txt")).read().split()
    pk, hw = (open(os.path.join(d, "vm.txt")).read().split() + ["0", "0"])[:2]
    t = open(os.path.join(d, "time.txt")).read()
    cpu = sum(float(m) * 60 + float(s) for m, s in re.findall(r"(?:user|sys)\s+(\d+)m([\d.]+)s", t))
    al = r.get("attempt_log") or []
    rows.append(dict(effort=e, run=n, rc=rc, wall_s=wall, cpu_s=round(cpu, 2), rfb=r.get("rfb", ""),
                     distance=r.get("distance", ""), attempts=r.get("attempts", len(al)), encode_s=r.get("encode_s", ""),
                     bytes=r.get("bytes", r.get("size", "")), timings=json.dumps(r.get("timings", {}), separators=(",", ":")),
                     cjxl_vmpeak_kb=pk, cjxl_vmhwm_kb=hw,
                     attempt_peak_rss_kb=max([a.get("peak_rss_kb") or 0 for a in al if isinstance(a, dict)] or [0])))
w = csv.DictWriter(open(os.path.join(out, "b0_summary.csv"), "w"), fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
for e in sorted({r["effort"] for r in rows}):
    rs = [r for r in rows if r["effort"] == e]
    enc = [float(r["encode_s"]) for r in rs if r["encode_s"] != ""]
    print(f"[b0] e{e}: n={len(rs)} kills/fallbacks={sum(r['rfb'] != '' for r in rs)} "
          f"median_encode_s={statistics.median(enc) if enc else 'n/a'} max_encode_s={max(enc) if enc else 'n/a'} "
          f"max_vmpeak_kb={max(int(r['cjxl_vmpeak_kb']) for r in rs)} max_vmhwm_kb={max(int(r['cjxl_vmhwm_kb']) for r in rs)} "
          f"median_cpu_s={statistics.median([r['cpu_s'] for r in rs])} median_wall_s={statistics.median([float(r['wall_s']) for r in rs])}")
EOF
echo "[b0] dmesg kill/oom lines: $(dmesg 2>/dev/null | grep -ciE 'killed process|out of memory' || echo n/a)"
grep -E "MemAvailable|CmaFree" /proc/meminfo | tr -s ' ' | tr '\n' ' '; echo
