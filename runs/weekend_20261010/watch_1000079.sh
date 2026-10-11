#!/bin/bash
# Watch SPOT-31593C for the receipt of re-sent 1000079 (after 17:08Z) and both units' OK lines at the 18Z boot. Read-only.
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=2)
got=""
until [ "$(date -u +%H%M)" -ge 1812 ]; do
  r=$(ssh "${O[@]}" pi@192.168.1.45 'L=/home/pi/spotter_logs/SPOT-31593C/console_$(date -u +%Y%m%d).log; awk "\$1>=\"2026-10-11T17:08\"" $L | grep -a "Remote message" | grep -a 1000079 | head -1 | cut -c1-120' </dev/null 2>/dev/null)
  [ -n "$r" ] && [ -z "$got" ] && { got=1; echo "RECEIPT $r"; }
  sleep 30
done
ssh "${O[@]}" pi@192.168.1.45 'for s in SPOT-33507C SPOT-31593C; do L=/home/pi/spotter_logs/$s/console_$(date -u +%Y%m%d).log; echo "== $s"; grep -aE "\] OK id=1000079|\] OK id=1000175" $L | cut -c1-170; awk "\$1>=\"2026-10-11T17:05\" && \$1<=\"2026-10-11T17:20\"" $L | grep -aE "Checking for Rx|Running health check|Added message\(id: [0-9]+ len: (4[0-9]|5[0-9])\) to queue MS_Q_LEGACY" | cut -c22-110; done' </dev/null
[ -z "$got" ] && echo "NO RECEIPT of 1000079 on SPOT-31593C 17:08-18:12Z"
