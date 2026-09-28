#!/bin/bash
# waitlog.sh FROM_PATTERN UNTIL_REGEX [timeout_s] [host] — S5: poll the unit's newest cycle log
# until UNTIL_REGEX appears after the first line matching FROM_PATTERN; then print the lines
# after FROM_PATTERN that matter to the ladder. Exit 1 on timeout.
FROM="$1"; UNTIL="$2"; TO="${3:-300}"; H="${4:-bmcam003}"
T0=$(date -u +%s)
until ssh -o BatchMode=yes -o ConnectTimeout=5 pi@$H "L=\$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1); sed -n '/$FROM/,\$p' \$L | grep -q -E '$UNTIL'" </dev/null 2>/dev/null; do
  sleep 6; [ $(( $(date -u +%s) - T0 )) -gt "$TO" ] && { echo "[waitlog] TIMEOUT ($UNTIL)"; exit 1; }
done
ssh -o BatchMode=yes pi@$H "L=\$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1); echo \$L; sed -n '/$FROM/,\$p' \$L | grep -E 'SUP|RUN\]|OUTPUT|saved|START|WS|ERR|exit|restart|revert|guard|CF|budget' | cut -c1-220 | head -20" </dev/null
