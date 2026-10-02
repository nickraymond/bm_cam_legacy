#!/bin/bash
# hil_refresh.sh — Sprint27 L1b: POST /devices/{d}/remote-config/refresh on the CONSOLE lane, then publish
# each recorded `get` on the Spotter console in id order, >= PACE s apart (default 65: the <CF> answers
# ride the Spotter's cellular queue, which overflows if they are bunched: F-G3-10), marking each sent.
# Rig-guarded (device + Spotter must be one bench rig).
#
# Inputs:   $1 device (BMCAM_003 | BMCAM_004), $2 SPOT-ID; env PACE (s, default 65)
# Outputs:  $HIL_RUN_DIR/api/refresh_<dev>.json (the backend's list), steps.log (each console answer),
#           api/refresh_<dev>_sent_<cid>.json
# Example:  hil/tools/hil_refresh.sh BMCAM_003 SPOT-33507C
# Limits:   the device view fills in only after Sofar ingests the <CF> answers (11-30 min); check
#           `reported_known` / `refresh_hint` afterwards. Refuses (exit 3) on 422 pending_unsent.
set -u
DEV="${1:?device}"; SPOT="${2:?SPOT}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_device "$DEV"; hil_require_spot "$SPOT"
[ "$(hil_spot_of_device "$DEV")" = "$SPOT" ] || { echo "[hil-guard] REFUSED: $DEV is not wired to $SPOT" >&2; exit 5; }
RUN="$HIL_RUN_DIR"; mkdir -p "$RUN/api"; PACE="${PACE:-65}"
MON="${HIL_MONITOR:?}"; ENVF="${HIL_MONITOR_ENV_FILE:-/home/pi/.config/nereus/heal_driver.env}"; API="${HIL_API:?}"
api() { ssh -o BatchMode=yes "$MON" "set -a; . $ENVF; set +a; curl -s -X $1 -H \"Authorization: Bearer \$ADMIN_TOKEN\" -H 'Content-Type: application/json' -d '$3' $API$2" < /dev/null; }
R=$(api POST "/devices/$DEV/remote-config/refresh" '{"lane":"console"}'); echo "$R" > "$RUN/api/refresh_$DEV.json"
N=$(echo "$R" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('commands') or []))" 2>/dev/null || echo 0)
[ "$N" -gt 0 ] || { echo "REFUSED $(echo "$R" | cut -c1-200)"; exit 3; }
echo "[refresh] $DEV: $N gets recorded; publishing every ${PACE}s"
i=0
echo "$R" | python3 -c "import json,sys; [print(c['command_id'], c['command']) for c in json.load(sys.stdin)['commands']]" | while read -r CID JSON; do
  [ $i -gt 0 ] && sleep "$PACE"; i=$((i+1))
  "$HIL_TOOLS_DIR/hil_cmd.sh" "L1b.$DEV.$CID" "$JSON" 8 "$SPOT" | grep -E "\[bmcam" | grep -v duplicate | head -1 | cut -c1-160
  api POST "/admin/devices/$DEV/commands/$CID/sent" '{"http_status":null}' > "$RUN/api/refresh_${DEV}_sent_$CID.json"
done
echo "[refresh] $DEV done $(date -u +%FT%TZ)"
