#!/bin/bash
# S4a soak: catch bmcam003 at its hourly wake, disarm cron (ARMED crontab backed up
# first: it is the re-arm file), SIGTERM the boot cycle (no finally -> no halt), back up
# config + state. Bracket patterns so pkill never matches its own remote shell.
H=${1:-bmcam003}; TS=$(date -u +%Y%m%dT%H%M%SZ)
echo "[watcher] start $TS"
n=0
until ssh -o ConnectTimeout=3 -o BatchMode=yes pi@$H true < /dev/null 2>/dev/null; do
  n=$((n+1)); sleep 2; [ $n -gt 600 ] && { echo "[watcher] gave up"; exit 1; }
done
echo "[watcher] reachable at $(date -u +%H:%M:%SZ) after $n polls"
ssh -o BatchMode=yes pi@$H "set -u
B=/home/pi/s4asoak/backup; A=/home/pi/BM_Devel_Pi; mkdir -p \$B
crontab -l > \$B/crontab_ARMED.txt 2>/dev/null
crontab -l | sed 's|^@reboot \(.*rc_run_capture_cycle\.sh\)\$|# DISABLED s4asoak $TS: @reboot \1|' | crontab -
pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jp[e]g.py' && echo '[watcher] SIGTERM sent to cycle' || echo '[watcher] no cycle process'
sleep 2
pgrep -af '[r]c_progressive|[r]c_run_capture|[r]picam|[f]fmpeg' || echo '[watcher] no camera procs'
echo '--- crontab now'; crontab -l | grep -n reboot
for f in camera_schedule.yaml camera_schedule.v1.yaml camera_config.yaml camera_config.lkg.json bm_command_state_v2.json config_journal.jsonl software_sha.txt; do [ -f \$A/\$f ] && cp -p \$A/\$f \$B/; done
ls -la \$B
echo '--- survey'; hostname; uptime; date -u
echo '--- deployed sha'; cat \$A/software_sha.txt
echo '--- repo'; git -C /home/pi/repos/bm_cam_legacy log --oneline -1; git -C /home/pi/repos/bm_cam_legacy status --short | head
echo '--- config'; cd \$A && python3 rc_progressive_jp[e]g.py --print-config 2>&1 | grep -E '^\[CFG\]' | head -6
echo '--- SD'; df -h / | tail -1
" < /dev/null 2>&1
echo "[watcher] done $(date -u +%H:%M:%SZ)"
