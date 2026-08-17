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

set -uo pipefail

DST="/home/pi/BM_Devel_Pi"
STATE="$DST/bm_command_state.json"
CRON_BACKUP="/home/pi/crontab_before_dev_mode.txt"
MODE="${1:-status}"

record_hlt() {
    # Record a local hlt command through the SAME CommandState machinery
    # the daemon uses (id = epoch seconds, unique enough for dedupe).
    python3 - "$1" <<'PYEOF'
import sys, time
sys.path.insert(0, "/home/pi/BM_Devel_Pi")
from command_state import CommandState
value = int(sys.argv[1])
state = CommandState(path="/home/pi/BM_Devel_Pi/bm_command_state.json")
state.record(int(time.time()) & 0xFFFFFFFF, "hlt", value)
print(f"[DEV-MODE] recorded hlt={value} "
      f"(settings={state.settings}, touched={sorted(state.touched)})")
PYEOF
}

show_status() {
    if crontab -l >/dev/null 2>&1; then
        echo "[DEV-MODE] boot capture : ARMED ($(crontab -l | grep -c '@reboot') @reboot line(s))"
    else
        echo "[DEV-MODE] boot capture : disarmed"
    fi
    python3 - <<'PYEOF'
import json
try:
    d = json.load(open("/home/pi/BM_Devel_Pi/bm_command_state.json"))
    hlt = d.get("settings", {}).get("hlt", 0)
    names = {0: "config file governs", 1: "halt ON (real)",
             2: "halt DRY-RUN", 3: "halt OFF (developer mode)"}
    print(f"[DEV-MODE] halt override : hlt={hlt} -> {names.get(hlt, '?')} "
          "(applies from next cycle)")
except FileNotFoundError:
    print("[DEV-MODE] halt override : no state file (config file governs)")
PYEOF
    echo "[DEV-MODE] spotter bus   : Spotter-side — confirm always-on at the"
    echo "[DEV-MODE]                Spotter console (bench SPOT-33507C: yes)"
}

case "$MODE" in
  on)
    echo "[DEV-MODE] entering developer state"
    if crontab -l >/dev/null 2>&1; then
        crontab -l > "$CRON_BACKUP"
        crontab -r
        echo "[DEV-MODE] crontab disarmed (backup: $CRON_BACKUP)"
    else
        echo "[DEV-MODE] crontab already empty (no backup overwritten)"
    fi
    record_hlt 3
    echo "[DEV-MODE] NOTE: a cycle already running still halts with its"
    echo "[DEV-MODE] boot settings (D2) — hlt=3 governs from the NEXT cycle."
    show_status
    ;;
  off)
    echo "[DEV-MODE] restoring field-normal state"
    if [[ -f "$CRON_BACKUP" ]]; then
        crontab "$CRON_BACKUP"
        echo "[DEV-MODE] crontab re-armed from $CRON_BACKUP"
    else
        echo "[DEV-MODE][WARN] no $CRON_BACKUP — re-arm manually from your"
        echo "[DEV-MODE][WARN] unit's armed backup (crontab <file>)"
    fi
    record_hlt 0
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
