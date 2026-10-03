#!/bin/bash
# hil_bridge_phase.sh — move a bench Spotter's hourly bus window to a chosen minute (:15 for the comms A/B) or back
# to UTC :00, through the only route the bridge firmware has: the uptime timebase.
#
# Why (bm_protocol src/apps/bridge/bridgePowerController.cpp, _alignNextInterval): with the UTC timebase the first
# window is seeded at now − now % sampleInterval, so an hourly window always opens at :00 UTC; alignmentInterval5Min
# only rounds UP to a 5-min multiple. There is no offset key. With ticksSamplingEnabled=1 the timebase is the bridge's
# uptime: windows open at (bridge reset) + N × interval. `bridge cfg commit` resets the bridge, so a commit timed at
# hh:MM−lead puts every later window at :MM (the first one an hour after the commit). ANY later bridge reset (Spotter
# reboot, rebootctl, power cycle) re-phases it: re-run this tool (with Nick's OK).
#
# Inputs:   $1 SPOT-ID (bench rigs only); $2 mode: `ticks MM` (uptime timebase, windows at :MM) or `utc` (back to
#           ticksSamplingEnabled 0 → windows at :00, the production default); env HIL_PHASE_LEAD_S (default 8: s
#           before :MM:00 to send the commit, covering console latency + bridge reset; measure and adjust).
# Preconditions (REFUSES otherwise): the unit is HALTED (a commit cuts the bus); the commit minute is outside the
#           current window; Nick's OK for this bridge change typed in the Test Engineer chat (logged by the operator).
# Steps:    read back ticks/interval/duration → set ticksSamplingEnabled → wait for hh:MM:00 − lead (ticks mode; the
#           next such minute at least 2 min away) → commit → catch the stub-window boot and halt it (tuned_halt.sh,
#           as hil_restore_schedule.sh) → read back. Logs to gate.log in $HIL_RUN_DIR.
# Outputs:  gate.log lines (commit time = the new phase anchor); exit 0 when the read-back matches.
# Example:  hil/tools/hil_bridge_phase.sh SPOT-31593C ticks 15     # arm B: windows at :15
#           hil/tools/hil_bridge_phase.sh SPOT-31593C utc          # arm A / restore: windows at :00
# Limits:   the first window after a ticks commit is ~1 h later (uptime 3600 s); verify it on the console
#           (hil_wake_report.sh shows bus on/off). Tick drift is small (crystal) but not zero: re-check each wake.
set -u
SPOT="${1:?SPOT-ID}"; MODE="${2:?ticks MM | utc}"; MM="${3:-}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_spot "$SPOT"
LINE=$(_hil_rigs | awk -F= -v s="$SPOT" '$1 == s')
H=$(echo "$LINE" | cut -d= -f3); BR=$(echo "$LINE" | cut -d= -f4)
[ -n "$H" ] && [ -n "$BR" ] || { echo "[phase] rig line for $SPOT lacks host/bridge"; exit 2; }
hil_require_host "$H"
case "$MODE" in
  ticks) case "$MM" in ''|*[!0-9]*) echo "[phase] ticks needs a minute 0-59"; exit 2;; esac
         [ "$MM" -le 59 ] || { echo "[phase] minute must be 0-59"; exit 2; }; TICKS=1;;
  utc)   TICKS=0;;
  *)     echo "[phase] mode must be 'ticks MM' or 'utc'"; exit 2;;
esac
LEAD="${HIL_PHASE_LEAD_S:-8}"
RUN="$HIL_RUN_DIR"; mkdir -p "$RUN/console"
CON="$HIL_TOOLS_DIR/hil_console.sh"
log() { echo "$(date -u +%FT%TZ) [phase $SPOT/$H] $*" | tee -a "$RUN/gate.log"; }
up() { ssh -o BatchMode=yes -o ConnectTimeout=3 "pi@$H" true < /dev/null 2>/dev/null; }
readback() { for k in ticksSamplingEnabled bridgePowerControllerEnabled sampleIntervalMs sampleDurationMs alignmentInterval5Min; do
  v=$("$CON" "$SPOT" "bridge cfg get $BR s $k" 3 2>/dev/null | grep -oE "Value: *[0-9]+" | grep -oE "[0-9]+" | tail -1); echo "$k=$v"; done; }
if up; then log "REFUSED: $H is UP (a commit cuts a running Pi)"; exit 2; fi
log "before: $(readback | tr '\n' ' ')"
"$CON" "$SPOT" "bridge cfg set $BR s u ticksSamplingEnabled $TICKS" 4 | grep -E "Value|Succes" | tee -a "$RUN/gate.log"
if [ "$TICKS" = 1 ]; then
  # next hh:MM:00 at least 120 s away, minus the lead
  T=$(python3 -c "
import datetime as d
n=d.datetime.now(d.timezone.utc); t=n.replace(minute=$MM, second=0, microsecond=0)
while (t-n).total_seconds() < 120: t += d.timedelta(hours=1)
print(int(t.timestamp()) - $LEAD)")
  log "commit scheduled at $(date -u -r "$T" +%T 2>/dev/null || date -u -d "@$T" +%T) (= :$(printf %02d "$MM"):00 − ${LEAD}s); windows will open at :$(printf %02d "$MM") from one hour after"
  while [ "$(date -u +%s)" -lt "$T" ]; do sleep 1; done
  up && { log "REFUSED at commit time: $H came UP"; exit 2; }
fi
"$CON" "$SPOT" "bridge cfg commit $BR s" 4 | grep -E "Reboot info|bus power|power on for" | tee -a "$RUN/gate.log"
log "COMMITTED ticksSamplingEnabled=$TICKS (phase anchor = this time + bridge reset); waiting for the stub-window boot"
T0=$(date -u +%s)
until up; do sleep 2; [ $(( $(date -u +%s) - T0 )) -gt 110 ] && { log "Pi not up in the stub window"; break; }; done
if up; then
  ssh -o BatchMode=yes "pi@$H" 'nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null & echo "[phase] stub boot halted $(date -u +%T)"' < /dev/null 2>&1 | tee -a "$RUN/gate.log"
  sleep 20; up && log "WARNING $H still up after halt" || log "$H dark"
fi
AFTER=$(readback); log "after: $(echo "$AFTER" | tr '\n' ' ')"
echo "$AFTER" | grep -q "^ticksSamplingEnabled=$TICKS$" && echo "$AFTER" | grep -q "^bridgePowerControllerEnabled=1$" && { log "phase mode confirmed (ticks=$TICKS)"; exit 0; }
log "read-back does NOT match ticksSamplingEnabled=$TICKS / controller 1"; exit 1
