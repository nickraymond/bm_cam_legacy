#!/usr/bin/env bash
# Sprint25 S3 lean benchmark on bmcam004 via SPOT-31593C (bus always on). Cold boot = the Pi
# halts itself (real halt), THEN `bridge cfg commit` cycles bus power. Never commit while up.
set -u
IP=192.168.1.143; CMD=$HOME/spotter_logs/SPOT-31593C/cmd.txt; BR=0e582dd12c1e1480
S="ssh -o BatchMode=yes -o ConnectTimeout=4 pi@$IP"
log(){ echo "$(date -u +%FT%TZ) $*"; }
up(){ $S true >/dev/null 2>&1; }
wait_up(){ local end=$(( $(date +%s)+240 )); while [ $(date +%s) -lt $end ]; do up && { log "UP"; return 0; }; sleep 3; done; log "TIMEOUT waiting for boot"; return 1; }
wait_down(){ local end=$(( $(date +%s)+600 )) fails=0; while [ $(date +%s) -lt $end ]; do if up; then fails=0; else fails=$((fails+1)); fi; [ $fails -ge 3 ] && { log "DOWN (3 misses)"; sleep 25; return 0; }; sleep 5; done; log "TIMEOUT waiting for halt"; return 1; }
commit(){ log "bridge commit (cold boot)"; printf 'bridge cfg commit %s s\n' $BR > $CMD; while [ -s $CMD ]; do sleep 0.5; done; }
catch_and_stop(){ # boot is up: stop the cycle by PID before it transmits
  $S 'P=$(pgrep -f "rc_progressive_jpeg.py --transmit"|head -1); for i in 1 2 3 4 5 6 7 8 9 10; do [ -n "$P" ] && break; sleep 2; P=$(pgrep -f "rc_progressive_jpeg.py --transmit"|head -1); done; K=$(pgrep -P "$P"|tr "\n" " "); echo "stop pid=$P kids=[$K]"; kill -TERM $P $K 2>/dev/null; sleep 2; for q in $(pgrep -f "rpicam-vid|ffmpeg"); do kill -TERM $q; done'; }
set -e
log "arm: restore original crontab, then halt"
$S 'crontab ~/backups/crontab.before_s3_bench_20260923T232748Z && crontab -l && sudo halt' || true
wait_down
for mode in video video video; do commit; wait_up; log "boot ($mode) running armed cycle"; wait_down; done
commit; wait_up; log "switch to stills"
catch_and_stop
$S 'Y=~/BM_Devel_Pi/camera_schedule.yaml; cp $Y $Y.bak_s3_before_stills && sed -i "s/^capture_mode: \"video\"/capture_mode: \"progressive_jpeg\"/" $Y && grep -n "^capture_mode" $Y && sudo halt' || true
wait_down
for mode in stills stills; do commit; wait_up; log "boot ($mode) running armed cycle"; wait_down; done
commit; wait_up; log "final: disarm, restore video, collect"
catch_and_stop
$S 'crontab -l | sed "s|^@reboot|#S3BENCH @reboot|" | crontab - && crontab -l && cp ~/BM_Devel_Pi/camera_schedule.yaml.bak_s3_before_stills ~/BM_Devel_Pi/camera_schedule.yaml && grep -n "^capture_mode" ~/BM_Devel_Pi/camera_schedule.yaml'
log "BENCH_DONE"
