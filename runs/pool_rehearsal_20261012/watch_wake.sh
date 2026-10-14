#!/bin/bash
# READ-ONLY: during the wake window HH:00:30-HH:09:30 try each unit (LAN IP, then Tailscale name) every 30 s and save the
# newest cycle log once its transmit is done. Usage: bash watch_wake.sh <UTC hour, e.g. 2026-10-14T00>
set -u
cd "$(dirname "$0")"; W=$1; OUT=wake_${W//[:]/}; mkdir -p $OUT
O=(-o BatchMode=yes -o ConnectTimeout=4 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)
t0=$(python3 -c "import datetime as d,sys;print(int(d.datetime.fromisoformat(sys.argv[1]+':00:30+00:00').timestamp()))" $W)
until [ "$(date +%s)" -ge $t0 ]; do sleep 10; done
end=$((t0 + 540))
while [ "$(date +%s)" -lt $end ]; do
  for u in "bmcam003 192.168.1.230" "bmcam004 192.168.1.143"; do set -- $u
    for addr in $2 $1; do
      ssh "${O[@]}" pi@$addr 'cd /home/pi/BM_Devel_Pi; f=$(ls -t cron_logs/rc_cycle_*.log | head -1); echo "ADDR '$addr' $(date -u +%FT%TZ) up=$(cut -d. -f1 /proc/uptime)s LOG $f"; cat $f' </dev/null > $OUT/.tmp_$1 2>/dev/null && [ -s $OUT/.tmp_$1 ] && { mv $OUT/.tmp_$1 $OUT/cycle_$1.log; break; }
    done
  done
  sleep 30
done
for h in bmcam003 bmcam004; do f=$OUT/cycle_$h.log; echo "== $h"; [ -f $f ] || { echo "not reached"; continue; }; head -1 $f | cut -c1-120; grep -m1 "\[CFG\] config v2" $f | grep -o "hash=[0-9a-f]*"; grep -E "media key|\[KEY\]|nrjxl ready|FALLBACK|START|END|cpu_temp|CPU temp|temp_c|halt uptime|post-transmit listen window:|ERR|Traceback" $f | cut -c1-150 | head -10; done
