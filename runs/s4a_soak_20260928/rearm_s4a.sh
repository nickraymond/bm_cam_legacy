#!/bin/bash
# S4a soak: restore bmcam003's ARMED crontab (the watcher's backup) and halt it cleanly
# before the scheduled bus cut, so the next aligned wake runs field-normal.
h=${1:-bmcam003}
ssh -o BatchMode=yes pi@$h 'crontab /home/pi/s4asoak/backup/crontab_ARMED.txt && echo "[rearm] crontab: $(crontab -l | grep reboot)"; cat /home/pi/BM_Devel_Pi/software_sha.txt; nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh > /dev/null 2>&1 < /dev/null & echo "[rearm] halt issued $(date -u +%T)"' < /dev/null 2>&1
sleep 25
ssh -o ConnectTimeout=4 -o BatchMode=yes pi@$h true < /dev/null 2>/dev/null && echo "[rearm] WARNING $h still up" || echo "[rearm] $h dark $(date -u +%T)"
