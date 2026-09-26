#!/bin/bash
# After the 30-min schedule commit: catch each Pi booting (disarmed) in the ~2 min stub
# window, restore its ARMED crontab from /home/pi/s2bench/backup/crontab_ARMED.txt, and halt
# cleanly, so the first aligned window starts field-normal (armed + halted).
rearm() {
  local h=$1 n=0
  until ssh -o ConnectTimeout=3 -o BatchMode=yes pi@$h true < /dev/null 2>/dev/null; do
    n=$((n+1)); sleep 2; [ $n -gt 150 ] && { echo "[rearm] $h not reachable in 5 min"; return 1; }; done
  echo "[rearm] $h reachable $(date -u +%T)"
  ssh -o BatchMode=yes pi@$h 'crontab /home/pi/s2bench/backup/crontab_ARMED.txt && echo "[rearm] $(hostname) crontab: $(crontab -l | grep reboot)"; cat /home/pi/BM_Devel_Pi/software_sha.txt; nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh > /dev/null 2>&1 < /dev/null & echo "[rearm] $(hostname) halt issued $(date -u +%T)"' < /dev/null 2>&1
  sleep 25
  ssh -o ConnectTimeout=4 -o BatchMode=yes pi@$h true < /dev/null 2>/dev/null && echo "[rearm] WARNING $h still up" || echo "[rearm] $h dark $(date -u +%T)"
}
rearm bmcam003 & rearm bmcam004 & wait
