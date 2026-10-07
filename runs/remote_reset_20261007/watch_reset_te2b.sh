#!/bin/bash
# TE2 v2 of watch_reset.sh. Poll SPOT-31593C's console (nereus000) from SINCE to DEADLINE for a Spotter reboot after a cloud
# reset. Matches EITHER 'Remote message received … reset' OR 'Reset Reason:' (reset #2 rebooted with no receipt line logged).
# The log file name is re-computed every poll (UTC date rollover at 5 PM PDT). Saves the excerpt from 120 s before the hit to
# +150 s. Exit 0 when SEEN, 2 at the deadline. Read-only. Usage: watch_reset_te2b.sh <sinceZ> <deadlineZ> <tag>
set -u
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/remote_reset_20261007; SINCE="$1"; DEADLINE="$2"; OUT=$R/console_reset_$3.txt
O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=2)
dl=$(python3 -c "import datetime,sys;print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$DEADLINE")
while [ "$(date +%s)" -lt "$dl" ]; do
  L=/home/pi/spotter_logs/SPOT-31593C/console_$(date -u +%Y%m%d).log
  hit=$(ssh "${O[@]}" pi@192.168.1.45 "awk '\$1>=\"$SINCE\"' $L | grep -aE 'Remote message received.*\"reset|Reset Reason:' | head -1" < /dev/null 2>/dev/null)
  if [ -n "$hit" ]; then
    t=$(echo "$hit" | cut -c1-20)
    t0=$(python3 -c "import datetime as d,sys;print((d.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00'))-d.timedelta(seconds=120)).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$t")
    sleep 150
    ssh "${O[@]}" pi@192.168.1.45 "awk '\$1>=\"$t0\"' $L | head -600" < /dev/null > $OUT 2>/dev/null
    echo "SEEN $hit"; grep -a 'Remote message received' $OUT | grep -ai reset | head -2; exit 0
  fi
  sleep 20
done
echo "NO EXECUTION by $DEADLINE"; exit 2
