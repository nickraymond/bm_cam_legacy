#!/bin/bash
# Sprint26 S3c bench gate on bmcam003 (PLAN_S3c.md §2 + §5 "Revised gate"): stay_on x save_local,
# 30 min video + 30 min still, SD bounded, then one per_boot save_local wake. Run from the Mac:
#   gate.sh deploy        stage helpers + field update to the S3c branch (unit left DISARMED)
#   gate.sh hist-out      move images/ + videos/ history to /home/pi/s3cbench/history (same SD:
#                         usage unchanged, but the guards cannot prune it); lists recorded
#   gate.sh set k=v ...   set_s3c.py on the Pi (dotted v2 keys), verified by the runtime loader
#   gate.sh start         start the sampler (15 s CSV) and the wrapper exactly as cron does
#   gate.sh stop          SIGTERM the stay_on run, wait until it has exited (stay_on: no halt)
#   gate.sh status        action log tail, heartbeats, last sampler rows
#   gate.sh once          one per_boot run of the wrapper (cron line), wait for it to exit
#   gate.sh hist-back     bench media -> /home/pi/s3cbench/bench_media, history back in place
#   gate.sh restore-cfg   camera_config.yaml back from the bench backup, verified
#   gate.sh heal stop|start   bm-heal-driver on nereus000 (state.json backed up on stop)
#   gate.sh pull          copy sampler.csv, action log, the run's cron logs, lists into ./pulled
# Everything the Pi writes lives under /home/pi/s3cbench; the backups came from watcher_s3c.sh.
set -u
H=pi@bmcam003; N=pi@192.168.1.45
A=/home/pi/BM_Devel_Pi; B=/home/pi/s3cbench
HERE="$(cd "$(dirname "$0")" && pwd)"
REF=feature/sprint26-s3c-save-local
SSH="ssh -o BatchMode=yes -o ConnectTimeout=6"
log() { echo "$(date -u +%FT%TZ) gate $*" | tee -a "$HERE/gate.log"; }

case "${1:-}" in
deploy)
  log "deploy $REF"
  scp -q "$HERE/set_s3c.py" "$HERE/sampler.sh" $H:$B/ || exit 1
  $SSH $H "cd /home/pi/repos/bm_cam_legacy && timeout 90 git fetch -q origin $REF && bash tools/rc_field_update.sh --ref $REF --profile bmcam003/live_20260925 --leave-disarmed" < /dev/null > "$HERE/bmcam003_deploy_s3c.log" 2>&1
  rc=$?
  grep -E "PARITY|parity OK|SUMMARY|sha:|effective hash|save_local" "$HERE/bmcam003_deploy_s3c.log" | cut -c1-170 | tee -a "$HERE/gate.log"
  [ $rc -eq 0 ] && grep -q "SUMMARY: PASS" "$HERE/bmcam003_deploy_s3c.log" || { log "DEPLOY FAILED rc=$rc"; exit 1; }
  $SSH $H "crontab -l | grep reboot; cat $A/software_sha.txt" < /dev/null | tee -a "$HERE/gate.log" ;;
hist-out)
  $SSH $H "set -e; mkdir -p $B/history/images $B/history/videos
    ls -la $A/images > $B/history/images.before.txt; ls -la $A/videos > $B/history/videos.before.txt
    find $A/images -maxdepth 1 -type f -exec mv -t $B/history/images {} +
    find $A/videos -maxdepth 1 -type f -exec mv -t $B/history/videos {} +
    echo moved images=\$(ls $B/history/images | wc -l) videos=\$(ls $B/history/videos | wc -l)
    ls $A/images $A/videos | head; df -B1 / | tail -1" < /dev/null | tee -a "$HERE/gate.log" ;;
set)
  shift; $SSH $H "cd $B && python3 set_s3c.py $*" < /dev/null | tee -a "$HERE/gate.log"
  exit ${PIPESTATUS[0]} ;;
start)
  log "start sampler + wrapper"
  $SSH $H "pgrep -x -f 'bash sampler.sh' >/dev/null || (cd $B && nohup setsid bash sampler.sh </dev/null >/dev/null 2>&1 &)
    nohup setsid /usr/bin/flock -n /tmp/bmcam_rc_capture.lock $A/rc_run_capture_cycle.sh >/dev/null 2>&1 </dev/null &
    sleep 8; pgrep -af '[r]c_progressive_jp[e]g|^bash sampler.s[h]' | cut -c1-150" < /dev/null | tee -a "$HERE/gate.log" ;;
