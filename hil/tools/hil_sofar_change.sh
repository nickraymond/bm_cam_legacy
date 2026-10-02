#!/bin/bash
# hil_sofar_change.sh — one remote-config change over the CELLULAR (Sofar) lane through the backend:
# POST /devices/{d}/remote-config/changes ("lane":"sofar") -> POST /admin/devices/{d}/commands/{cid}/send.
# The G5 sender (no console) and the G3 L12/L14 sender. Rig allow-list guarded: a device that is not
# in HIL_RIG_A / HIL_RIG_B (e.g. a field unit on SPOT-33361C) exits 5 before any network call.
#
# Inputs:   $1 STEP tag, $2 device id (BMCAM_003 | BMCAM_004), $3 change body JSON
#           ({"set":{...}} or {"reset":[...]}); add "supersede":true yourself only when a previous
#           change for the same keys is in flight and you mean to replace it.
#           env (from hil/hil.env via hil_common.sh): HIL_MONITOR, HIL_MONITOR_ENV_FILE, HIL_API, HIL_RUN_DIR
# Outputs:  $HIL_RUN_DIR/api/<STEP>_sofar.json (change + send answers); prints "CID <id> outcome=<o>"
#           or "REFUSED ..." (exit 3). Sends share the backend's 65 s / Spotter guard with heals:
#           a 409 rate_limited answer is printed, not retried here.
# Example:  hil/tools/hil_sofar_change.sh L14a BMCAM_004 '{"reset":["camera.image_processing.hdr"]}'
# Limits:   delivery takes 20-65 min (the Spotter checks its mailbox around its report); watch the
#           console or the device view for the ack. The token is read ON the monitor host only.
set -u
STEP="${1:?STEP}"; DEV="${2:?device}"; BODY="${3:?body}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_device "$DEV"
RUN="${HIL_RUN_DIR:?}"; mkdir -p "$RUN/api"
MON="${HIL_MONITOR:?}"; ENVF="${HIL_MONITOR_ENV_FILE:-/home/pi/.config/nereus/heal_driver.env}"; API="${HIL_API:?}"
case "$BODY" in *"'"*) echo "single quote in body" >&2; exit 2;; esac
FULL=$(python3 -c "import json,sys; b=json.loads(sys.argv[1]); b['lane']='sofar'; print(json.dumps(b, separators=(',',':')))" "$BODY") || exit 2
api() { ssh -o BatchMode=yes "$MON" "set -a; . $ENVF; set +a; curl -s -X $1 -H \"Authorization: Bearer \$ADMIN_TOKEN\" -H 'Content-Type: application/json' -d '$3' $API$2" < /dev/null; }
R=$(api POST "/devices/$DEV/remote-config/changes" "$FULL")
CID=$(echo "$R" | python3 -c "import json,sys; print(json.load(sys.stdin).get('command_id') or '')" 2>/dev/null)
if [ -z "$CID" ]; then
  echo "{\"change\":$R}" > "$RUN/api/${STEP}_sofar.json"; echo "REFUSED $(echo "$R" | cut -c1-200)"; exit 3
fi
S=$(api POST "/admin/devices/$DEV/commands/$CID/send" '')
echo "{\"change\":$R,\"send\":$S}" > "$RUN/api/${STEP}_sofar.json"
echo "$(date -u +%FT%TZ) $DEV sofar cid=$CID $FULL" >> "$RUN/commands.log"
echo "CID $CID outcome=$(echo "$S" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('sends') or [{}])[-1].get('outcome') or d.get('detail'))" 2>/dev/null)"
