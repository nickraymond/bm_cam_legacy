#!/usr/bin/env bash
# dev_mode.sh — put a bmcam unit into (or out of) DEVELOPER state.
#
# Repo path: tools/dev_mode.sh          Run ON the Pi:
#   bash ~/repos/bm_cam_legacy/tools/dev_mode.sh on|off|status
#
# Developer state = the "happy state for developing" (Nick 2026-08-01):
#   1. power halt OFF        -> command overlay hlt=3 ("developer mode"
#                               in the tables) recorded in
#                               bm_command_state.json. Applies from the
#                               NEXT cycle (D2); visible in `cfg` as
#                               "command hlt=3"; reversed by hlt 0.
#   2. no capture at boot    -> crontab disarmed (backup kept at a FIXED
#                               path so `off` restores exactly what `on`
#                               removed, never a guessed backup).
#   3. Spotter bus always-on -> Spotter-side setting; this tool only
#                               REPORTS it as a checklist item (bench
#                               SPOT-33507C is always-on).
#
# `off` = field-normal: re-arm from the backup + hlt 0 (config file
# governs the halt again). `status` changes nothing.
#
# Outputs: loud per-lever lines + a final state block. Exits nonzero if
# a lever could not be applied. Known limitation: does not touch the
# YAML (the overlay doctrine: camera_schedule.yaml is never rewritten).
#
# Sprint26 S4 (PLAN_S4.md b.7): the hlt is still recorded in the v8 section
# (the legacy runtime reads it); the v9 supervisor folds a CHANGED v8 hlt into
# its overlay at the next boot (on = halt off; off = the halt keys go back to
# the YAML unless a v9 command set them since). The write takes the cycle's
# flock (/tmp/bmcam_rc_capture.lock) and refuses while a cycle runs, so it is
# never a second writer next to a running process. Ids are console-range
# (1..99999), not epoch seconds. Test hooks: BMCAM_DST, BMCAM_DEV_MODE_NO_CRON=1.

set -uo pipefail

DST="${BMCAM_DST:-/home/pi/BM_Devel_Pi}"
# Sprint26 S2: a config-v2 unit (camera_config.yaml present) keeps the command
# state in bm_command_state_v2.json (the v8 section). The v1 file is then only
# the rollback copy: writing hlt there would change nothing the unit runs.
if [[ -f "$DST/camera_config.yaml" ]]; then
    STATE="$DST/bm_command_state_v2.json"
else
    STATE="$DST/bm_command_state.json"
fi
CRON_BACKUP="/home/pi/crontab_before_dev_mode.txt"
LOCK="${BMCAM_CYCLE_LOCK:-/tmp/bmcam_rc_capture.lock}"
NO_CRON="${BMCAM_DEV_MODE_NO_CRON:-0}"
MODE="${1:-status}"

record_hlt() {
    # Record a local hlt through the SAME CommandState machinery the legacy
    # daemon uses; console-range id (§6.2: 1..99999). Under the cycle's flock.
    local cmd=(python3 - "$1" "$DST" "$STATE")
    if command -v flock >/dev/null 2>&1; then
        cmd=(flock -n "$LOCK" "${cmd[@]}")
    else
        echo "[DEV-MODE][WARN] no flock here (not a Pi?): writing without the cycle lock"
    fi
    "${cmd[@]}" <<'PYEOF'
import sys, time
sys.path.insert(0, sys.argv[2])
from command_state import CommandState
value = int(sys.argv[1])
state = CommandState(path=sys.argv[3])
cid = 1 + int(time.time()) % 99999
state.record(cid, "hlt", value)
print(f"[DEV-MODE] recorded hlt={value} (id {cid}) in {state.path} "
      f"(settings={state.settings}, touched={sorted(state.touched)})")
PYEOF
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "[DEV-MODE][ERROR] hlt not recorded (exit $rc): a cycle holds $LOCK?" \
             "Catch the unit halted or disarmed and re-run." >&2
    fi
    return $rc
}

show_status() {
    if [[ "$NO_CRON" == "1" ]]; then
        echo "[DEV-MODE] boot capture : (not checked: BMCAM_DEV_MODE_NO_CRON)"
    elif crontab -l >/dev/null 2>&1; then
        echo "[DEV-MODE] boot capture : ARMED ($(crontab -l | grep -c '@reboot') @reboot line(s))"
    else
        echo "[DEV-MODE] boot capture : disarmed"
    fi
    python3 - "$STATE" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    full = d
    if d.get("schema") == "bm_command_state_v2":
        d = d.get("v8") or {}
    hlt = d.get("settings", {}).get("hlt", 0)
    names = {0: "config file governs", 1: "halt ON (real)",
             2: "halt DRY-RUN", 3: "halt OFF (developer mode)"}
    print(f"[DEV-MODE] halt override : hlt={hlt} -> {names.get(hlt, '?')} "
          "(applies from next cycle)")
    ov = {k: v for k, v in (full.get("overlay") or {}).items() if k.startswith("power.halt.")}
    if full.get("schema") == "bm_command_state_v2":
        print(f"[DEV-MODE] v9 overlay    : {ov or 'no power.halt keys'} "
              f"(the supervisor re-folds a changed hlt at its next boot)")
except FileNotFoundError:
    print("[DEV-MODE] halt override : no state file (config file governs)")
PYEOF
    echo "[DEV-MODE] spotter bus   : Spotter-side — confirm always-on at the"
    echo "[DEV-MODE]                Spotter console (bench SPOT-33507C: yes)"
}

case "$MODE" in
  on)
    echo "[DEV-MODE] entering developer state"
    if [[ "$NO_CRON" == "1" ]]; then
        echo "[DEV-MODE] crontab untouched (BMCAM_DEV_MODE_NO_CRON)"
    elif crontab -l >/dev/null 2>&1; then
        crontab -l > "$CRON_BACKUP"
        crontab -r
        echo "[DEV-MODE] crontab disarmed (backup: $CRON_BACKUP)"
    else
        echo "[DEV-MODE] crontab already empty (no backup overwritten)"
    fi
    record_hlt 3 || exit 1
    echo "[DEV-MODE] NOTE: a cycle already running still halts with its"
    echo "[DEV-MODE] boot settings (D2) — hlt=3 governs from the NEXT cycle."
    show_status
    ;;
  off)
    echo "[DEV-MODE] restoring field-normal state"
    # S4b review R2-7: the halt override goes FIRST; re-arming a unit that
    # still has hlt=3 would give field cycles that never halt.
    record_hlt 0 || exit 1
    if [[ "$NO_CRON" == "1" ]]; then
        echo "[DEV-MODE] crontab untouched (BMCAM_DEV_MODE_NO_CRON)"
    elif [[ -f "$CRON_BACKUP" ]]; then
        crontab "$CRON_BACKUP"
        echo "[DEV-MODE] crontab re-armed from $CRON_BACKUP"
    else
        echo "[DEV-MODE][WARN] no $CRON_BACKUP — re-arm manually from your"
        echo "[DEV-MODE][WARN] unit's armed backup (crontab <file>)"
    fi
    show_status
    ;;
  status)
    show_status
    ;;
  *)
    echo "usage: dev_mode.sh on|off|status" >&2
    exit 2
    ;;
esac