stop)
  log "stop (SIGTERM)"
  $SSH $H "pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jp[e]g.py'
    for i in \$(seq 1 72); do pgrep -f '[r]c_progressive_jp[e]g.py' >/dev/null || break; sleep 5; done
    pgrep -af '[r]c_progressive_jp[e]g|[r]c_run_capture' && echo STILL-RUNNING || echo stopped
    L=\$(ls -t $A/cron_logs/rc_cycle_*.log | head -1); grep -E 'stay_on exit|stop requested' \$L | tail -2" < /dev/null | tee -a "$HERE/gate.log" ;;
status)
  $SSH $H "L=\$(ls -t $A/cron_logs/rc_cycle_*.log | head -1); echo \$L
    grep -E '===== action|heartbeat|STORE|RING|saved \(save_local|ERR|Traceback|exit' \$L | cut -c1-160 | tail -${2:-12}
    echo '--- actions'; tail -n 3 $A/cron_logs/supervisor_actions.jsonl | cut -c1-260
    echo '--- sampler'; tail -n 3 $B/sampler.csv" < /dev/null ;;
once)
  log "one per_boot run (cron line)"
  $SSH $H "/usr/bin/flock -n /tmp/bmcam_rc_capture.lock $A/rc_run_capture_cycle.sh </dev/null >/dev/null 2>&1; echo wrapper exit \$?
    L=\$(ls -t $A/cron_logs/rc_cycle_*.log | head -1); echo \$L
    grep -E 'RUNTIME|OUTPUT|per_boot|saved|a=saved|WS|tail|halt|action:|ERR' \$L | cut -c1-170 | tail -25
    tail -n 1 $A/cron_logs/supervisor_actions.jsonl" < /dev/null | tee -a "$HERE/gate.log" ;;
hist-back)
  $SSH $H "set -e; mkdir -p $B/bench_media/images $B/bench_media/videos
    find $A/images -maxdepth 1 -type f -exec mv -t $B/bench_media/images {} +
    find $A/videos -maxdepth 1 -type f -exec mv -t $B/bench_media/videos {} +
    find $B/history/images -maxdepth 1 -type f -exec mv -t $A/images {} +
    find $B/history/videos -maxdepth 1 -type f -exec mv -t $A/videos {} +
    ls -la $A/images > $B/history/images.after.txt; ls -la $A/videos > $B/history/videos.after.txt
    echo images: \$(diff <(awk '{print \$9}' $B/history/images.before.txt) <(awk '{print \$9}' $B/history/images.after.txt) | wc -l) diff lines
    echo videos: \$(diff <(awk '{print \$9}' $B/history/videos.before.txt) <(awk '{print \$9}' $B/history/videos.after.txt) | wc -l) diff lines
    echo bench_media images=\$(ls $B/bench_media/images | wc -l) videos=\$(ls $B/bench_media/videos | wc -l)" < /dev/null | tee -a "$HERE/gate.log" ;;
restore-cfg)
  $SSH $H "cp -p $B/backup/camera_config.yaml $A/camera_config.yaml && cd $A && python3 -c \"
import config_v2, rc_progressive_jpeg as rc
b = config_v2.load_for_boot('$A/camera_schedule.yaml', '$A/camera_config.yaml', '$A/camera_config.lkg.json')
r = rc.resolve_runtime(None, b)[0]
print('[restore] level', b.level, 'hash', config_v2.config_hash(b.values), 'runtime', r, 'run', rc.resolve_run_mode(b, r), 'output', rc.resolve_output(b, r))\"" < /dev/null | tee -a "$HERE/gate.log" ;;
heal)
  if [ "${2:-}" = stop ]; then
    $SSH $N "sudo systemctl stop bm-heal-driver && cp -p /home/pi/spotter_logs/heal_driver/state.json /home/pi/spotter_logs/heal_driver/state.json.s3cbench && systemctl is-active bm-heal-driver" < /dev/null | tee -a "$HERE/gate.log"
  else
    $SSH $N "cmp /home/pi/spotter_logs/heal_driver/state.json /home/pi/spotter_logs/heal_driver/state.json.s3cbench && echo state unchanged; sudo systemctl start bm-heal-driver; systemctl is-active bm-heal-driver" < /dev/null | tee -a "$HERE/gate.log"
  fi ;;
pull)
  mkdir -p "$HERE/pulled"
  scp -q $H:$B/sampler.csv $H:$A/cron_logs/supervisor_actions.jsonl "$HERE/pulled/"
  $SSH $H "ls -t $A/cron_logs/rc_cycle_*.log* | head -${2:-6}" < /dev/null | while read -r f; do scp -q "$H:$f" "$HERE/pulled/"; done
  scp -q "$H:$B/history/*.txt" "$HERE/pulled/" 2>/dev/null
  ls -la "$HERE/pulled" ;;
*) sed -n 2,20p "$0"; exit 2 ;;
esac
