#!/bin/bash
# console.sh SPOT "command" [wait_s] — send ONE Spotter console command through
# nereus000's spotter-monitor (cmd.txt), wait until the monitor consumed it, then
# print the console lines that followed (power ticks filtered). Logged locally in
# console_commands.log. Commands must not contain single quotes.
SPOT="$1"; CMD="$2"; W="${3:-6}"
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "$(date -u +%FT%TZ) $SPOT > $CMD" >> "$HERE/console_commands.log"
ssh -o BatchMode=yes pi@192.168.1.45 "D=/home/pi/spotter_logs/$SPOT; L=\$D/console_\$(date -u +%Y%m%d).log; n=\$(wc -l < \$L); printf '%s\n' '$CMD' > \$D/cmd.txt; for i in \$(seq 1 20); do [ -s \$D/cmd.txt ] || break; sleep 0.5; done; [ -s \$D/cmd.txt ] && echo NOT-CONSUMED; sleep $W; tail -n +\$((n+1)) \$L | grep -v ', power |' | cut -c1-220" < /dev/null 2>&1 | tee -a "$HERE/console_commands.log"
