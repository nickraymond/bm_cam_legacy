#!/bin/bash
# hil_step.sh — one ladder step on a stay_on unit: [command] -> [one-shot trigger] -> wait for the
# action to finish -> pull the capture evidence (sidecar / metadata / action log excerpt).
#
# Purpose:  make every G3 step produce the same evidence set, so RESULTS rows are comparable.
# Inputs:   $1 STEP tag, $2 host, $3 SPOT-ID, $4 command JSON or "-" (no command),
#           $5 trigger JSON or "-" (no capture), $6 max wait for the action in s (default 420)
#           env HIL_RUN_DIR, HIL_MONITOR (see hil_console.sh)
# Outputs:  steps.log (command + trigger answers via hil_cmd.sh, pistate before/after),
#           pulled/<STEP>/ : action.log (the unit's log for this action), *.capture_metadata.json /
#           *.metadata.json (stills) or the clip manifest (video); summary line printed:
#           "STEP <tag> ack=<OK|REJECTED|none> action=<stage> media=<file>"
# Example:  hil/tools/hil_step.sh L3 bmcam004 SPOT-31593C \
#             '{"id":64010,"c":"set","kv":{"camera.exposure.shutter_us":10000,"camera.exposure.analogue_gain":2.0}}' \
#             '{"id":64011,"c":"trg","v":2,"kv":{"med":"still","m":40}}'
# Limits:   assumes stay_on (actions numbered "===== action N"); a trigger is answered at once
#           but runs at the next decision point; one action at a time (do not overlap steps).
#           Pulls only small files (< 1000 kB; note GNU find -size -1M only matches EMPTY files).
set -u
STEP="${1:?STEP}"; H="${2:?host}"; SPOT="${3:?SPOT}"; CMD="${4:?cmd or -}"; TRG="${5:?trg or -}"; WMAX="${6:-420}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="${HIL_RUN_DIR:?HIL_RUN_DIR}"; OUT="$RUN/pulled/$STEP"; mkdir -p "$OUT"
APP=/home/pi/BM_Devel_Pi
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 pi@$H"
"$HERE/hil_pistate.sh" "$STEP.before" "$H" > /dev/null
ACK=none
if [ "$CMD" != "-" ]; then
  A=$("$HERE/hil_cmd.sh" "$STEP.cmd" "$CMD" 8 "$SPOT" | grep -v duplicate)
  echo "$A" | cut -c1-300
  ACK=$(echo "$A" | grep -oE '\] (OK|REJECTED) id=' | head -1 | grep -oE 'OK|REJECTED' || echo none)
fi
if [ "$TRG" != "-" ]; then
  N0=$($SSH "grep -c '===== action .* done' \$(ls -t $APP/cron_logs/rc_cycle_*.log | head -1)" < /dev/null)
  "$HERE/hil_cmd.sh" "$STEP.trg" "$TRG" 6 "$SPOT" | grep -v duplicate | grep -E '\[bmcam' | cut -c1-260
  T0=$(date +%s)
  while :; do
    N=$($SSH "grep -c '===== action .* done' \$(ls -t $APP/cron_logs/rc_cycle_*.log | head -1)" < /dev/null 2>/dev/null)
    [ -n "$N" ] && [ "$N" -gt "$N0" ] && break
    [ $(( $(date +%s) - T0 )) -gt "$WMAX" ] && { echo "[step] TIMEOUT waiting for the action (${WMAX}s)"; break; }
    sleep 10
  done
  $SSH "L=\$(ls -t $APP/cron_logs/rc_cycle_*.log | head -1); awk '/===== action [0-9]+ \(/{buf=\"\"} {buf=buf \$0 \"\n\"} /===== action [0-9]+ done/{last=buf} END{printf \"%s\", last}' \$L" < /dev/null > "$OUT/action.log"
  # newest still sidecars or clip manifest written by this action
  F=$($SSH "cd $APP; ls -t images/*capture_metadata.json images/*native_full.metadata.json videos/*.json 2>/dev/null | head -4" < /dev/null)
  for f in $F; do
    $SSH "find $APP/$f -newermt '@$T0' -size -1000k" < /dev/null | grep -q . && scp -q "pi@$H:$APP/$f" "$OUT/" 2>/dev/null
  done
fi
"$HERE/hil_pistate.sh" "$STEP.after" "$H" > /dev/null
STAGE=$(grep -oE 'stage=[A-Za-z_]+' "$OUT/action.log" 2>/dev/null | tail -1)
echo "STEP $STEP ack=$ACK action=${STAGE:-none} files=$(ls "$OUT" | tr '\n' ' ')" | tee -a "$RUN/steps.log"
