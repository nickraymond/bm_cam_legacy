#!/bin/bash
# cmd.sh STEP 'JSON' [wait_s] [SPOT] — S5 ladder: publish ONE v9 command on the Spotter
# console (bm pub bmcam/cmd JSON 1 1 via nereus000's cmd.txt), wait, then print the unit's
# console answer and every cellular payload the Spotter queued meanwhile (decoded).
# Everything goes to ladder.log with the step tag. JSON must not contain single quotes.
#   ./cmd.sh L1.ping '{"id":1,"c":"ping"}' 6
STEP="$1"; JSON="$2"; W="${3:-8}"; SPOT="${4:-SPOT-33507C}"
HERE="$(cd "$(dirname "$0")" && pwd)"
{
  echo "===== $(date -u +%FT%TZ) $STEP $SPOT  bm pub bmcam/cmd $JSON 1 1  ($(printf 'bm pub bmcam/cmd %s 1 1\n' "$JSON" | wc -c | tr -d ' ') B line)"
  "$HERE/console.sh" "$SPOT" "bm pub bmcam/cmd $JSON 1 1" "$W" | "$HERE/con_decode.py"
} | tee -a "$HERE/ladder.log"
