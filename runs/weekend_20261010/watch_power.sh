#!/bin/bash
# watch_power.sh: READ-ONLY. Poll nereus000 rig-health health.csv every 5 min until a Spotter's in_v > 4.5 V (USB power
# back) or batt_v < 3.90 V (EM threshold, 2026-10-12), or $1 minutes pass. Prints the trigger line and exits.
# Columns: SPOT-31593C batt_v 1, in_v 9; SPOT-33507C batt_v 13, in_v 21 (header of health.csv).
END=$(( $(date +%s) + ${1:-95}*60 ))
while [ "$(date +%s)" -lt $END ]; do
  L=$(ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'tail -1 /home/pi/hil_health/health.csv' </dev/null)
  out=$(python3 -c "
import csv,sys
r=next(csv.reader([sys.argv[1]]))
v={'SPOT-31593C':(r[1],r[9]),'SPOT-33507C':(r[13],r[21])}
msg=[]
for s,(b,i) in v.items():
  try:
    if i and float(i)>4.5: msg.append(f'{s} in_v {i} V (power back)')
    if b and float(b)<3.90: msg.append(f'{s} batt {b} V (< 3.90)')
  except ValueError: pass
print(r[-2] if len(r)>2 else '', '|', ' ; '.join(msg), '| now', {s:x for s,x in v.items()})
" "$L" 2>&1)
  echo "$(date -u +%FT%TZ) $out" >> "$(dirname "$0")/power_watch.log"
  case "$out" in *"power back"*|*"< 3.90"*) echo "TRIGGER $out"; exit 0;; esac
  sleep 300
done
echo "no trigger; last: $out"
