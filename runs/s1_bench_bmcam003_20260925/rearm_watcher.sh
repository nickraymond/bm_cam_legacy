#!/bin/bash
# After SPOT-33507C's `bridgePowerControllerEnabled 1` commit the bus power-cycles and
# bmcam003 boots (cron still DISARMED, so nothing runs) into a ~2 min stub window.
# Catch it, restore the ARMED crontab, and halt it cleanly before the stub ends, so the
# next aligned window (:00) starts from the field-normal state: armed + halted.
echo "[rearm] start $(date -u +%H:%M:%SZ)"
n=0
until ssh -o ConnectTimeout=3 -o BatchMode=yes pi@bmcam003 true < /dev/null 2>/dev/null; do
  n=$((n+1)); sleep 2; [ $n -gt 150 ] && { echo "[rearm] not reachable in 5 min"; exit 1; }
done
echo "[rearm] reachable $(date -u +%H:%M:%SZ)"
ssh -o BatchMode=yes pi@bmcam003 'crontab /home/pi/s1bench/backup/crontab_ARMED.txt && echo "[rearm] crontab: $(crontab -l | grep reboot)"; cat /home/pi/BM_Devel_Pi/software_sha.txt; nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh > /dev/null 2>&1 < /dev/null & echo "[rearm] halt issued $(date -u +%H:%M:%SZ)"' < /dev/null 2>&1
sleep 25
ssh -o ConnectTimeout=4 -o BatchMode=yes pi@bmcam003 true < /dev/null 2>/dev/null && echo "[rearm] WARNING still reachable 25 s after halt" || echo "[rearm] unit dark (halted) $(date -u +%H:%M:%SZ)"
