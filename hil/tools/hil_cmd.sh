#!/bin/bash
# hil_cmd.sh — publish ONE bmcam command on a Spotter console (bm pub bmcam/cmd JSON 1 1),
# then print the unit's console answer and every cellular payload queued meanwhile, decoded.
#
# Purpose:  console-lane command step for ladders / gates (fast, direct, zero cellular for the
#           command itself; Nick 2026-09-24: console lane until the feature is vetted).
# Origin:   adapted copy of runs/s5_console_20260928/cmd.sh (original left in place).
# Inputs:   $1 STEP tag, $2 the command JSON, $3 wait seconds (default 8), $4 SPOT-ID
#           (default $HIL_SPOT); env as hil_console.sh.
# Outputs:  $HIL_RUN_DIR/steps.log (step header + decoded answer); commands.log via hil_console.
# Example:  hil/tools/hil_cmd.sh L1.ping '{"id":51001,"c":"ping"}' 8 SPOT-33507C
# Limits:   JSON must not contain single quotes; the full console line must be <= 256 B
#           (the JSON <= ~234 B); the answer must arrive within the wait (re-read the
#           monitor log with a longer wait if the unit is mid-burst: answers queue in its inbox).
set -u
STEP="${1:?STEP}"; JSON="${2:?JSON}"; W="${3:-8}"; SPOT="${4:-${HIL_SPOT:?SPOT-ID or HIL_SPOT}}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="${HIL_RUN_DIR:-.}"; mkdir -p "$RUN"
LINE="bm pub bmcam/cmd $JSON 1 1"
{
  echo "===== $(date -u +%FT%TZ) $STEP $SPOT  $LINE  ($(( $(printf '%s' "$LINE" | wc -c) + 1 )) B line)"
  "$HERE/hil_console.sh" "$SPOT" "$LINE" "$W" 2>/dev/null | python3 "$HERE/hil_con_decode.py"
} | tee -a "$RUN/steps.log"
