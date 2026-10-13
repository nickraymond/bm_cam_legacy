#!/bin/bash
# tendure_driver.sh: T-ENDURE (EM card 2026-10-12 ~1:50 PM) on bmcam003 + bmcam004, DETACHED (nohup) so it outlives the
# session's ~2 h background-job limit. Pool commands via pool_cmd.py on nereus000 (Nick OK b). Writes only to this folder.
# Phases: battery + status -> cal 1200 s @10 s (both) -> loop 18 x 600 s (both) -> preview at loop+45 min -> mark endure_1p5h at
# loop+90 min -> wait loop 18/18 -> final status. Pull + restore are done by the TE session afterwards (not here).
# Side monitors (same process tree): rss sampler per unit (5 s), battery + stop-rule watcher (5 min).
# Stop rules (EM card): either Spotter batt < 3.80 V -> pool stop both + STOP file; pool.log ERR/Traceback/MemoryError or
# unit unreachable for 10 min -> pool stop THAT unit + note. Manual stop: touch STOP.
# v2 (2026-10-12 3 PM, after the T-ENDURE Mac-sleep gap): (1) `loop` is sent only to units whose pool.log shows no loop
#   started yet (a second loop in one run is REFUSED with ERR on f8caaa1, and the old watcher stopped the unit on any ERR);
# (2) the ERR watcher ignores `ERR id=... refused` command answers (counts Traceback/MemoryError/Killed/OOM + other ERR);
# (3) console commands are skipped (logged) when nereus000 is unreachable instead of failing silently.
# Usage: nohup bash tendure_driver_v2.sh > driver.out 2>&1 < /dev/null & disown
set -u
cd "$(dirname "$0")"; DIR=$PWD
caffeinate -i -w $$ &
MON=pi@192.168.1.45; O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
ts(){ date -u +%FT%TZ; }; log(){ echo "$(ts) $*" >> driver.log; }
P(){ ping -c 1 -t 3 192.168.1.45 >/dev/null 2>&1 || { log "SKIP (nereus000 unreachable): pool_cmd $*"; return 1; }; log ">> pool_cmd $*"; ssh "${O[@]}" $MON "python3 /home/pi/pool_tools/pool_cmd.py $* --wait 40" </dev/null >> driver.log 2>&1; log "<< rc=$?"; }
batt(){ ssh "${O[@]}" $MON 'tail -1 /home/pi/hil_health/health.csv' </dev/null 2>/dev/null | python3 -c "
import csv,sys
try:
  r=next(csv.reader(sys.stdin)); print(r[0],r[1],r[2],r[9],r[12],r[13],r[14],r[21])
except Exception: print('NA')"; }
rss_sampler(){ local H=$1; while [ ! -f STOP ] && [ ! -f DONE ]; do
  ssh "${O[@]}" pi@$H 'for i in $(seq 1 120); do p=$(pgrep -f "^[^ ]*python3? [^ ]*pool_sequence\.py" | head -1); [ -n "$p" ] && echo "$(date -u +%T) $(grep -E "VmHWM|VmRSS" /proc/$p/status | tr -s " \t" " " | tr "\n" " ") avail=$(grep MemAvailable /proc/meminfo | tr -s " " | cut -d" " -f2) free_mb=$(df -BM --output=avail / | tail -1 | tr -d " M")"; sleep 5; done' </dev/null >> rss_$H.log 2>&1 || { echo "$(ts) ssh-fail" >> rss_$H.log; sleep 30; }
done; }
watch(){
  while [ ! -f STOP ] && [ ! -f DONE ]; do
    b=$(batt); echo "$(ts) $b" >> battery.log
    read -r a31 v31 w31 i31 a35 v35 w35 i35 <<< "$b"
    if python3 -c "import sys; v=[float(x) for x in sys.argv[1:] if x not in ('','NA')]; sys.exit(0 if v and min(v)<3.80 else 1)" "${v31:-}" "${v35:-}"; then
      log "STOP RULE: Spotter battery < 3.80 V ($b) -> pool stop both"; P both stop; touch STOP; break; fi
    for H in bmcam003 bmcam004; do
      [ -f STOPPED_$H ] && continue
      e=$(ssh "${O[@]}" pi@$H 'r=$(ls -td /home/pi/pool_runs/*/ | head -1); grep -E "\bERR\b|Traceback|MemoryError|Killed" $r/pool.log | grep -vcE "ERR id=[0-9]+ pool [a-z]+ refused"; dmesg 2>/dev/null | grep -ciE "out of memory|oom-kill"' </dev/null 2>/dev/null | tr '\n' ' ')
      n=$(cat down_$H 2>/dev/null || echo 0)
      if [ -z "$e" ]; then n=$((n + 1)); else n=0; fi; echo $n > down_$H
      echo "$(ts) $H err/oom: ${e:-unreachable} down=$n" >> health.log
      set -- $e
      if [ "${1:-0}" != 0 ] || [ "${2:-0}" != 0 ] || [ $n -ge 2 ]; then log "STOP RULE: $H err/oom='$e' down=$n -> pool stop $H"; P $H stop; touch STOPPED_$H; fi
    done
    sleep 300
  done; }
loop_done(){ ssh "${O[@]}" pi@$1 'r=$(ls -td /home/pi/pool_runs/*/ | head -1); grep -c "loop 18/18" $r/pool.log' </dev/null 2>/dev/null; }
cal_done(){ ssh "${O[@]}" pi@$1 'r=$(ls -td /home/pi/pool_runs/*/ | head -1); python3 -c "import json;m=json.load(open(\"$r/run_manifest.json\"));print(len(m.get(\"calibration\",[])))"' </dev/null 2>/dev/null; }
log "== T-ENDURE driver start pid $$"
rss_sampler bmcam003 & rss_sampler bmcam004 & watch &
P both status
P both cal --duration-s 1200 --interval-s 10
T=$(( $(date +%s) + 1500 )); while [ "$(date +%s)" -lt $T ] && [ ! -f STOP ]; do
  c3=$(cal_done bmcam003); c4=$(cal_done bmcam004); log "cal progress 003=$c3 004=$c4"
  { [ "${c3:-0}" -ge 120 ] || [ -f STOPPED_bmcam003 ]; } && { [ "${c4:-0}" -ge 120 ] || [ -f STOPPED_bmcam004 ]; } && sleep 20 && break; sleep 60; done
P both status
loop_started(){ ssh "${O[@]}" pi@$1 'r=$(ls -td /home/pi/pool_runs/*/ | head -1); grep -cE "loop start|loop 1/|loop auto-start: starting" $r/pool.log' </dev/null 2>/dev/null; }
if [ ! -f STOP ]; then for H in bmcam003 bmcam004; do
  n=$(loop_started $H); if [ "${n:-0}" -ge 1 ]; then log "$H loop already started (auto-start?): not sending loop"; else P $H loop --iterations 18 --period-s 600; fi
done; fi
L0=$(date +%s); prev=0; mark=0
while [ ! -f STOP ]; do
  el=$(( $(date +%s) - L0 ))
  [ $prev = 0 ] && [ $el -ge 2700 ] && { log "mid-loop preview (expect wait behind a clip)"; P both preview; prev=1; }
  [ $mark = 0 ] && [ $el -ge 5400 ] && { P both mark endure_1p5h; mark=1; }
  d3=$(loop_done bmcam003); d4=$(loop_done bmcam004)
  { [ "${d3:-0}" -ge 1 ] || [ -f STOPPED_bmcam003 ]; } && { [ "${d4:-0}" -ge 1 ] || [ -f STOPPED_bmcam004 ]; } && break
  [ $el -ge 12600 ] && { log "loop did not finish in 3.5 h"; break; }
  sleep 60
done
P both status
log "== T-ENDURE driver end (loop phase); pull + restore by the TE session"
touch DONE; sleep 10; kill %1 %2 %3 2>/dev/null
