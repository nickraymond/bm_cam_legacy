#!/bin/bash
# nereus000 power watch for the fdeb01a held-bus deploy: every 60 s read VIN/VBAT/IOUT; every 5th sample -> gate.log;
# writes deploy_s/ABORT when VBAT < 3100 mV, VIN < 4498 mV (external power dropout), or 3 unreachable reads in a row.
cd "$(dirname "$0")"; F=power_watch_s147.log; miss=0; n=0
while [ ! -f deploy_s/WATCH_STOP ]; do
  v=$(ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'echo $(lifepo4wered-cli get VIN) $(lifepo4wered-cli get VBAT) $(lifepo4wered-cli get IOUT)' </dev/null 2>/dev/null)
  set -- $v; ts=$(date -u +%FT%TZ)
  if [ -z "${2:-}" ]; then miss=$((miss+1)); echo "$ts unreachable ($miss)" >> $F; [ $miss -ge 3 ] && { echo "$ts nereus000 unreachable x3" > deploy_s/ABORT; }
  else miss=0; echo "$ts VIN=$1 VBAT=$2 IOUT=$3" >> $F
    [ $((n % 5)) = 0 ] && echo "$ts [TE2 power] nereus000 VIN=$1 VBAT=$2 IOUT=$3" >> gate.log
    [ "$2" -lt 3100 ] && echo "$ts VBAT $2 < 3100" > deploy_s/ABORT
    [ "$1" -lt 4498 ] && echo "$ts VIN $1 < 4498 (dropout)" > deploy_s/ABORT
  fi; n=$((n+1)); sleep 60
done
