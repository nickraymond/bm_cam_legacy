#!/bin/bash
# Catch bmcam003 awake after the bus comes on; disarm cron + stop the cycle BEFORE it can halt.
# SIGTERM kills python without running `finally`, so power_halt never fires (bmcam-field-update skill).
TS=$(date -u +%Y%m%dT%H%M%SZ)
echo "[watcher] start $TS"
n=0
until ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=accept-new pi@bmcam003 true 2>/dev/null; do
  n=$((n+1)); sleep 2
  [ $n -gt 900 ] && { echo "[watcher] gave up after 30 min"; exit 1; }
done
echo "[watcher] reachable at $(date -u +%H:%M:%SZ) after $n polls"
ssh -o BatchMode=yes pi@bmcam003 "set -u
crontab -l > /home/pi/crontab_backup_s1bench_$TS.txt 2>/dev/null
crontab -l | sed 's|^@reboot \(.*rc_run_capture_cycle\.sh\)\$|# DISABLED s1bench $TS: @reboot \1|; s|^@reboot \(.*/run_capture_cycle\.sh\)\$|# DISABLED s1bench $TS: @reboot \1|' | crontab -
pkill -TERM -f 'rc_run_capture_cycle.sh|rc_progressive_jpeg.py|main_pi_camera.py' && echo '[watcher] SIGTERM sent to cycle' || echo '[watcher] no cycle process to stop'
sleep 2
echo '--- survey'; hostname; uptime; date -u
echo '--- crontab (backup /home/pi/crontab_backup_s1bench_$TS.txt)'; crontab -l
echo '--- camera procs'; pgrep -af 'rc_progressive|rc_run_capture|main_pi_camera|rpicam|ffmpeg' || echo none
echo '--- deployed sha'; cat /home/pi/BM_Devel_Pi/software_sha.txt 2>/dev/null; tail -2 /home/pi/BM_Devel_Pi/deploy_history.log 2>/dev/null
echo '--- repo'; git -C /home/pi/repos/bm_cam_legacy log --oneline -1 2>&1; git -C /home/pi/repos/bm_cam_legacy status --short | head -5
echo '--- yaml keys'; grep -nE '^capture_mode|^video_tx:|^media_key:|^power_halt:|^bm_commands:|^transmit_phase:|^  enabled|^  dry_run|image_buffer_size|image_transmit_delay' /home/pi/BM_Devel_Pi/camera_schedule.yaml
echo '--- python deps'; python3 -c 'import yaml, PIL, serial; print(\"yaml\", yaml.__version__, \"PIL\", PIL.__version__, \"serial\", serial.__version__)'; python3 -V; which ffmpeg rpicam-still
echo '--- mem'; free -m | head -2
echo '--- last cron log'; ls -t /home/pi/BM_Devel_Pi/cron_logs | head -2
" 2>&1
echo "[watcher] done $(date -u +%H:%M:%SZ)"
