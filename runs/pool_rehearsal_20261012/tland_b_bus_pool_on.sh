#!/bin/bash
# T-LAND step B (both units, after the 1 PM wake halts + bus-off ~20:10Z): per unit IN PARALLEL:
# hil_bus_always_on.sh (Nick OK c) -> snapshot (bus-on, disarmed) -> pool_mode.sh on + start (Nick OK d, f8caaa1 code).
# Then pool_cmd.py both status (Nick OK b). Output: tland_b_<unit>_<UTC>.txt + tland_b_status.txt
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..; source hil/hil.env
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,12,20,10,15,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
one() {
  H=$1; S=$2; OUT=$RUN/tland_b_${H}_$(date -u +%Y%m%dT%H%MZ).txt
  {
  for i in $(seq 1 30); do ssh "${O[@]}" pi@$H true </dev/null 2>/dev/null || break; sleep 10; done
  echo "== $(date -u +%FT%TZ) $H halted; bus hold on $S"
  HIL_RUN_DIR=$RUN hil/tools/hil_bus_always_on.sh $S; rc=$?; echo "hil_bus_always_on rc=$rc"
  [ $rc = 0 ] || { echo "STOP: bus hold failed on $H"; return 1; }
  ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; echo "== BEFORE-POOL-ON"; crontab -l; sha256sum camera_config.yaml camera_schedule.yaml bm_command_state_v2.json; sha256sum pool_sequence.py rc_preview.py /home/pi/pool_mode.sh | cut -c1-12,65-; echo "== pool_mode on"; bash /home/pi/pool_mode.sh on 2>&1 | tail -8; echo "== start"; bash /home/pi/pool_mode.sh start 2>&1 | tail -3; sleep 8; bash /home/pi/pool_mode.sh status 2>&1 | tail -6' </dev/null
  } > "$OUT" 2>&1
}
one bmcam003 SPOT-33507C & one bmcam004 SPOT-31593C & wait
ssh "${O[@]}" pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both status --wait 30' </dev/null > $RUN/tland_b_status.txt 2>&1
cat $RUN/tland_b_*_*.txt | grep -E "^==|rc=|STOP|POOL MODE|pool process|newest run|cfg=|ERROR" ; cat $RUN/tland_b_status.txt | grep -E "OK id|ERR"
