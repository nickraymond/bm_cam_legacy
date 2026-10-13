#!/bin/bash
# Wait (detached) until both units log "loop 18/18", then run tendure_pull.sh (EM call (b)). Read-only on the units.
cd "$(dirname "$0")"; caffeinate -i -w $$ &
O=(-o BatchMode=yes -o ConnectTimeout=5)
done18(){ ssh "${O[@]}" pi@$1 'r=$(ls -td /home/pi/pool_runs/*/ | head -1); grep -c "loop 18/18" $r/pool.log' </dev/null 2>/dev/null; }
until [ "$(done18 192.168.1.230)" = 1 ] && [ "$(done18 192.168.1.143)" = 1 ]; do [ -f STOP ] && exit 1; sleep 60; done
echo "$(date -u +%FT%TZ) both 18/18 -> pull" >> driver.log
sleep 30; bash tendure_pull.sh
