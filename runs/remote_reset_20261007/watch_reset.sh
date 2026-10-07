#!/bin/bash
# Poll SPOT-31593C's console (nereus000) from SINCE until DEADLINE for the cloud reset: a `Remote message received` line
# containing "reset" and what follows (boot banner). Saves the excerpt; exits 0 when seen (+90 s of trailing lines), 2 at
# the deadline. Read-only.
set -u
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c
R=runs/remote_reset_20261007; SINCE="$1"; DEADLINE="$2"; OUT=$R/console_reset_$3.txt
L=/home/pi/spotter_logs/SPOT-31593C/console_$(date -u +%Y%m%d).log
dl=$(python3 -c "import datetime,sys;print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$DEADLINE")
while [ "$(date +%s)" -lt "$dl" ]; do
  hit=$(ssh -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 pi@192.168.1.45 "awk '\$1>=\"$SINCE\"' $L | grep -a 'Remote message received' | grep -ai 'reset' | head -1" < /dev/null 2>/dev/null)
  if [ -n "$hit" ]; then
    t=$(echo "$hit" | cut -c1-20); sleep 90
    ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 "awk '\$1>=\"$t\"' $L | head -400" < /dev/null > $OUT 2>/dev/null
    echo "SEEN $hit"; exit 0
  fi
  sleep 20
done
echo "NO EXECUTION by $DEADLINE"; exit 2
