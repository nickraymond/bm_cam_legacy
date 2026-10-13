#!/bin/bash
# Deploy bm #160 (pool focus LensPosition 0.8) to both units and restart pool mode with fresh runs. Run on the EM's call / QE PASS.
# Per unit: backup current pool files (*.pre160) -> scp pool_sequence.py + pool_mode.sh -> sha check -> pool_mode.sh args
# --lens-position 0.8 -> stop (wait not running) -> start -> status. Then console status (expect lp=0.8) + preview + sha check.
# Usage: bash lp08_deploy.sh <SRC tree at the QE-passed sha>
set -u
cd "$(dirname "$0")"; RUN=$PWD; SRC=${1:?source tree}
O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
OUT=$RUN/lp08_$(date -u +%Y%m%dT%H%MZ).txt
{
echo "== src $(cd $SRC && shasum -a 256 BM_Devel_Pi/pool_sequence.py tools/pool_mode.sh | awk '{print substr($1,1,12), $2}')"
for IP in 192.168.1.230 192.168.1.143; do
  echo "== $(date -u +%FT%TZ) $IP"
  ssh "${O[@]}" pi@$IP 'cp -p /home/pi/BM_Devel_Pi/pool_sequence.py /home/pi/BM_Devel_Pi/pool_sequence.py.pre160; cp -p /home/pi/pool_mode.sh /home/pi/pool_mode.sh.pre160' </dev/null
  scp -q "${O[@]}" "$SRC/BM_Devel_Pi/pool_sequence.py" pi@$IP:/home/pi/BM_Devel_Pi/ && scp -q "${O[@]}" "$SRC/tools/pool_mode.sh" pi@$IP:/home/pi/pool_mode.sh && echo "scp rc=0"
  ssh "${O[@]}" pi@$IP 'sha256sum /home/pi/BM_Devel_Pi/pool_sequence.py /home/pi/pool_mode.sh | cut -c1-12,65-; bash /home/pi/pool_mode.sh args --lens-position 0.8 2>&1 | tail -3; cat ~/pool_args 2>/dev/null; bash /home/pi/pool_mode.sh stop 2>&1 | tail -1; for i in $(seq 1 40); do bash /home/pi/pool_mode.sh status 2>&1 | grep -q "pool process: not running" && break; sleep 5; done; bash /home/pi/pool_mode.sh start 2>&1 | tail -1; sleep 12; bash /home/pi/pool_mode.sh status 2>&1 | tail -3; grep -h "pool ready" $(ls -td /home/pi/pool_runs/*/ | head -1)/pool.log | tail -1 | cut -c1-200' </dev/null
done
echo "== console status + preview"
ssh "${O[@]}" pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both status --wait 30; python3 /home/pi/pool_tools/pool_cmd.py both preview --wait 30' </dev/null 2>&1 | grep -E "OK id|ERR" | grep -v "\.\. 20"
} > "$OUT" 2>&1
echo "done -> $OUT"; cat "$OUT" | cut -c1-220
