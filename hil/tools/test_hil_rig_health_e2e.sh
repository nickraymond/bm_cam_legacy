#!/bin/bash
# test_hil_rig_health_e2e.sh — end-to-end run of a CANDIDATE hil_rig_health.py on nereus000 in an isolated --out dir:
# copies the real state at HH:06 (so the run reads the HH:05-HH:10 console incl. the bus-off edge), runs at HH:10:45 with
# --api "" (no backend calls) and a no-op `logger` on PATH (no journald alerts). Never touches /home/pi/hil_health.
# Usage (Mac): bash hil/tools/test_hil_rig_health_e2e.sh HH   -> prints rc, bus fields, any bus WARN
set -u
HH=$(printf %02d $((10#$1))); D=/tmp/rh_test
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)
cd "$(dirname "$0")/../.."
ssh "${O[@]}" pi@192.168.1.45 "rm -rf $D && mkdir -p $D/bin && printf '#!/bin/sh\nexit 0\n' > $D/bin/logger && chmod +x $D/bin/logger" </dev/null
scp -q "${O[@]}" hil/tools/hil_rig_health.py hil/tools/hil_rig_events.py pi@192.168.1.45:$D/ </dev/null
at() { local t; t=$(python3 -c "import datetime as d,sys;n=d.datetime.now(d.timezone.utc);print(int(n.replace(hour=int(sys.argv[1]),minute=int(sys.argv[2]),second=int(sys.argv[3]),microsecond=0).timestamp()))" $HH $1 $2); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done; }
at 6 0
ssh "${O[@]}" pi@192.168.1.45 "cp -p /home/pi/hil_health/state.json /home/pi/hil_health/alerts.json /home/pi/hil_health/config.json $D/ && echo state copied \$(date -u +%T)" </dev/null
at 10 45
ssh "${O[@]}" pi@192.168.1.45 "cd $D && PATH=$D/bin:\$PATH python3 $D/hil_rig_health.py --config $D/config.json --out $D --api '' ; echo RC=\$?; python3 -c \"import json;a=json.load(open('$D/alerts.json'));print('LEVEL',a.get('level'),'WARN',a.get('warn'),'CRIT',a.get('crit'));r=a.get('R') or a;print({k:v for k,v in r.items() if 'bus_v' in k})\" 2>&1 | head -5; tail -3 $D/ALERTS.log 2>/dev/null" </dev/null
