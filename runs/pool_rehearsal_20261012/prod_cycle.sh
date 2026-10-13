#!/bin/bash
# One PRODUCTION capture + cellular transmit cycle per unit (Nick OK 2026-10-13 ~3:18 PM), indoors (rtc 0) via the held
# bus: units HALTED + crontab ARMED, bus controller already 0 -> `bridge cfg commit` power-cycles the bus -> the Pi boots
# its armed per_boot cycle (capture -> nrjxl -> transmit -> listen -> real halt). Then hil_restore_schedule.sh.
# SWAPPED pairing: bmcam003<->SPOT-31593C (bridge 0e582dd12c1e1480), bmcam004<->SPOT-33507C (bridge c3c564b91856226c).
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..; source hil/hil.env
grep -qE '^export HIL_RIG_A="SPOT-33507C=BMCAM_004=bmcam004=' hil/hil.env || { echo "PAIRING WRONG"; exit 1; }
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=4)
up(){ ssh "${O[@]}" pi@$1 true </dev/null 2>/dev/null; }
one(){
  H=$1; S=$2; BR=$3; OUT=$RUN/prodcycle_${H}_$(date -u +%Y%m%dT%H%MZ).txt
  {
  up $H && { echo "STOP: $H up"; return 1; }
  T0=$(date -u +%FT%TZ); echo "== $T0 $H on $S: commit (bus power-cycle -> production boot)"
  HIL_RUN_DIR=$RUN hil/tools/hil_console.sh $S "bridge cfg commit $BR s" 4 | grep -E "bus power|power on for"
  for i in $(seq 1 60); do up $H && break; sleep 3; done; echo "$(date -u +%T) $H up"
  sleep 20; for i in $(seq 1 150); do up $H || break; sleep 5; done; T1=$(date -u +%FT%TZ); echo "$T1 $H halted"
  echo "T0=$T0 T1=$T1"
  } > "$OUT" 2>&1
}
one bmcam003 SPOT-31593C 0e582dd12c1e1480 & one bmcam004 SPOT-33507C c3c564b91856226c & wait
cat $RUN/prodcycle_*_*.txt | cut -c1-160
