#!/usr/bin/env bash
# pull.sh — Sprint26 S2: READ-ONLY pull of live config from bench units.
# Polls each host until it answers SSH (scheduled bus), then copies config +
# command state + provenance. Changes NOTHING on the unit (cat/ls/crontab -l only).
# Usage: bash pull.sh <deadline_epoch> host...
# Output: <host>/ with the files, pull.log with timestamps.
set -u
OUT="$(cd "$(dirname "$0")" && pwd)"
DEADLINE="$1"; shift
PENDING=("$@")
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/pull.log"; }
SSH="ssh -o ConnectTimeout=4 -o BatchMode=yes"
log "start hosts=${PENDING[*]} deadline=$(date -u -r "$DEADLINE" +%TZ)"
while [[ ${#PENDING[@]} -gt 0 && $(date +%s) -lt $DEADLINE ]]; do
  NEXT=()
  for h in "${PENDING[@]}"; do
    if $SSH pi@$h true 2>/dev/null; then
      log "$h reachable; pulling"
      d="$OUT/$h"; mkdir -p "$d"
      ok=1
      for f in camera_schedule.yaml bm_command_state.json software_sha.txt; do
        if $SSH pi@$h "cat /home/pi/BM_Devel_Pi/$f" > "$d/$f" 2>"$d/$f.err"; then rm -f "$d/$f.err"
        else log "$h: $f missing/unreadable: $(cat "$d/$f.err")"; rm -f "$d/$f"; fi
      done
      $SSH pi@$h 'hostname; date -u +%FT%TZ; uptime; echo "--- crontab"; crontab -l 2>&1;
        echo "--- runtime dir (yaml/json/txt)"; ls -la /home/pi/BM_Devel_Pi/*.yaml /home/pi/BM_Devel_Pi/*.json /home/pi/BM_Devel_Pi/*.txt 2>&1;
        echo "--- sha256"; cd /home/pi/BM_Devel_Pi && sha256sum camera_schedule.yaml bm_command_state.json 2>&1;
        echo "--- deploy_history tail"; tail -5 deploy_history.log 2>&1;
        echo "--- python/yaml"; python3 -c "import sys,yaml;print(sys.version.split()[0],yaml.__version__)" 2>&1;
        echo "--- repo"; git -C /home/pi/repos/bm_cam_legacy log --oneline -1 2>&1; git -C /home/pi/repos/bm_cam_legacy rev-parse --abbrev-ref HEAD 2>&1;
        echo "--- camera procs"; pgrep -af "[r]c_progressive_jpeg|[r]c_run_capture" 2>&1' > "$d/survey.txt" 2>&1 || ok=0
      [[ -s "$d/camera_schedule.yaml" ]] || ok=0
      if [[ $ok == 1 ]]; then log "$h DONE"; else log "$h incomplete; retry"; NEXT+=("$h"); fi
    else NEXT+=("$h"); fi
  done
  PENDING=("${NEXT[@]+"${NEXT[@]}"}")
  [[ ${#PENDING[@]} -gt 0 ]] && sleep 5
done
log "end; not reached: ${PENDING[*]:-none}"
[[ ${#PENDING[@]} -eq 0 ]]
