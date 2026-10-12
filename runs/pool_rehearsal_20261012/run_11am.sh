#!/bin/bash
# Wait for the 18Z (11 AM PDT) wake, then run readonly_check.sh on both units once each answers.
cd "$(dirname "$0")"
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,12,18,0,40,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for H in bmcam003 bmcam004; do
  for i in $(seq 1 20); do ssh -o BatchMode=yes -o ConnectTimeout=3 pi@$H true </dev/null 2>/dev/null && break; sleep 4; done
  bash readonly_check.sh $H
done
