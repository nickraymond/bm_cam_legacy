#!/bin/bash
# Runs ON bmcam004 (nohup) in a bus window: disarm, stop the boot cycle before it can
# halt or transmit, deploy S2 from origin, migrate to config v2, prove parity, halt.
# Cron is left DISARMED (re-armed in the stub window after the hourly-schedule commit).
set -u
R=/home/pi/repos/bm_cam_legacy; A=/home/pi/BM_Devel_Pi; B=/home/pi/s2bench/backup
echo "[m004] start $(date -u +%FT%TZ)"
crontab -l | sed -E "s%^([[:space:]]*@reboot[^#]*(rc_run_capture_cycle\.sh|rc_progressive_jpeg\.py).*)$%# DISABLED s2migrate: \1%" | crontab -
crontab -l
pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py'; pkill -TERM -f '[r]picam-vid|[f]fmpeg'
sleep 3; pgrep -af '[r]c_progressive|[r]picam|[f]fmpeg' || echo "[m004] no cycle running"
bash /tmp/rc_field_update.sh --repo $R --ref feature/sprint26-s2-settings --profile bmcam004/live_20260925 --leave-disarmed 2>&1 | grep -E "FAIL|ERROR|PARITY|parity|installed|SUMMARY|repo:|patched"
cd $A && python3 $R/tools/config_migrate_v1_v2.py --app $A 2>&1 | grep -E "^result|REFUSED|dry-run" ; \
python3 $R/tools/config_migrate_v1_v2.py --app $A --write 2>&1 | grep "^\[MIGRATE\]"
cd $R && bash tools/deploy_rc_runtime.sh --create-service-key 2>&1 | grep -E "ERROR|PARITY|parity|strict|installed|service key|cfg"
cd $A && python3 rc_progressive_jpeg.py --print-config 2>&1 | grep "^\[CFG\]"; tail -1 deploy_history.log; cat config_journal.jsonl
sha256sum camera_schedule.yaml bm_command_state.json; sha256sum $B/camera_schedule.yaml $B/bm_command_state.json
echo "[m004] done $(date -u +%FT%TZ); halting (cron stays disarmed)"
sudo -n /bin/bash $A/tuned_halt.sh > /dev/null 2>&1 < /dev/null &
