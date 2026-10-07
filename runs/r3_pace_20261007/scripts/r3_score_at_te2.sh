#!/bin/bash
# TE2: wait for HH:16Z, pull bmcam003's wake HH console + score it (arm from schedule.json history). Exits so the session is
# notified; the RESULTS row + EM line are written by the session. Usage: r3_score_at_te2.sh HH
set -u
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
HH=$(printf %02d $((10#$1))); R=runs/r3_pace_20261007
T=$(python3 -c "import datetime as d,sys; n=d.datetime.now(d.timezone.utc); t=n.replace(hour=int(sys.argv[1]),minute=16,second=0,microsecond=0); t=t if t>n-d.timedelta(minutes=50) else t+d.timedelta(days=1); print(int(t.timestamp()), t.strftime('%Y-%m-%d'))" $HH)
TS=${T% *}; DAY=${T#* }
until [ "$(date +%s)" -ge "$TS" ]; do sleep 10; done
ARM=$(python3 -c "import json,sys;h=[x for x in json.load(open('$R/schedule.json'))['history'] if x['wake_utc_hour']==int(sys.argv[1])];print(h[-1]['arm'] if h else '?')" $HH)
HIL_RUN_DIR=$R hil/tools/hil_wake_report.sh SPOT-33507C ${DAY}T$HH:00 2>&1 | tail -3
python3 hil/tools/hil_r2_score.py --console $R/console/wake_SPOT-33507C_${DAY}T${HH}00.txt --arm $ARM --out-json $R/analysis/wake_$HH.json 2>&1 | grep -v -i deprecat | tail -3
