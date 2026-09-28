#!/bin/bash
# restore_schedule.sh [host] [bridge] [SPOT] — S5 restore: the unit must be HALTED and DISARMED.
# 1. bridgePowerControllerEnabled 1 + commit (the bridge resets; the bus runs a ~2 min stub
#    window, then the aligned 10-min windows at :00; memory "bridge commit short first window")
# 2. the stub boots the Pi disarmed: re-arm from the watcher's crontab_ARMED.txt and halt it
#    cleanly before the stub ends, so the next aligned window runs field-normal.
# 3. read back the bridge config.
H=${1:-bmcam003}; BR=${2:-c3c564b91856226c}; SPOT=${3:-SPOT-33507C}
HERE="$(cd "$(dirname "$0")" && pwd)"
if ssh -o BatchMode=yes -o ConnectTimeout=4 pi@$H true </dev/null 2>/dev/null; then
  echo "[restore] $H is UP: refusing to commit (halt it first)"; exit 2
fi
echo "$(date -u +%FT%TZ) restore schedule on $SPOT ($BR)" | tee -a "$HERE/gate.log"
"$HERE/console.sh" "$SPOT" "bridge cfg set $BR s u bridgePowerControllerEnabled 1" 4 | grep -E "Value|Succes" | tee -a "$HERE/gate.log"
"$HERE/console.sh" "$SPOT" "bridge cfg commit $BR s" 4 | grep -E "Reboot info|bus power|power on for" | tee -a "$HERE/gate.log"
T0=$(date -u +%s)
until ssh -o BatchMode=yes -o ConnectTimeout=3 pi@$H true </dev/null 2>/dev/null; do
  sleep 2; [ $(( $(date -u +%s) - T0 )) -gt 110 ] && { echo "[restore] $H not up in the stub window"; break; }
done
ssh -o BatchMode=yes pi@$H 'crontab /home/pi/s5bench/backup/crontab_ARMED.txt && echo "[restore] crontab: $(crontab -l | grep reboot)"; pgrep -af "[r]c_progressive_jp[e]g" || echo "[restore] no cycle running"; cat /home/pi/BM_Devel_Pi/software_sha.txt; nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null & echo "[restore] halt issued $(date -u +%T)"' </dev/null 2>&1 | tee -a "$HERE/gate.log"
sleep 20
ssh -o BatchMode=yes -o ConnectTimeout=4 pi@$H true </dev/null 2>/dev/null && echo "[restore] WARNING $H still up" || echo "[restore] $H dark $(date -u +%T)" | tee -a "$HERE/gate.log"
for k in bridgePowerControllerEnabled sampleIntervalMs sampleDurationMs; do
  "$HERE/console.sh" "$SPOT" "bridge cfg get $BR s $k" 3 | grep -E "Value" | sed "s/^/[$k] /" | tee -a "$HERE/gate.log"
done
