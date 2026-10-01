#!/bin/bash
# wake.sh [host] [bridge] [SPOT] — S5: boot a HALTED unit on a HELD bus by re-committing the
# bridge config (a commit resets the bridge and power-cycles the bus; S3c F2), then wait
# until the unit's supervisor has subscribed to bmcam/cmd and print its uptime.
# ONLY with the unit halted (a commit cuts a running Pi).
# Exit 0 once "[CMD] subscribed" is in the newest cycle log; 1 on timeout.
H=${1:-bmcam003}; BR=${2:-c3c564b91856226c}; SPOT=${3:-SPOT-33507C}
HERE="$(cd "$(dirname "$0")" && pwd)"
if ssh -o BatchMode=yes -o ConnectTimeout=4 pi@$H true </dev/null 2>/dev/null; then
  echo "[wake] $H is UP: refusing to power-cycle the bus"; exit 2
fi
echo "$(date -u +%FT%TZ) wake $H via bridge commit $BR" | tee -a "$HERE/gate.log"
"$HERE/console.sh" "$SPOT" "bridge cfg commit $BR s" 4 | grep -E "Reboot info|bus power|power on for" | tee -a "$HERE/gate.log"
for i in $(seq 1 90); do
  out=$(ssh -o BatchMode=yes -o ConnectTimeout=3 pi@$H 'L=$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log 2>/dev/null | head -1); u=$(cut -d" " -f1 /proc/uptime); grep -q "\[CMD\] subscribed" "$L" && echo "SUB $u $L"' </dev/null 2>/dev/null)
  case "$out" in SUB*) echo "[wake] $(date -u +%T) $out" | tee -a "$HERE/gate.log"; exit 0;; esac
  sleep 1
done
echo "[wake] timeout waiting for subscribe"; exit 1
