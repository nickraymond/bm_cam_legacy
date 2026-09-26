#!/bin/bash
# After the schedule-restore commit (which power-cycles the bus): catch bmcam003 booting
# DISARMED in the ~2 min stub window, restore its ARMED crontab from the bench backup and
# halt it cleanly, so the first aligned window starts field-normal (armed + halted).
# (S2 rearm_both.sh, one host.)
h=bmcam003; n=0
until ssh -o ConnectTimeout=3 -o BatchMode=yes pi@$h true < /dev/null 2>/dev/null; do
  n=$((n+1)); sleep 2; [ $n -gt 150 ] && { echo "[rearm] $h not reachable in 5 min"; exit 1; }; done
echo "[rearm] $h reachable $(date -u +%T)"
ssh -o BatchMode=yes pi@$h 'crontab /home/pi/s3abench/backup/crontab_ARMED.txt && echo "[rearm] crontab: $(crontab -l | grep reboot)"; cat /home/pi/BM_Devel_Pi/software_sha.txt; grep -n "  runtime:" /home/pi/BM_Devel_Pi/camera_config.yaml; nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh > /dev/null 2>&1 < /dev/null & echo "[rearm] halt issued $(date -u +%T)"' < /dev/null 2>&1
sleep 25
ssh -o ConnectTimeout=4 -o BatchMode=yes pi@$h true < /dev/null 2>/dev/null && echo "[rearm] WARNING $h still up" || echo "[rearm] $h dark $(date -u +%T)"
