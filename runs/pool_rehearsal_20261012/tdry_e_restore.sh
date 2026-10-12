#!/bin/bash
# T-RESTORE for bmcam004 / SPOT-31593C after T-DRY + the EM's media cleanup: re-arm the crontab from the
# hil_bus_always_on backup, halt the Pi (tuned_halt.sh), hil_restore_schedule.sh (Nick OK c). Output: tdry_e_<UTC>.txt
set -u
cd "$(dirname "$0")"; RUN=$PWD
H=bmcam004; S=SPOT-31593C; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
OUT=$RUN/tdry_e_$(date -u +%Y%m%dT%H%MZ).txt
TS=$(grep -oE "hil_backup/[0-9TZ]+/crontab_ARMED" gate.log | tail -1 | cut -d/ -f2)
{
echo "== $(date -u +%FT%TZ) E: re-arm crontab from ~/hil_backup/$TS, halt"
[ -n "$TS" ] || { echo "STOP: no ARMED backup TS in gate.log"; exit 1; }
ssh "${O[@]}" pi@$H "crontab ~/hil_backup/$TS/crontab_ARMED.txt && echo armed: && crontab -l" </dev/null
# QE #152 finding: pool_mode off restores the DISARMED crontab; never trust it. Verify an ACTIVE @reboot RC line that is
# byte-identical to the 12:03 PM pre-pool snapshot before halting / restoring the schedule.
WANT=$(grep -m1 "^@reboot .*rc_run_capture_cycle.sh" tdry_a_*.txt)
GOT=$(ssh "${O[@]}" pi@$H "crontab -l" </dev/null)
if [ "$GOT" != "$WANT" ]; then echo "STOP: crontab after re-arm != pre-pool snapshot"; echo "want: $WANT"; echo "got:  $GOT"; exit 1; fi
echo "crontab == pre-pool snapshot (armed): OK"
ssh "${O[@]}" pi@$H "sync && (sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 &) ; sleep 1; echo halt-issued" </dev/null
for i in $(seq 1 30); do ssh "${O[@]}" pi@$H true </dev/null 2>/dev/null || { echo "$(date -u +%T) Pi down"; break; }; sleep 2; done
sleep 15
echo "== $(date -u +%FT%TZ) E: restore schedule"
cd ../..; source hil/hil.env; HIL_RUN_DIR=$RUN hil/tools/hil_restore_schedule.sh $S; echo "hil_restore_schedule rc=$?"
} > "$OUT" 2>&1
echo "done -> $OUT"; tail -30 "$OUT"
