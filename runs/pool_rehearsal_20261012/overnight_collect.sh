#!/bin/bash
# overnight_collect.sh — READ-ONLY overnight record, 10/14 12 AM .. 7 AM PDT (07Z..14Z) wakes: per wake, hourly_verify.sh
# on both units (cycle-log CFG/KEY/RAW/HEAL lines + cjxl argv/VmHWM sampler), then a media-table snapshot (run on nereus000,
# its own token) and nereus000 power. No unit or console writes. Outputs: deploy_s/<host>/hourly_<UTC>.txt,
# deploy_s/media_<UTC>.csv, deploy_s/overnight.log. Nick 10:05 PM (via EM): let it ride, no changes overnight.
cd "$(dirname "$0")"; L=deploy_s/overnight.log
for HH in 07 08 09 10 11 12 13 14; do
  T=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,14,int('$HH'),0,tzinfo=d.timezone.utc).timestamp()))")
  until [ "$(date +%s)" -ge $((T - 60)) ]; do sleep 20; done
  echo "$(date -u +%FT%TZ) wake ${HH}Z: verify both" >> $L
  for h in bmcam003 bmcam004; do bash hourly_verify.sh $h > deploy_s/$h/hourly_verify_${HH}Z.out 2>&1 & done; wait
  ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 "python3 /home/pi/hil_g4/hil_media_table.py --api https://nereus-vision-staging.onrender.com --since 2026-10-13T23:00 --until $(date -u +%FT%H:%M)" </dev/null > deploy_s/media_${HH}Z.csv 2>&1
  P=$(ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'echo VIN=$(lifepo4wered-cli get VIN) VBAT=$(lifepo4wered-cli get VBAT) IOUT=$(lifepo4wered-cli get IOUT)' </dev/null 2>/dev/null)
  echo "$(date -u +%FT%TZ) wake ${HH}Z done; nereus000 ${P:-UNREACHABLE}; media rows $(grep -c BMCAM deploy_s/media_${HH}Z.csv)" >> $L
done
