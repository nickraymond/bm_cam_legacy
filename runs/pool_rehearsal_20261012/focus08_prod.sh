#!/bin/bash
# Production focus lock 0.8 (Nick decision 2026-10-13 2:35 PM; needs Nick's typed OK in the TE2 chat before running).
# Indoors (Spotter rtc 0 -> no scheduled wakes), per unit on its CURRENT Spotter (swap: bmcam003<->SPOT-31593C,
# bmcam004<->SPOT-33507C), in parallel:
#  boot 1: bus forced ON via console (bridge cfg set ... bridgePowerControllerEnabled 0 + commit; Nick 10/12 OK for
#          always-on over console) with the crontab ARMED -> production cycle -> in its listen window send
#          set camera.focus.mode manual + lens_position 0.8 (console id) -> OK id=... cfg=... -> the cycle halts itself.
#  boot 2: commit again (bus power-cycle, Pi halted) -> production cycle -> in its listen window read the capture
#          sidecar (LensPosition) + the cycle log focus args -> it halts itself.
#  then hil_restore_schedule.sh <SPOT>.  Hold ~2 cycles (~16-20 min). Output: focus08_<host>_<UTC>.txt
# Usage: bash focus08_prod.sh   (ids 66004 bmcam003, 66005 bmcam004)
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..; source hil/hil.env
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=4)
CON=hil/tools/hil_console.sh
up(){ ssh "${O[@]}" pi@$1 true </dev/null 2>/dev/null; }
wait_window(){ local H=$1 n=$2; for i in $(seq 1 120); do st=$(ssh "${O[@]}" pi@$H 'f=$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1); echo "$f $(grep -c "post-transmit listen window: " $f) $(grep -c "post-transmit listen window done" $f)"' </dev/null 2>/dev/null); set -- $st; [ "${2:-0}" = 1 ] && [ "${3:-1}" = 0 ] && [ "$1" != "$n" ] && { echo "$1"; return 0; }; sleep 5; done; return 1; }
wait_down(){ for i in $(seq 1 120); do up $1 || return 0; sleep 5; done; return 1; }
commit_on(){ local S=$1 BR=$2; HIL_RUN_DIR=$RUN $CON $S "bridge cfg set $BR s u bridgePowerControllerEnabled 0" 4 | grep -E "Value|Succes"; HIL_RUN_DIR=$RUN $CON $S "bridge cfg commit $BR s" 4 | grep -E "bus power|power on for|Reboot info"; }
one(){
  H=$1; S=$2; BR=$3; ID=$4; OUT=$RUN/focus08_${H}_$(date -u +%Y%m%dT%H%MZ).txt
  {
  echo "== $(date -u +%FT%TZ) $H on $S (bridge $BR)"
  up $H && { echo "STOP: $H is up (a commit would cut it)"; return 1; }
  commit_on $S $BR; echo "boot 1: waiting for the listen window"
  L1=$(wait_window $H none) || { echo "STOP: no listen window on boot 1"; return 1; }
  echo "listen window open in $L1"
  HIL_RUN_DIR=$RUN hil/tools/hil_cmd.sh FOCUS08.$H "{\"id\":$ID,\"c\":\"set\",\"kv\":{\"camera.focus.mode\":\"manual\",\"camera.focus.lens_position\":0.8}}" 12 $S
  wait_down $H && echo "$(date -u +%T) $H halted after boot 1" || { echo "STOP: $H did not halt"; return 1; }
  sleep 10; echo "boot 2 (verify)"; HIL_RUN_DIR=$RUN $CON $S "bridge cfg commit $BR s" 4 | grep -E "bus power|power on for"
  L2=$(wait_window $H "$L1") || { echo "STOP: no listen window on boot 2"; return 1; }
  ssh "${O[@]}" pi@$H "grep -m1 '\[CFG\] config v2' $L2 | grep -o 'hash=[0-9a-f]* .*'; grep -m1 'Applying requested focus' $L2 | cut -c1-160; m=\$(ls -t /home/pi/BM_Devel_Pi/images/*capture_metadata.json | head -1); echo \$m; grep -oE '\"(LensPosition|AfState)\": *[^,]*' \$m" </dev/null
  wait_down $H && echo "$(date -u +%T) $H halted after boot 2"
  echo "== restore schedule $S"; HIL_RUN_DIR=$RUN hil/tools/hil_restore_schedule.sh $S; echo "hil_restore_schedule rc=$?"
  } > "$OUT" 2>&1
}
one bmcam003 SPOT-31593C 0e582dd12c1e1480 66004 & one bmcam004 SPOT-33507C c3c564b91856226c 66005 & wait
cat $RUN/focus08_*_*.txt | grep -vE "^\s*$" | cut -c1-200
