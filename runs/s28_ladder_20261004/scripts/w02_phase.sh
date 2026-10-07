#!/bin/bash
# bmcam004 02:00Z window: re-phase SPOT-31593C to ~:03:40 (lane block already ON since 00:00:43Z).
# Hardened after two failures (2026-10-07): every ssh bounded (perl alarm + ServerAlive), bracket pkill patterns,
# cron is NEVER disarmed here (only the running cycle is stopped), halt retried, hard deadline 02:02:20 to be dark —
# on any failure the unit is left HALTED + cron ARMED (or, worst case, up + armed for the :10 bus cut) and no commit.
# DRY=1 rehearses everything except the halt (echoed) and the bridge set/commit (echoed).
set -u
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c
export HIL_RUN_DIR=runs/s28_ladder_20261004; G=$HIL_RUN_DIR/gate.log; H=bmcam004; DRY="${DRY:-0}"; [ "$DRY" = 1 ] && H="${REHEARSE_HOST:-bmcam004}"
log() { echo "$(date -u +%FT%TZ) [w02 phase${DRY:+ DRY=$DRY}] $*" | tee -a $G; }
B() { perl -e 'alarm shift; exec @ARGV' "$@"; }
S() { B "${SB:-15}" ssh -o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=2 pi@$H "$@" < /dev/null; }
up() { SB=8 S true 2>/dev/null; }
ep() { python3 -c "import datetime,sys;print(int(datetime.datetime(2026,10,7,${HOUR:-2},int(sys.argv[1]),int(sys.argv[2]),tzinfo=datetime.timezone.utc).timestamp()))" "$1" "$2"; }
T=$(ep 0 0); T=$((T - 10)); [ "$DRY" = 1 ] && T=$(date +%s)
until [ "$(date +%s)" -ge "$T" ]; do sleep 5; done
t0=$(date +%s); for i in $(seq 1 45); do up && break; sleep 2; done
up || { log "ABORT: $H not up (waited $(( $(date +%s) - t0 )) s); nothing changed"; exit 1; }
log "caught boot in $(( $(date +%s) - t0 )) s"
t1=$(date +%s)
S "pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py|[m]ain_pi_camera.py'; sleep 1; pgrep -af '[r]c_run_capture_cycle|[r]c_progressive_jpeg' || echo no-cycle; crontab -l | grep -cE '^@reboot.*[r]c_run_capture_cycle' | sed 's/^/armed_lines=/'" 2>&1 | tee -a $G
log "stop-cycle step returned in $(( $(date +%s) - t1 )) s (rc ${PIPESTATUS[0]})"
halt() { if [ "$DRY" = 1 ]; then echo "[DRY] would halt $H"; else S 'nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null & echo halt-issued'; fi; }
DL=$(ep 2 20); [ "$DRY" = 1 ] && DL=$(( $(date +%s) + 40 ))
for attempt in 1 2; do
  t2=$(date +%s); halt 2>&1 | tee -a $G
  [ "$DRY" = 1 ] && { log "DRY: halt step returned in $(( $(date +%s) - t2 )) s; stopping before the commit (Pi left up, cron armed)"; exit 0; }
  while [ "$(date +%s)" -lt "$DL" ]; do sleep 3; up || break; done
  up || break
  log "still up after halt attempt $attempt"
done
up && { log "ABORT: $H still up at the 02:02:20 deadline -> NO commit (cron armed; the :10 bus cut powers it off)"; exit 2; }
log "$H dark at $(date -u +%T) (cron armed); handing over to hil_bridge_phase (commit 02:03:32)"
HIL_PHASE_LEAD_S=28 hil/tools/hil_bridge_phase.sh SPOT-31593C ticks 04 2>&1 | tee $HIL_RUN_DIR/phase_0403.log | tail -10
log "hil_bridge_phase rc=${PIPESTATUS[0]} (windows expected ~:03:40 from 03:03:40Z)"
