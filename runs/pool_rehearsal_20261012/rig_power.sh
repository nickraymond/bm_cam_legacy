#!/bin/bash
# READ-ONLY: one line of nereus000 power + Spotter battery from rig health (by header name). Usage: bash rig_power.sh
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'tail -n 1 /home/pi/hil_health/health.csv; head -1 /home/pi/hil_health/health.csv' </dev/null | python3 -c "
import csv,sys
rows=list(csv.reader(sys.stdin)); r,h=rows[0],rows[1]; d=dict(zip(h,r))
print(d['ts'][11:19]+'Z', 'n000 VIN',d['lp_vin_mv'],'IOUT',d['lp_iout_ma'],'VBAT',d['lp_vbat_mv'],d['lp_soc_est'],'ext',d['lp_external_power'],'| SPOT-31593C',d['SPOT-31593C_batt_v'],'V',d['SPOT-31593C_batt_w'],'W @',d['SPOT-31593C_batt_at'][11:16],'| SPOT-33507C',d['SPOT-33507C_batt_v'],'V',d['SPOT-33507C_batt_w'],'W @',d['SPOT-33507C_batt_at'][11:16])"
