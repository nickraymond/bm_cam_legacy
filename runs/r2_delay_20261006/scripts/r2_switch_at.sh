#!/bin/bash
# Run the R2 arm switch at HH:01:30Z (inside bmcam003's wake, after its send decision at ~uptime 35-60 s, before an
# A wake halts at ~:05). Launched in the background ~10 min earlier so cron lateness does not matter.
# Usage: r2_switch_at.sh HH [record-prev-HH]   (record-prev: first record wake PREV from the 2nd newest log, no write)
set -u
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c
HH=$((10#$1)); PREV="${2:-}"; ST=runs/r2_delay_20261006/schedule.json; G=runs/r2_delay_20261006/gate.log
T=$(python3 -c "import datetime as d,sys; n=d.datetime.now(d.timezone.utc); t=n.replace(hour=int(sys.argv[1]),minute=1,second=30,microsecond=0); t=t if t>n-d.timedelta(minutes=30) else t+d.timedelta(days=1); print(int(t.timestamp()))" $HH)
until [ "$(date +%s)" -ge "$T" ]; do sleep 5; done
if [ -n "$PREV" ]; then python3 hil/tools/hil_r2_switch.py --state $ST --wake $((10#$PREV)) --nth 2 --no-write 2>&1 | sed "s/^/$(date -u +%FT%TZ) /" | tee -a $G; fi
out=$(python3 hil/tools/hil_r2_switch.py --state $ST --wake $HH 2>&1); rc=$?
if [ $rc = 4 ]; then sleep 60; out=$(python3 hil/tools/hil_r2_switch.py --state $ST --wake $HH 2>&1); rc=$?; fi
echo "$(date -u +%FT%TZ) $out (rc $rc)" | tee -a $G
git add -f $ST $G >/dev/null 2>&1 && git commit -qm "R2 switch wake ${HH}Z (rc $rc)" >/dev/null 2>&1 && git push -q >/dev/null 2>&1
exit $rc
