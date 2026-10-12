#!/bin/bash
# T-ENDURE pull (EM call (b) 2026-10-12): full parallel pool_pull of both units' T-ENDURE runs to ~/NereusPool/20261012_TENDURE/
# with a Mac free-space guard: if free space < 5 GB, SIGTERM the pull and report the rate so far.
# Output: pull_console.log (pool_pull output), pull_guard.log; pool_pull's own pull_report.json + pull.log in the dest.
set -u
cd "$(dirname "$0")"
PP=/private/tmp/claude-501/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy--claude-worktrees-sprint26-s3a-runtime-parity-092958/3cd7e446-c576-4dd7-8494-833abcb29a17/scratchpad/dev_48e08df/tools/pool_pull.py
caffeinate -i -w $$ &
free_gb(){ df -k ~ | tail -1 | awk '{printf "%.2f", $4/1048576}'; }
echo "$(date -u +%FT%TZ) start; Mac free $(free_gb) GB" | tee -a pull_guard.log
T0=$(date +%s)
python3 "$PP" --host bmcam003=192.168.1.230 --host bmcam004=192.168.1.143 \
  --run bmcam003=20261012_201138_bmcam003 --run bmcam004=20261012_201130_bmcam004 \
  --dest-root ~/NereusPool --date 20261012_TENDURE > pull_console.log 2>&1 < /dev/null &
PID=$!
while kill -0 $PID 2>/dev/null; do
  f=$(free_gb); echo "$(date -u +%T) free ${f} GB pulled $(du -sk ~/NereusPool/20261012_TENDURE 2>/dev/null | cut -f1) KiB" >> pull_guard.log
  if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) < 5.0 else 1)" "$f"; then
    echo "$(date -u +%FT%TZ) GUARD: free ${f} GB < 5 GB -> stopping the pull" | tee -a pull_guard.log; pkill -TERM -P $PID; kill -TERM $PID; sleep 5; pkill -KILL -P $PID; kill -KILL $PID 2>/dev/null; break; fi
  sleep 15
done
wait $PID; rc=$?
echo "$(date -u +%FT%TZ) pull exit rc=$rc after $(( $(date +%s) - T0 )) s; Mac free $(free_gb) GB; pulled $(du -sh ~/NereusPool/20261012_TENDURE 2>/dev/null | cut -f1)" | tee -a pull_guard.log
