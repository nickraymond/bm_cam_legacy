#!/bin/bash
# Sprint26 S3c bench (backups in /home/pi/s3cbench): catch the unit ($1, default bmcam003) as soon as the held bus boots it; disarm cron and
# stop the boot cycle BEFORE it can halt (SIGTERM skips python's finally -> no halt;
# bmcam-field-update skill). Then back up everything the bench can touch into
# /home/pi/s3cbench/backup/ and print a survey. Bracket patterns ([r]c_...) so pkill
# does not match its own remote shell (S1 gotcha).
H=${1:-bmcam003}; TS=$(date -u +%Y%m%dT%H%M%SZ)
echo "[watcher] start $TS"
n=0
until ssh -o ConnectTimeout=3 -o BatchMode=yes pi@$H true < /dev/null 2>/dev/null; do
  n=$((n+1)); sleep 2
  [ $n -gt 450 ] && { echo "[watcher] gave up after ~15 min"; exit 1; }
done
echo "[watcher] reachable at $(date -u +%H:%M:%SZ) after $n polls"
ssh -o BatchMode=yes pi@$H "set -u
B=/home/pi/s3cbench/backup; A=/home/pi/BM_Devel_Pi; mkdir -p \$B
crontab -l > \$B/crontab_ARMED.txt 2>/dev/null
crontab -l | sed 's|^@reboot \(.*rc_run_capture_cycle\.sh\)\$|# DISABLED s3cbench $TS: @reboot \1|' | crontab -
pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py' && echo '[watcher] SIGTERM sent to cycle' || echo '[watcher] no cycle process to stop'
sleep 2
pgrep -af '[r]c_progressive|[r]c_run_capture|[r]picam|[f]fmpeg' || echo '[watcher] no camera procs'
echo '--- crontab now'; crontab -l
tar czf \$B/BM_Devel_Pi_before_s3cbench.tgz -C /home/pi --exclude=BM_Devel_Pi/images --exclude=BM_Devel_Pi/videos --exclude=BM_Devel_Pi/cron_logs --exclude=BM_Devel_Pi/sent BM_Devel_Pi 2>/dev/null
for f in camera_schedule.yaml camera_schedule.v1.yaml camera_config.yaml camera_config.lkg.json bm_command_state.json bm_command_state_v2.json bm_media_key_last.txt software_sha.txt; do [ -f \$A/\$f ] && cp -p \$A/\$f \$B/; done
ls -la \$B
echo '--- survey'; hostname; uptime; date -u
echo '--- deployed sha'; cat \$A/software_sha.txt; tail -2 \$A/deploy_history.log 2>/dev/null
echo '--- repo'; git -C /home/pi/repos/bm_cam_legacy log --oneline -1; git -C /home/pi/repos/bm_cam_legacy branch --show-current
echo '--- config'; cd \$A && python3 rc_progressive_jp[e]g.py --print-config 2>&1 | grep -E '^\[CFG\]|capture_mode|media' | head -6
echo '--- mem'; free -m | head -2
echo '--- SD (S3c gate baseline)'; df -B1 / | tail -1; du -sb \$A/images \$A/videos 2>/dev/null
echo \"natives=\$(ls \$A/images/*_native_full.jpg 2>/dev/null | wc -l) compressed=\$(ls \$A/images/*_compressed.jpg 2>/dev/null | wc -l) mp4=\$(ls \$A/videos/*.mp4 2>/dev/null | wc -l)\"
echo '--- newest clips (record quality sizes)'; ls -l --time-style=+%FT%T \$A/videos/*.mp4 2>/dev/null | tail -4
echo '--- sent records'; ls \$A/sent 2>/dev/null | wc -l
" < /dev/null 2>&1
echo "[watcher] done $(date -u +%H:%M:%SZ)"
