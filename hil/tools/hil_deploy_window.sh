#!/bin/bash
# hil_deploy_window.sh — one-window deploy of a git ref to an ARMED per_boot bench unit inside its hourly bus window
# (bmcam-field-update pattern; the bus window replaces "power it now"). Generalised from the R1F #121 deploy
# (scratchpad deploy_121_bmcam003.sh, 2026-10-04).
#
# Steps:   wait until WINDOW − 10 s → poll ssh until the Pi is up → ONE session: back up the ARMED crontab, disarm the
#          @reboot cycle line, SIGTERM the cycle (no finally → no halt) → snapshot before → stage tools/rc_field_update.sh
#          in /tmp from the unit's checkout (after git fetch) → run it with --ref REF --profile PROFILE --leave-disarmed
#          [--accept-print-config-diff] → verify software_sha.txt starts with WANT → re-arm from the ARMED backup →
#          snapshot after → [optional POST hook] → halt (tuned_halt.sh) before the bus hard-cuts at :10.
# Inputs:  $1 host (bmcam003|bmcam004), $2 ref, $3 expected sha prefix, $4 device profile (e.g. bmcam004/live_20260925),
#          $5 window start UTC (e.g. 2026-10-06T02:00:00Z), $6 label (for logs/snapshots);
#          env ACCEPT_DIFF=1 → pass --accept-print-config-diff; POST="<command run on the Mac after re-arm, before the
#          halt>" (e.g. a B0 run); HIL_RUN_DIR (gate.log, pulled/, snapshots/)
# Outputs: gate.log lines, pulled/<host>_field_update_<label>_<ts>.log, snapshots before_/after_<label>; prints RESULT
# Example: ACCEPT_DIFF=1 HIL_RUN_DIR=runs/s28_ladder_20261004 hil/tools/hil_deploy_window.sh bmcam004 \
#            feature/sprint28-b3a d91b9b6 bmcam004/live_20260925 2026-10-06T02:00:00Z b3a
# Restore: printed in gate.log (crontab backup path; the runtime tar rc_field_update writes in /home/pi/backups).
set -u
H="${1:?host}"; REF="${2:?ref}"; WANT="${3:?sha prefix}"; PROFILE="${4:?profile}"; WIN="${5:?window UTC}"; LBL="${6:?label}"
case "$H" in bmcam003|bmcam004) ;; *) echo "[hil-guard] REFUSED: $H is not a bench unit" >&2; exit 5;; esac
cd "$(dirname "$0")/../.." || exit 1
: "${HIL_RUN_DIR:?HIL_RUN_DIR}"; G=$HIL_RUN_DIR/gate.log; mkdir -p "$HIL_RUN_DIR/pulled"
log() { echo "$(date -u +%FT%TZ) [deploy-$LBL $H] $*" | tee -a "$G"; }
S() { ssh -o BatchMode=yes -o ConnectTimeout=3 "pi@$H" "$@" < /dev/null; }
T=$(python3 -c "import datetime,sys;print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp())-10)" "$WIN")
log "ref=$REF want=$WANT profile=$PROFILE window=$WIN accept_diff=${ACCEPT_DIFF:-0}; waiting"
until [ "$(date +%s)" -ge "$T" ]; do sleep 5; done
for i in $(seq 1 120); do S true 2>/dev/null && break; sleep 2; done
S true 2>/dev/null || { log "ABORT: $H not reachable within 4 min of the window"; exit 1; }
TS=$(date -u +%Y%m%dT%H%M%SZ)
S "mkdir -p ~/hil_backup/$TS && crontab -l > ~/hil_backup/$TS/crontab_ARMED.txt && crontab -l | sed -E 's|^(@reboot.*rc_run_capture_cycle)|# DISARMED_FOR_${LBL} \1|' | crontab - && pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py|[m]ain_pi_camera.py'; sleep 2; echo up=\$(cut -d. -f1 /proc/uptime)s; pgrep -af 'rc_run_capture_cycle|rc_progressive_jpeg' || echo no-cycle; crontab -l | grep -E '@reboot'" 2>&1 | tee -a "$G"
log "backup+disarm+stop TS=$TS; RESTORE crontab: ssh pi@$H 'crontab ~/hil_backup/$TS/crontab_ARMED.txt'; runtime rollback = /home/pi/backups tar printed by rc_field_update"
hil/tools/hil_unit_snapshot.sh "$H" "before_$LBL" >/dev/null 2>&1; log "snapshot before_$LBL"
FL="$HIL_RUN_DIR/pulled/${H}_field_update_${LBL}_$TS.log"
EXTRA=""; [ "${ACCEPT_DIFF:-0}" = 1 ] && EXTRA="--accept-print-config-diff"
S "cd /home/pi/repos/bm_cam_legacy && git fetch -q origin $REF && git show origin/$REF:tools/rc_field_update.sh > /tmp/rc_field_update_$LBL.sh && bash /tmp/rc_field_update_$LBL.sh --repo /home/pi/repos/bm_cam_legacy --ref $REF --profile $PROFILE --leave-disarmed $EXTRA" > "$FL" 2>&1
RC=$?
log "rc_field_update rc=$RC (log $FL)"
grep -nE "print-config|DIFF|FAIL|ERROR|stage [0-9]" "$FL" | tail -15 | tee -a "$G"
SHA=$(S "cat /home/pi/BM_Devel_Pi/software_sha.txt" 2>/dev/null | head -1)
log "runtime sha now: $SHA"
if [ $RC = 0 ] && echo "$SHA" | grep -q "^$WANT"; then
  S "crontab ~/hil_backup/$TS/crontab_ARMED.txt && crontab -l | grep -E '@reboot.*rc_run_capture_cycle'" 2>&1 | tee -a "$G"
  log "RE-ARMED from the ARMED backup"; RESULT=DEPLOYED
  hil/tools/hil_unit_snapshot.sh "$H" "after_$LBL" >/dev/null 2>&1; log "snapshot after_$LBL"
  if [ -n "${POST:-}" ]; then log "POST hook: $POST"; bash -c "$POST" 2>&1 | tee -a "$G"; fi
else
  log "DEPLOY FAILED or sha mismatch: re-arming the ARMED backup anyway (see $FL)"
  S "crontab ~/hil_backup/$TS/crontab_ARMED.txt" 2>&1 | tee -a "$G"; RESULT=FAILED
fi
S 'nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null & echo halt-issued' 2>&1 | tee -a "$G"
sleep 25; S true 2>/dev/null && log "WARNING still up after halt" || log "$H dark ($RESULT)"
echo "RESULT=$RESULT sha=$SHA"
