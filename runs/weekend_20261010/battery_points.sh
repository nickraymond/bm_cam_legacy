#!/bin/bash
# battery_points.sh: READ-ONLY. Print each Spotter's battery points (Sofar telemetry via rig health) since $1 (UTC ISO prefix).
# Source: nereus000:/home/pi/hil_health/health.csv (SPOT-31593C cols 0-2, SPOT-33507C cols 12-14: batt_at, batt_v, batt_w).
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'tail -n 600 /home/pi/hil_health/health.csv' </dev/null | python3 -c "
import csv,sys,datetime as d
since=sys.argv[1]; seen=set()
for r in csv.reader(sys.stdin):
  for s,(a,v,w) in {'SPOT-31593C':(0,1,2),'SPOT-33507C':(12,13,14)}.items():
    if len(r)>w and r[a][:4]=='2026' and r[a]>=since and (s,r[a]) not in seen:
      seen.add((s,r[a])); t=d.datetime.fromisoformat(r[a])-d.timedelta(hours=7); print(s, t.strftime('%a %I:%M %p PDT'), r[v],'V', r[w],'W')
" "$1" | sort -k1,1 -s
