#!/bin/bash
# T-DRY step A (bmcam004, 12 PM wake window): (1) READ-ONLY pre-pool snapshot of the ARMED production state,
# (2) deploy the 3 new pool files from bm #152 @ 36a4e67 (additive: they do not exist on the unit yet), verify sha256,
# (3) pool_sequence.py --print-config (read-only). Output: tdry_a_<UTC>.txt
set -u
cd "$(dirname "$0")"
H=bmcam004; SRC=/private/tmp/claude-501/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy--claude-worktrees-sprint26-s3a-runtime-parity-092958/3cd7e446-c576-4dd7-8494-833abcb29a17/scratchpad/pr152
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
OUT=tdry_a_$(date -u +%Y%m%dT%H%MZ).txt
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,12,19,3,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 20); do ssh "${O[@]}" pi@$H true </dev/null 2>/dev/null && break; sleep 4; done
{
echo "== PRE-POOL SNAPSHOT (armed production) $(date -u +%FT%TZ)"
ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; head -c 12 software_sha.txt; echo; crontab -l; sha256sum camera_config.yaml camera_schedule.yaml bm_command_state_v2.json bm_command_state.json 2>/dev/null; ls pool_sequence.py rc_preview.py /home/pi/pool_mode.sh 2>&1; df -BM / | tail -1' </dev/null
echo "== DEPLOY $(date -u +%FT%TZ)"
scp -q "${O[@]}" "$SRC/BM_Devel_Pi/pool_sequence.py" "$SRC/BM_Devel_Pi/rc_preview.py" pi@$H:/home/pi/BM_Devel_Pi/ && \
scp -q "${O[@]}" "$SRC/tools/pool_mode.sh" pi@$H:/home/pi/pool_mode.sh && echo "scp rc=0"
ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; sha256sum pool_sequence.py rc_preview.py /home/pi/pool_mode.sh | cut -c1-12,65-; echo "== PRINT-CONFIG"; timeout 60 python3 pool_sequence.py --print-config 2>&1 | tail -40' </dev/null
echo "== expected sha: 88bf3e706d60 pool_sequence.py, 027fd06e0359 rc_preview.py, 87d9e549ed26 pool_mode.sh"
} > "$OUT" 2>&1
echo "done -> $OUT"
