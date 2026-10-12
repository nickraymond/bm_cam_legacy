#!/bin/bash
# T-DRY step B (bmcam004 / SPOT-31593C), after the 12 PM wake has halted and the bus is off (~19:11Z):
# (1) hil_bus_always_on.sh (Nick OK c): bus held on, Pi caught awake, @reboot RC line disarmed (backup), cycle SIGTERMed;
# (2) snapshot the bus-on state = the "before" for the T-DRY pool_mode off check;
# (3) pool_mode.sh on (Nick OK d) + start; status.  Output: tdry_b_<UTC>.txt (+ gate.log lines from the hil script)
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..
H=bmcam004; S=SPOT-31593C; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
OUT=$RUN/tdry_b_$(date -u +%Y%m%dT%H%MZ).txt
{
echo "== $(date -u +%FT%TZ) bus hold on $S"
source hil/hil.env
HIL_RUN_DIR=$RUN hil/tools/hil_bus_always_on.sh $S; rc=$?
echo "hil_bus_always_on rc=$rc"
[ $rc = 0 ] || { echo "STOP: bus hold failed"; exit 1; }
echo "== $(date -u +%FT%TZ) BEFORE-POOL-ON snapshot (bus-on, disarmed)"
ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; crontab -l; sha256sum camera_config.yaml camera_schedule.yaml bm_command_state_v2.json 2>/dev/null; pgrep -af "[r]c_run_capture|[r]c_progressive|[p]ool_sequence" || echo no-cycle' </dev/null
echo "== $(date -u +%FT%TZ) pool_mode on + start"
ssh "${O[@]}" pi@$H 'bash /home/pi/pool_mode.sh on 2>&1 | tail -15; bash /home/pi/pool_mode.sh start 2>&1 | tail -5; sleep 8; bash /home/pi/pool_mode.sh status 2>&1 | tail -15' </dev/null
} > "$OUT" 2>&1
echo "done -> $OUT"; tail -25 "$OUT"
