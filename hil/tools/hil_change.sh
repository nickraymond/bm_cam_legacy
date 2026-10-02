#!/bin/bash
# hil_change.sh — one remote-config change through the BACKEND on the console lane (Sprint27 SPEC
# §2.3 / LADDER "How each step is sent"): POST /devices/{d}/remote-config/changes with
# "lane":"console" -> publish the returned console_line on the Spotter console -> POST
# /admin/devices/{d}/commands/{cid}/sent with http_status null. The backend records the
# command (logs.html, D4) and allocates the remote id; the console makes delivery immediate.
#
# Inputs:   $1 STEP tag, $2 device id (BMCAM_004), $3 SPOT-ID, $4 change body JSON
#           ({"set":{...}} or {"reset":[...]}; "lane":"console" and "supersede":true are added)
#           env HIL_RUN_DIR, HIL_MONITOR, HIL_MONITOR_ENV_FILE (token, read ON the monitor host),
#           HIL_API (staging base)
# Outputs:  $HIL_RUN_DIR/api/<STEP>_change.json (backend answer), steps.log (console answer),
#           api/<STEP>_sent.json; prints "CID <id>" or "REFUSED <reasons>" (exit 3 when refused).
# Example:  hil/tools/hil_change.sh IP1.sh0 BMCAM_004 SPOT-31593C '{"set":{"camera.image_processing.sharpness":0.0}}'
# Limits:   "supersede":true is always set (the console-lane ack reaches the backend only after
#           Sofar ingest, 11-30 min, so the previous change is still in_flight); never use it for
#           the Sofar lane. The body must not contain single quotes.
set -u
STEP="${1:?STEP}"; DEV="${2:?device}"; SPOT="${3:?SPOT}"; BODY="${4:?body}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_device "$DEV"; hil_require_spot "$SPOT"
[ "$(hil_spot_of_device "$DEV")" = "$SPOT" ] || { echo "[hil-guard] REFUSED: $DEV is not wired to $SPOT" >&2; exit 5; }
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="${HIL_RUN_DIR:?}"; mkdir -p "$RUN/api"
MON="${HIL_MONITOR:?}"; ENVF="${HIL_MONITOR_ENV_FILE:-/home/pi/.config/nereus/heal_driver.env}"; API="${HIL_API:?}"
case "$BODY" in *"'"*) echo "single quote in body" >&2; exit 2;; esac
FULL=$(python3 -c "import json,sys; b=json.loads(sys.argv[1]); b.update(lane='console', supersede=True); print(json.dumps(b, separators=(',',':')))" "$BODY")
api() {  # $1 method, $2 path, $3 body -> stdout json (curl runs on the monitor, token never leaves it)
  ssh -o BatchMode=yes "$MON" "set -a; . $ENVF; set +a; curl -s -X $1 -H \"Authorization: Bearer \$ADMIN_TOKEN\" -H 'Content-Type: application/json' -d '$3' $API$2" < /dev/null
}
R=$(api POST "/devices/$DEV/remote-config/changes" "$FULL"); echo "$R" > "$RUN/api/${STEP}_change.json"
CID=$(echo "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('command_id') or '')" 2>/dev/null)
if [ -z "$CID" ]; then
  echo "REFUSED $(echo "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); d=d.get('detail',d); print([(r.get('reason'),r.get('key')) for r in d.get('refusals',[])] or d)" 2>/dev/null || echo "$R" | cut -c1-200)"
  exit 3
fi
LINE=$(echo "$R" | python3 -c "import json,sys; print(json.load(sys.stdin)['console_line'])")
JSON=${LINE#bm pub bmcam/cmd }; JSON=${JSON% 1 1}
"$HERE/hil_cmd.sh" "$STEP" "$JSON" 8 "$SPOT" | grep -v duplicate | grep -E '\[bmcam' | cut -c1-300
api POST "/admin/devices/$DEV/commands/$CID/sent" '{"http_status":null}' > "$RUN/api/${STEP}_sent.json"
echo "CID $CID"
