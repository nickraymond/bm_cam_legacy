#!/usr/bin/env bash
# filename: spot_cmd.sh
# description: Send one Spotter console command via the monitor's cmd.txt FIFO
#              and print everything the console emitted in response.
#
# WHY  The monitor owns the serial port, so nothing else may open it. It
#      drains <log-root>/<SPOT-ID>/cmd.txt one line at a time; this wrapper
#      writes the line, waits, and diffs the console log so the response is
#      attributable to THIS command instead of scrolling past in the log.
#
# USAGE   ./spot_cmd.sh SPOT-33507C "bm cfg status 0 s" [wait_s]
# OUTPUT  the console lines produced after the command was written
# NOTE    read-only commands only unless you know what you are doing; see
#         docs/spotter_cli_reference.md "Danger zone".
set -uo pipefail
SPOT="${1:?spotter id, e.g. SPOT-33507C}"
CMD="${2:?command line}"
WAIT="${3:-8}"
ROOT="${SPOTTER_LOG_ROOT:-$HOME/spotter_logs}"
LOG="$ROOT/$SPOT/console_$(date -u +%Y%m%d).log"

before=$(wc -l < "$LOG" 2>/dev/null || echo 0)
echo "$CMD" > "$ROOT/$SPOT/cmd.txt"
sleep "$WAIT"
echo "--- $SPOT <<< $CMD  (+${WAIT}s) ---"
tail -n +$((before + 1)) "$LOG" | grep -v "power | tick" || echo "(no output)"
