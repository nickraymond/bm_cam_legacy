#!/bin/bash
# hil_deploy_unit.sh — deploy origin/development to ONE stay_on bench unit (held bus, cron armed) the
# TAKEOVER way: back up -> disarm -> stop the runtime -> tools/rc_field_update.sh --leave-disarmed
# -> [optional unit-side step while stopped] -> re-arm from the backup -> reboot. Rig-guarded.
#
# Inputs:   $1 host (bmcam003 | bmcam004), $2 device profile (e.g. bmcam004/live_20260925)
#           $3.. extra rc_field_update flags (e.g. --accept-print-config-diff)
#           env HIL_POST_DEPLOY_CMD (optional, from hil.env or the caller's shell): a command run ON the
#           unit after the deploy, before re-arming (e.g. a config upgrade).
# Outputs:  $HIL_RUN_DIR/pulled/<host>_field_update_<TS>.log, snapshots/<host>_{before,after}_deploy_*,
#           gate.log lines (backup dir, restore command, rollback tar). Prints the print-config diff
#           section of the deploy log so the operator can judge it.
# Example:  hil/tools/hil_deploy_unit.sh bmcam004 bmcam004/live_20260925 --accept-print-config-diff
# Limits:   stay_on units only (a per_boot unit halts itself: use the bmcam-field-update skill). The
#           reboot reloads cron @reboot; wait for `stay_on: media=` before the next step. Exit 1 and the
#           unit is LEFT DISARMED + stopped if rc_field_update fails (restore command in gate.log).
set -u
H="${1:?host}"; PROFILE="${2:?profile}"; shift 2
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_host "$H"
REPO_ROOT="$(dirname "$HIL_DIR")"
RUN="$HIL_RUN_DIR"; mkdir -p "$RUN/pulled"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 pi@$H"
TS=$(date -u +%Y%m%dT%H%M%SZ); B=/home/pi/hil_backup/$TS
log() { echo "$(date -u +%FT%TZ) [$H] $*" | tee -a "$RUN/gate.log"; }
log "deploy: backup+disarm TS=$TS; restore: crontab $B/crontab_ARMED.txt && sudo reboot"
"$HIL_TOOLS_DIR/hil_unit_snapshot.sh" "$H" before_deploy > /dev/null
$SSH "set -u; A=/home/pi/BM_Devel_Pi; mkdir -p $B; crontab -l > $B/crontab_ARMED.txt
for f in camera_config.yaml camera_schedule.yaml bm_command_state_v2.json config_journal.jsonl software_sha.txt; do [ -f \$A/\$f ] && cp -p \$A/\$f $B/; done
crontab -l | sed 's|^@reboot \(.*rc_run_capture_cycle\.sh\)\$|# DISABLED hil $TS: @reboot \1|' | crontab -
crontab -l | grep -n reboot" < /dev/null 2>&1 | tee -a "$RUN/gate.log"
# stop in its OWN ssh call (a pgrep in the same command as the sed above matches itself)
$SSH "pkill -TERM -f '[r]c_progressive_jp[e]g.py'; for i in \$(seq 1 72); do pgrep -f '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle' >/dev/null || break; sleep 5; done; pgrep -af '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle|[r]pica[m]-|[f]fmpe[g]' || echo STOPPED" < /dev/null 2>&1 | tee -a "$RUN/gate.log" | grep -q STOPPED || { log "runtime did NOT stop: unit left disarmed, NOT deployed"; exit 1; }
scp -q "$REPO_ROOT/tools/rc_field_update.sh" "pi@$H:/tmp/rc_field_update.sh"
$SSH "bash /tmp/rc_field_update.sh --repo /home/pi/repos/bm_cam_legacy --ref development --profile $PROFILE --leave-disarmed $* > $B/field_update.log 2>&1; echo rc=\$?" < /dev/null | tee -a "$RUN/gate.log" | grep -q "rc=0"; RC=$?
scp -q "pi@$H:$B/field_update.log" "$RUN/pulled/${H}_field_update_$TS.log"
grep -E "parity|diff|^[-+<>] |SUMMARY|repo:|restore command" "$RUN/pulled/${H}_field_update_$TS.log" | cut -c1-220
if [ $RC -ne 0 ]; then log "rc_field_update FAILED: unit left DISARMED; see pulled/${H}_field_update_$TS.log"; exit 1; fi
if [ -n "${HIL_POST_DEPLOY_CMD:-}" ]; then
  log "post-deploy step: $HIL_POST_DEPLOY_CMD"; $SSH "$HIL_POST_DEPLOY_CMD" < /dev/null 2>&1 | tee -a "$RUN/gate.log"
fi
"$HIL_TOOLS_DIR/hil_unit_snapshot.sh" "$H" after_deploy | grep -E "^KEY (sha|cfg_hash|procs)"
$SSH "crontab $B/crontab_ARMED.txt && cmp <(crontab -l) $B/crontab_ARMED.txt && echo CRON_MATCHES_BACKUP && sudo systemctl reboot" < /dev/null 2>&1 | tee -a "$RUN/gate.log"
log "re-armed + reboot"
