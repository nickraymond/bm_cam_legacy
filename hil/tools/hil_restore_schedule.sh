#!/bin/bash
# hil_restore_schedule.sh — put a bench Spotter's bus back on the PRODUCTION schedule (power controller
# on, 1 h interval, 10 min window) with its unit armed and halted, so the next aligned window runs the
# field-normal per_boot cycle. Adapted copy of runs/s5_console_20260928/restore_schedule.sh (original
# left in place). Rig-guarded (SPOT + host + bridge come from HIL_RIG_A / HIL_RIG_B).
#
# Inputs:   $1 SPOT-ID (SPOT-33507C | SPOT-31593C); env INTERVAL_MS (3600000), DURATION_MS (600000)
# Preconditions (REFUSES otherwise): the unit is HALTED (ssh fails). Its crontab must already be ARMED
#           (the stub-window boot runs cron; this script halts that boot cleanly). Nick's OK for bridge
#           config changes (2026-10-02, in the Test Engineer chat).
# Steps:    read back -> set bridgePowerControllerEnabled 1 (+ interval/duration if they differ) ->
#           commit (the bridge resets; the bus gives a ~2 min stub window) -> catch the Pi, confirm the
#           crontab is armed, halt it via tuned_halt.sh before the stub ends -> read back.
# Outputs:  gate.log + console/ lines in $HIL_RUN_DIR; exit 0 when the read-back is 1 / INTERVAL / DURATION.
# Example:  hil/tools/hil_restore_schedule.sh SPOT-31593C
# Limits:   the commit power-cycles the bus: NEVER with the Pi running (checked). If the Pi does not come
#           up in the stub window, it boots at the next aligned window (:00) and runs a normal cycle.
set -u
SPOT="${1:?SPOT-ID}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_spot "$SPOT"
LINE=$(_hil_rigs | awk -F= -v s="$SPOT" '$1 == s')
H=$(echo "$LINE" | cut -d= -f3); BR=$(echo "$LINE" | cut -d= -f4)
[ -n "$H" ] && [ -n "$BR" ] || { echo "[restore] rig line for $SPOT lacks host/bridge"; exit 2; }
hil_require_host "$H"
IV="${INTERVAL_MS:-3600000}"; DU="${DURATION_MS:-600000}"
RUN="$HIL_RUN_DIR"; mkdir -p "$RUN/console"
CON="$HIL_TOOLS_DIR/hil_console.sh"
log() { echo "$(date -u +%FT%TZ) [restore $SPOT/$H] $*" | tee -a "$RUN/gate.log"; }
up() { ssh -o BatchMode=yes -o ConnectTimeout=3 "pi@$H" true < /dev/null 2>/dev/null; }
readback() { for k in bridgePowerControllerEnabled sampleIntervalMs sampleDurationMs; do
  v=$("$CON" "$SPOT" "bridge cfg get $BR s $k" 3 2>/dev/null | grep -oE "Value: *[0-9]+" | grep -oE "[0-9]+" | tail -1); echo "$k=$v"; done; }
if up; then log "REFUSED: $H is UP (halt it first; a commit cuts a running Pi)"; exit 2; fi
log "before: $(readback | tr '\n' ' ')"
BEFORE=$(readback)
echo "$BEFORE" | grep -q "^sampleIntervalMs=$IV$" || "$CON" "$SPOT" "bridge cfg set $BR s u sampleIntervalMs $IV" 4 | grep -E "Value|Succes" | tee -a "$RUN/gate.log"
echo "$BEFORE" | grep -q "^sampleDurationMs=$DU$" || "$CON" "$SPOT" "bridge cfg set $BR s u sampleDurationMs $DU" 4 | grep -E "Value|Succes" | tee -a "$RUN/gate.log"
"$CON" "$SPOT" "bridge cfg set $BR s u bridgePowerControllerEnabled 1" 4 | grep -E "Value|Succes" | tee -a "$RUN/gate.log"
"$CON" "$SPOT" "bridge cfg commit $BR s" 4 | grep -E "Reboot info|bus power|power on for" | tee -a "$RUN/gate.log"
log "committed; waiting for the stub-window boot"
T0=$(date -u +%s)
until up; do sleep 2; [ $(( $(date -u +%s) - T0 )) -gt 110 ] && { log "Pi not up in the stub window (it will boot at the next :00 window)"; break; }; done
if up; then
  ssh -o BatchMode=yes "pi@$H" 'crontab -l | grep -E "^@reboot.*rc_run_capture_cycle" >/dev/null && echo "[restore] cron ARMED" || echo "[restore] WARNING cron NOT armed"; cat /home/pi/BM_Devel_Pi/software_sha.txt; nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null & echo "[restore] halt issued $(date -u +%T)"' < /dev/null 2>&1 | tee -a "$RUN/gate.log"
  sleep 20; up && log "WARNING $H still up after halt" || log "$H dark"
fi
AFTER=$(readback); log "after: $(echo "$AFTER" | tr '\n' ' ')"
echo "$AFTER" | grep -q "^bridgePowerControllerEnabled=1$" && echo "$AFTER" | grep -q "^sampleIntervalMs=$IV$" && echo "$AFTER" | grep -q "^sampleDurationMs=$DU$" && { log "PRODUCTION schedule confirmed"; exit 0; }
log "read-back does NOT match 1/$IV/$DU"; exit 1
