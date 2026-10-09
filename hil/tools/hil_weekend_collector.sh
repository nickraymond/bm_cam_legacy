#!/bin/bash
# hil_weekend_collector.sh: detached, READ-ONLY evidence collector for an unattended bench weekend (EM/Nick 2026-10-09).
# Survives a Claude session death: launch with nohup (see Example). Never writes to a unit, a Spotter or nereus000.
#
# Per hourly wake HH (both units):
#   HH:01:30–HH:09:30  every 20 s while the Pi answers: newest cycle log + newest capture-metadata sidecar
#                      → $OUT/pulled/<host>/cycle_<D>T<HH>.log, meta_<D>T<HH>.json.txt (last good copy wins)
#   HH:16              per Spotter: hil_wake_report.sh (console excerpt + wakes.csv) and first-send loss
#                      (hil_r2_score.py, or runs/reef_rc_20261007/console_loss.py when START was lost)
#   HH:20              media table on nereus000 per device since HEAL_SINCE → $OUT/analysis/media_<dev>_<D>T<HH>.csv
#   one summary line per unit per wake → $OUT/summary.csv; progress → $OUT/collector.log
# Stop: touch $OUT/STOP, or the --until time passes. Holds `caffeinate -i` for its own lifetime.
# Inputs:  $1 OUT dir, $2 until (UTC ISO, e.g. 2026-10-12T16:00:00Z)
# Example: nohup bash hil/tools/hil_weekend_collector.sh runs/weekend_20261010 2026-10-12T16:00:00Z \
#            > runs/weekend_20261010/nohup.out 2>&1 < /dev/null & disown
# Limits:  needs the Mac awake (lid open + AC); git commits are left to a live session; a unit that never boots in a
#          window gets a "no cycle log" summary row.
set -u
cd "$(dirname "$0")/../.."
OUT=$1; UNTIL=$2; mkdir -p "$OUT/pulled" "$OUT/analysis" "$OUT/console"
caffeinate -i -w $$ &
UNITS="bmcam003:SPOT-33507C:BMCAM_003 bmcam004:SPOT-31593C:BMCAM_004"
HEAL_SINCE=${HEAL_SINCE:-2026-10-08T19:55}
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)
log() { echo "$(date -u +%FT%TZ) $*" >> "$OUT/collector.log"; }
ts_of() { python3 -c "import datetime as d,sys;print(int(d.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$1"; }
wait_until() { local t; t=$(ts_of "$1"); while [ "$(date +%s)" -lt "$t" ]; do [ -f "$OUT/STOP" ] && return 1; sleep 10; done; return 0; }
END=$(ts_of "$UNTIL")
[ -f "$OUT/summary.csv" ] || echo "wake_utc,host,spot,sha,cfg_hash,format,exposure_profile,applied,AG,ET_us,M,loss_pct,start_on_console,halt_s,heal_chunks_sent,cmds_applied,media_open,media_backlog_chunks" > "$OUT/summary.csv"
log "collector start pid $$ out=$OUT until=$UNTIL"
while [ "$(date +%s)" -lt "$END" ] && [ ! -f "$OUT/STOP" ]; do
  # next wake hour: the coming HH:01:30
  NEXT=$(python3 -c "import datetime as d;n=d.datetime.now(d.timezone.utc);t=n.replace(minute=1,second=30,microsecond=0);t=t if t>n else t+d.timedelta(hours=1);print(t.strftime('%Y-%m-%dT%H:%M:%SZ'))")
  D=${NEXT:0:10}; HH=${NEXT:11:2}
  wait_until "$NEXT" || break
  log "wake $D $HH: pulling"
  stop=$(( $(date +%s) + 480 ))
  while [ "$(date +%s)" -lt "$stop" ]; do
    for u in $UNITS; do
      H=${u%%:*}; mkdir -p "$OUT/pulled/$H"
      ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; f=$(ls -t cron_logs/rc_cycle_*.log | head -1); echo "LOG $f"; head -c 40 software_sha.txt; echo; cat "$f"' </dev/null > "$OUT/pulled/$H/.cyc" 2>/dev/null && [ -s "$OUT/pulled/$H/.cyc" ] && mv "$OUT/pulled/$H/.cyc" "$OUT/pulled/$H/cycle_${D}T${HH}.log"
      ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; m=$(ls -t images/*capture_metadata.json 2>/dev/null | head -1); [ -n "$m" ] && echo "FILE $m" && cat "$m"' </dev/null > "$OUT/pulled/$H/.meta" 2>/dev/null && [ -s "$OUT/pulled/$H/.meta" ] && mv "$OUT/pulled/$H/.meta" "$OUT/pulled/$H/meta_${D}T${HH}.json.txt"
    done
    sleep 20
  done
  wait_until "${D}T${HH}:16:00Z" || break
  for u in $UNITS; do
    H=${u%%:*}; R=${u#*:}; S=${R%%:*}
    HIL_RUN_DIR=$OUT hil/tools/hil_wake_report.sh $S ${D}T$HH:00 >> "$OUT/collector.log" 2>&1
  done
  wait_until "${D}T${HH}:20:00Z" || break
  for u in $UNITS; do
    H=${u%%:*}; R=${u#*:}; S=${R%%:*}; DEV=${R#*:}
    ssh "${O[@]}" pi@192.168.1.45 "python3 /home/pi/hil_g4/hil_media_table.py --api https://nereus-vision-staging.onrender.com --devices $DEV --since $HEAL_SINCE --until ${D}T$HH:10 2>&1" </dev/null > "$OUT/analysis/media_${DEV}_${D}T${HH}.csv" 2>/dev/null
    python3 - "$OUT" "$H" "$S" "$DEV" "$D" "$HH" >> "$OUT/summary.csv" 2>>"$OUT/collector.log" <<'PY'
import csv, glob, json, os, re, subprocess, sys
out, h, s, dev, d, hh = sys.argv[1:]
cyc = os.path.join(out, "pulled", h, f"cycle_{d}T{hh}.log"); meta = os.path.join(out, "pulled", h, f"meta_{d}T{hh}.json.txt")
C = open(cyc, errors="replace").read() if os.path.exists(cyc) else ""
M = open(meta, errors="replace").read() if os.path.exists(meta) else ""
g = lambda pat, T, i=1: (re.search(pat, T).group(i) if re.search(pat, T) else "")
sha = g(r"^([0-9a-f]{12})$", C) if C else ""
sha = (re.search(r"^[0-9a-f]{12}$", C, re.M).group(0) if re.search(r"^[0-9a-f]{12}$", C, re.M) else "")
cfg = g(r"\[CFG\] config v2 \S+ media=\S+ hash=(\w+)", C)
fmt = "nrjxl" if "nrjxl ready" in C else ("pjpg" if "[RC] attempt" in C else "")
key = g(r"media key (\w+)", C)
con = sorted(glob.glob(os.path.join(out, "console", f"wake_{s}_{d}T{hh}00*.txt")))
loss, m_, start = "", "", ""
if con and key:
    try:
        o = subprocess.run(["python3", "runs/reef_rc_20261007/console_loss.py", con[0], key], capture_output=True, text=True, timeout=60).stdout
        m_ = g(r" M (\d+)", o); loss = g(r"loss ([\d.]+)", o); start = "no" if "START None" in o else "yes"
    except Exception:
        pass
halt = g(r"halt uptime_s=([\d.]+)", C)
heal = g(r"\[HEAL\] sent (\d+) heal chunk", C) or "0"
cmds = ";".join(re.findall(r"\[CMD\] applied id=(1\d{6}) set", C))
mt = os.path.join(out, "analysis", f"media_{dev}_{d}T{hh}.csv")
rows = [r for r in csv.DictReader(open(mt))] if os.path.exists(mt) else []
op = [r for r in rows if r.get("complete") != "True" and r.get("media_id")]
print(",".join(str(x) for x in [f"{d}T{hh}:00Z", h, s, sha, cfg, fmt, g(r'"exposure_profile": *"(\w+)"', M), g(r'"exposure_profile_applied": *(\w+)', M),
      g(r'"AnalogueGain": *([\d.]+)', M), g(r'"ExposureTime": *(\d+)', M), m_, loss, start, halt, heal, cmds, len(op), sum(int(r.get("missing") or 0) for r in op)]))
PY
  done
  log "wake $D $HH: done ($(tail -2 "$OUT/summary.csv" | tr '\n' ' '))"
done
log "collector exit (STOP file or until reached)"
