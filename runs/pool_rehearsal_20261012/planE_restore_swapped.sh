#!/bin/bash
# Plan E restore, SWAPPED rig 2026-10-13 (bmcam003 on SPOT-31593C, bmcam004 on SPOT-33507C): both units back to RC2 production overnight once nereus000 (console) is reachable.
# Per unit: pool_mode stop (wait for exit) -> pool_mode off (exit 4 expected: the pool backup is the DISARMED crontab)
# -> re-arm from ~/hil_backup/20261012T201023Z/crontab_ARMED.txt -> crontab byte-identical to the pre-pool production line
# -> halt (tuned_halt.sh) -> hil_restore_schedule.sh <SPOT> (Nick OK c). Battery read first. Output: planE_<UTC>.txt
set -u
cd "$(dirname "$0")"; RUN=$PWD
O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
WANT='@reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh'
OUT=$RUN/planE_$(date -u +%Y%m%dT%H%MZ).txt
{
echo "== $(date -u +%FT%TZ) battery (rig health)"; ssh "${O[@]}" pi@192.168.1.45 'tail -1 /home/pi/hil_health/health.csv' </dev/null | python3 -c "import csv,sys; r=next(csv.reader(sys.stdin)); print('SPOT-31593C',r[0],r[1],'V',r[2],'W in_v',r[9],'| SPOT-33507C',r[12],r[13],'V',r[14],'W in_v',r[21])"
for u in "bmcam003 192.168.1.230 SPOT-31593C" "bmcam004 192.168.1.143 SPOT-33507C"; do
  set -- $u; H=$1; IP=$2; S=$3
  echo "== $(date -u +%FT%TZ) $H: stop + off"
  ssh "${O[@]}" pi@$IP 'bash /home/pi/pool_mode.sh stop 2>&1 | tail -2; for i in $(seq 1 40); do pgrep -f "^[^ ]*python3? [^ ]*pool_sequence[.]py" >/dev/null || break; sleep 5; done; pgrep -af "[p]ool_sequence" || echo "no pool process"; bash /home/pi/pool_mode.sh off > /tmp/pool_off.log 2>&1; echo "pool_mode off exit=$?"; tail -6 /tmp/pool_off.log' </dev/null
  echo "== $H: re-arm from ~/hil_backup/20261012T201023Z"
  ssh "${O[@]}" pi@$IP 'crontab ~/hil_backup/20261012T201023Z/crontab_ARMED.txt && crontab -l' </dev/null
  GOT=$(ssh "${O[@]}" pi@$IP 'crontab -l' </dev/null)
  if [ "$GOT" != "$WANT" ]; then echo "STOP: $H crontab != production line"; echo "got: $GOT"; continue; fi
  echo "$H crontab == production (armed): OK"
  ssh "${O[@]}" pi@$IP 'cd /home/pi/BM_Devel_Pi; sha256sum camera_config.yaml camera_schedule.yaml | cut -c1-8,65-; sync; (sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 &); sleep 1; echo halt-issued' </dev/null
  for i in $(seq 1 30); do ssh "${O[@]}" pi@$IP true </dev/null 2>/dev/null || { echo "$(date -u +%T) $H down"; break; }; sleep 2; done
  sleep 15
  echo "== $(date -u +%FT%TZ) $H: restore schedule $S"
  (cd ../..; source hil/hil.env; HIL_RUN_DIR=$RUN hil/tools/hil_restore_schedule.sh $S; echo "hil_restore_schedule rc=$?")
done
} > "$OUT" 2>&1
echo "done -> $OUT"; grep -E "^==|exit=|OK|STOP|rc=|PRODUCTION|down" "$OUT"
