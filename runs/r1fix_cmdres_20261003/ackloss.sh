#!/bin/bash
# R1G deliberate ack-loss stress on BMCAM_003 (EM GO 2026-10-04 ~08:10 PDT). A 3rd command (get) in an even-hour :20 slot
# with the driver set + trg; up to 3 slots (16, 20, 00Z) until one ORIGINAL ack is missing at the backend. Log: ackloss.log
L=/home/pi/hil_r1/ackloss.log; API=https://nereus-vision-staging.onrender.com
at() { T=$(python3 -c "import datetime as d;print(int((d.datetime(2026,10,4,tzinfo=d.timezone.utc)+d.timedelta(hours=$1,minutes=$2,seconds=$3)).timestamp()))"); until [ "$(date +%s)" -ge "$T" ]; do sleep 10; done; }
for H in 16 20 24; do
  at $H 22 40
  R=$(python3 /home/pi/hil_r1/hil_backend_cmd.py --api $API --device BMCAM_003 --json "{\"c\":\"get\",\"k\":[\"mode.media\"]}" 2>&1 | tail -1)
  echo "$(date -u +%FT%TZ) R1G ACK-LOSS STRESS slot +${H}h:20 (deliberate, not counted against R1G.1): 3rd command get -> $R" >> $L
  at $((H+2)) 45 0
  set -a; . ~/.config/nereus/heal_driver.env; set +a
  OUT=$(curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "$API/admin/devices/BMCAM_003/commands?limit=12" | python3 -c "
import sys,json,datetime as d
j=json.load(sys.stdin); lo=d.datetime(2026,10,4,tzinfo=d.timezone.utc)+d.timedelta(hours=$H,minutes=19)
for r in j[\"commands\"]:
    c=d.datetime.fromisoformat(r[\"created_at\"].replace(\"Z\",\"+00:00\"))
    if lo<=c<lo+d.timedelta(minutes=5): print(r[\"command_id\"], r[\"verb\"], r[\"status\"], \"ack\" if r.get(\"ack\") else \"NO-ACK\", (r.get(\"ack\") or {}).get(\"d\"))
")
  echo "$(date -u +%FT%TZ) slot +${H}h:20 backend after the reply wake: $(echo "$OUT" | tr "\n" ";")" >> $L
  echo "$OUT" | grep -q NO-ACK && { echo "$(date -u +%FT%TZ) LOST-ACK seen in slot +${H}h:20 -> check d:1 recovery at the next wake" >> $L; exit 0; }
done
echo "$(date -u +%FT%TZ) no original ack lost in 3 slots" >> $L
