#!/bin/bash
# hil_console.sh — send ONE Spotter USB-console line through the monitor host's
# spotter-monitor (cmd.txt) and print the console lines that followed.
#
# Purpose:  the only way HIL tools talk to a Spotter console (bm pub, bridge cfg, post, ...).
# Origin:   adapted copy of runs/s5_console_20260928/console.sh (original left in place).
# Inputs:   $1 SPOT-ID, $2 the console line, $3 wait seconds after it is consumed (default 6)
#           env HIL_MONITOR (ssh target, e.g. pi@192.168.1.45), HIL_MONITOR_LOG_ROOT
#           (default /home/pi/spotter_logs), HIL_RUN_DIR (local run folder, default .)
# Outputs:  stdout = console lines after the send (power ticks filtered, cut to 220 chars);
#           appended to $HIL_RUN_DIR/commands.log with a UTC timestamp.
# Example:  HIL_RUN_DIR=runs/g3_hardmode_20261003 hil/tools/hil_console.sh SPOT-33507C 'post' 4
# Limits:   the line must not contain single quotes; the Spotter takes <= 256 B per line
#           incl. newline (longer lines are refused here, they overflow the Spotter RX).
#           Prints NOT-CONSUMED if the monitor did not pick cmd.txt up within 10 s
#           (monitor down?) and exits 3; exits 4 when ssh to the monitor fails.
set -u
SPOT="${1:?SPOT-ID}"; CMD="${2:?console line}"; W="${3:-6}"
MON="${HIL_MONITOR:?set HIL_MONITOR (source hil/hil.env)}"
ROOT="${HIL_MONITOR_LOG_ROOT:-/home/pi/spotter_logs}"
RUN="${HIL_RUN_DIR:-.}"; mkdir -p "$RUN"
case "$CMD" in *"'"*) echo "[hil_console] refusing: single quote in the line" >&2; exit 2;; esac
LEN=$(( $(printf '%s' "$CMD" | wc -c) + 1 ))
if [ "$LEN" -gt 256 ]; then echo "[hil_console] refusing: ${LEN} B line > 256 B Spotter limit" >&2; exit 2; fi
echo "$(date -u +%FT%TZ) $SPOT > $CMD" >> "$RUN/commands.log"
OUT=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$MON" "D=$ROOT/$SPOT; L=\$D/console_\$(date -u +%Y%m%d).log; n=\$(wc -l < \$L); printf '%s\n' '$CMD' > \$D/cmd.txt; for i in \$(seq 1 20); do [ -s \$D/cmd.txt ] || break; sleep 0.5; done; [ -s \$D/cmd.txt ] && echo NOT-CONSUMED; sleep $W; tail -n +\$((n+1)) \$L | grep -v ', power |' | cut -c1-220" < /dev/null 2>&1)
RC=$?
printf '%s\n' "$OUT" | tee -a "$RUN/commands.log"
[ $RC -ne 0 ] && { echo "[hil_console] ssh to $MON failed (rc=$RC)" >&2; exit 4; }
printf '%s\n' "$OUT" | grep -q '^NOT-CONSUMED' && { echo "[hil_console] monitor did not consume cmd.txt" >&2; exit 3; }
exit 0
