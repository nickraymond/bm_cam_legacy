#!/bin/bash
# T-FOCUS deploy: development cbdea93 (#164 focus sweep) Pi side to both units. Per unit: backup *.prefocus -> scp
# pool_sequence.py + rc_focus_sweep.py -> sha -> pool_mode stop (wait) -> start (fresh run; ~/pool_args lp 0.8 kept).
# Then console status (lp=0.8 cal=0/0) + pool_sha_check vs cbdea93. Output: focus_deploy_<UTC>.txt
set -u
cd "$(dirname "$0")"; RUN=$PWD; SRC=${1:?source tree}
O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
OUT=$RUN/focus_deploy_$(date -u +%Y%m%dT%H%MZ).txt
{
for IP in 192.168.1.230 192.168.1.143; do
  echo "== $(date -u +%FT%TZ) $IP"
  ssh "${O[@]}" pi@$IP 'cd /home/pi/BM_Devel_Pi; cp -p pool_sequence.py pool_sequence.py.prefocus; [ -f rc_focus_sweep.py ] && cp -p rc_focus_sweep.py rc_focus_sweep.py.prefocus; true' </dev/null
  scp -q "${O[@]}" "$SRC/BM_Devel_Pi/pool_sequence.py" "$SRC/BM_Devel_Pi/rc_focus_sweep.py" pi@$IP:/home/pi/BM_Devel_Pi/ && echo "scp rc=0"
  ssh "${O[@]}" pi@$IP 'cd /home/pi/BM_Devel_Pi; sha256sum pool_sequence.py rc_focus_sweep.py /home/pi/pool_mode.sh | cut -c1-12,65-; cat ~/pool_args; bash /home/pi/pool_mode.sh stop 2>&1 | tail -1; for i in $(seq 1 40); do bash /home/pi/pool_mode.sh status 2>&1 | grep -q "pool process: not running" && break; sleep 5; done; bash /home/pi/pool_mode.sh start 2>&1 | tail -1; sleep 12; bash /home/pi/pool_mode.sh status 2>&1 | grep -E "newest run|cfg="' </dev/null
done
echo "== console status"
ssh "${O[@]}" pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both status --wait 30' </dev/null 2>&1 | grep -E "OK id|ERR" | grep -v "\.\. 20"
echo "== sha check vs cbdea93"
(cd ../..; bash $SRC/tools/pool_sha_check.sh --ref cbdea93 --unit pi@192.168.1.230 --unit pi@192.168.1.143 2>&1 | tail -10)
for IP in 192.168.1.230 192.168.1.143; do ssh "${O[@]}" pi@$IP 'sha256sum /home/pi/BM_Devel_Pi/rc_focus_sweep.py | cut -c1-12' </dev/null | sed "s/^/rc_focus_sweep.py $IP /"; done
} > "$OUT" 2>&1
echo "done -> $OUT"; cut -c1-220 "$OUT"
