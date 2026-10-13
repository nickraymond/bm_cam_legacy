#!/bin/bash
# Tue 10/13 ~8:00 AM contingency (EM): units still in pool mode, bus held (plan-E restore never ran). Per unit via ssh:
# pool_mode stop (wait) -> start (fresh run folder; the T-ENDURE run would refuse a new cal) -> status from pool.log;
# then pool_sha_check vs origin/development. Console status via pool_cmd if nereus000 is reachable. Output: tue_am_<UTC>.txt
set -u
cd "$(dirname "$0")"; RUN=$PWD
O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
OUT=$RUN/tue_am_$(date -u +%Y%m%dT%H%MZ).txt
{
for IP in 192.168.1.230 192.168.1.143; do
  echo "== $(date -u +%FT%TZ) $IP"
  ssh "${O[@]}" pi@$IP 'bash /home/pi/pool_mode.sh stop 2>&1 | tail -2; for i in $(seq 1 40); do pgrep -f "^[^ ]*python3? [^ ]*pool_sequence[.]py" >/dev/null || break; sleep 5; done; bash /home/pi/pool_mode.sh start 2>&1 | tail -2; sleep 10; bash /home/pi/pool_mode.sh status 2>&1 | tail -5; crontab -l' </dev/null
done
(cd ../..; git fetch -q origin; bash /private/tmp/claude-501/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy--claude-worktrees-sprint26-s3a-runtime-parity-092958/3cd7e446-c576-4dd7-8494-833abcb29a17/scratchpad/dev_pull/tools/pool_sha_check.sh --ref origin/development --unit pi@192.168.1.230 --unit pi@192.168.1.143)
if ping -c 1 -t 3 192.168.1.45 >/dev/null 2>&1; then ssh "${O[@]}" pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both status --wait 30' </dev/null; else echo "nereus000 unreachable: console status skipped"; fi
} > "$OUT" 2>&1
echo "done -> $OUT"; grep -E "^==|newest run|cfg=|MATCH|DIFF|STOP|ALL MATCH|OK id|unreachable" "$OUT"
