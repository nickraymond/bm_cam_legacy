#!/bin/bash
# T-FOCUS sweep pair (Nick OK sweep 2026-10-13): two back-to-back `sweep` on both units via the nereus000 console.
# Waits on the CONSOLE log for each id's final "OK id=N pool sweep n=" on both Spotters (pool_cmd returns early on the
# replay "in_progress dup" line until bm #167). Then pool_pull --sweep (development) + focus_sweep_sheet with tag $1.
# Usage: bash tfocus_sweep2x.sh <tag e.g. 0.7>   Output: tfocus.log, ~/NereusPool/<date>/..., runs/focus_sweep_<UTC>/
set -u
cd "$(dirname "$0")"; TAG=${1:?tag}
DEV=/private/tmp/claude-501/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy--claude-worktrees-sprint26-s3a-runtime-parity-092958/3cd7e446-c576-4dd7-8494-833abcb29a17/scratchpad/dev_head2
O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=10); MON=pi@192.168.1.45
log(){ echo "$(date -u +%FT%TZ) $*" | tee -a tfocus.log; }
DIRS=()
for k in 1 2; do
  log "== sweep $k (tag $TAG)"
  out=$(ssh "${O[@]}" $MON 'python3 /home/pi/pool_tools/pool_cmd.py both sweep --wait 180' </dev/null 2>&1); echo "$out" >> tfocus.log
  id=$(echo "$out" | grep -oE "id=9[0-9]{4}" | head -1 | cut -d= -f2)
  [ -n "$id" ] || { log "no id from pool_cmd; STOP"; exit 1; }
  for i in $(seq 1 60); do
    n=$(ssh "${O[@]}" $MON "cat /home/pi/spotter_logs/SPOT-*/console_\$(date -u +%Y%m%d).log | grep -E 'OK id=$id pool sweep n=|ERR id=$id' | sort -u | wc -l" </dev/null)
    [ "${n:-0}" -ge 2 ] && break; sleep 5
  done
  ssh "${O[@]}" $MON "grep -hE 'OK id=$id pool sweep n=|ERR id=$id' /home/pi/spotter_logs/SPOT-*/console_\$(date -u +%Y%m%d).log | sort -u" </dev/null | cut -c1-240 | tee -a tfocus.log
  d=$(ssh "${O[@]}" $MON "grep -hoE 'OK id=$id pool sweep n=[0-9]+ dt=[0-9.]+s dir=sweep/[0-9]{6}' /home/pi/spotter_logs/SPOT-33507C/console_\$(date -u +%Y%m%d).log | head -1 | grep -oE '[0-9]{6}$'" </dev/null)
  DIRS+=("$d")
done
log "sweep dirs: ${DIRS[*]}"
python3 $DEV/tools/pool_pull.py --sweep --host bmcam003=192.168.1.230 --host bmcam004=192.168.1.143 --run bmcam003=$(ssh "${O[@]}" pi@192.168.1.230 'basename $(ls -td /home/pi/pool_runs/*/ | head -1)' </dev/null) --run bmcam004=$(ssh "${O[@]}" pi@192.168.1.143 'basename $(ls -td /home/pi/pool_runs/*/ | head -1)' </dev/null) --date 20261013_TFOCUS_pull 2>&1 | grep -E "SUMMARY|OK in|DONE|ERROR|FAIL" | tee -a tfocus.log
P=$HOME/NereusPool/20261013_TFOCUS_pull
A=(); for u in bmcam003 bmcam004; do for d in "${DIRS[@]}"; do f=$(ls -d $P/$u/sweep/$d 2>/dev/null); [ -n "$f" ] && A+=(--sweep-dir "$u=$TAG:$f"); done; done
log "sheet args: ${A[*]}"
cd ../..; /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.venv/bin/python $DEV/tools/focus_sweep_sheet.py "${A[@]}" --board-distance-m $TAG --medium in-air --run-tag tfocus_${TAG}m 2>&1 | grep -E "group|VERDICT|wrote|metric region" | cut -c1-240 | tee -a runs/pool_rehearsal_20261012/tfocus.log
