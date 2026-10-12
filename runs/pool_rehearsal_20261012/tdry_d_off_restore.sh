#!/bin/bash
# T-DRY step D + T-RESTORE (bmcam004 / SPOT-31593C):
# D: pull the newest pool run folder's manifest/logs + listing (read-only), pool_mode stop, pool_mode off (Nick OK d),
#    verify crontab + config/state sha256 == the step-B "before pool on" snapshot.
# E (EM 12:3x PM: restore to production after T-DRY): re-arm the crontab from the hil_bus_always_on backup, halt the Pi
#    (tuned_halt.sh), hil_restore_schedule.sh (Nick OK c), then compare with the step-A pre-pool snapshot.
# Output: tdry_d_<UTC>.txt, pulled/ (manifest + logs)
set -u
cd "$(dirname "$0")"; RUN=$PWD
H=bmcam004; S=SPOT-31593C; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
OUT=$RUN/tdry_d_$(date -u +%Y%m%dT%H%MZ).txt; mkdir -p pulled
TS=$(grep -oE "hil_backup/[0-9TZ]+/crontab_ARMED" gate.log | tail -1 | cut -d/ -f2)
{
echo "== $(date -u +%FT%TZ) D: pull run folder (read-only); ARMED backup TS=$TS"
R=$(ssh "${O[@]}" pi@$H 'ls -td /home/pi/pool_runs/*/ | head -1' </dev/null); echo "run=$R"
ssh "${O[@]}" pi@$H "cd $R && find . -type f -printf '%s\t%p\n' | sort -k2" </dev/null
scp -q "${O[@]}" "pi@$H:${R}run_manifest.json" "pi@$H:${R}pool.log" "pi@$H:${R}console.log" pulled/ 2>&1
echo "== $(date -u +%FT%TZ) D: pool_mode stop + off"
ssh "${O[@]}" pi@$H 'bash /home/pi/pool_mode.sh stop 2>&1 | tail -4; sleep 10; pgrep -af "[p]ool_sequence" || echo "no pool process"; bash /home/pi/pool_mode.sh off 2>&1 | tail -12; echo "-- after off:"; crontab -l; cd /home/pi/BM_Devel_Pi; sha256sum camera_config.yaml camera_schedule.yaml bm_command_state_v2.json' </dev/null
echo "== $(date -u +%FT%TZ) E: re-arm crontab from ~/hil_backup/$TS, halt"
[ -n "$TS" ] || { echo "STOP: no ARMED backup TS in gate.log"; exit 1; }
ssh "${O[@]}" pi@$H "crontab ~/hil_backup/$TS/crontab_ARMED.txt && echo armed: && crontab -l && sync && (sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 &) ; sleep 1; echo halt-issued" </dev/null
for i in $(seq 1 30); do ssh "${O[@]}" pi@$H true </dev/null 2>/dev/null || { echo "$(date -u +%T) Pi down"; break; }; sleep 2; done
sleep 15
echo "== $(date -u +%FT%TZ) E: restore schedule"
cd ../..; source hil/hil.env; HIL_RUN_DIR=$RUN hil/tools/hil_restore_schedule.sh $S; echo "hil_restore_schedule rc=$?"
} > "$OUT" 2>&1
echo "done -> $OUT"; tail -30 "$OUT"
