#!/bin/bash
# T-LAND deploy: bm development f8caaa1 pool files (pool_sequence.py 5fe40e14, rc_preview.py 571b6b45, pool_mode.sh 031dc0ca)
# onto bmcam003 + bmcam004 in a wake window (additive; the RC runtime is unchanged: dev BM_Devel_Pi == 9ec4cb7 + these 2 files).
# Old T-DRY copies on 004 are kept as *.36a4e67. Then --print-config (read-only). Output: tland_deploy_<UTC>.txt
set -u
cd "$(dirname "$0")"
SRC=/private/tmp/claude-501/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy--claude-worktrees-sprint26-s3a-runtime-parity-092958/3cd7e446-c576-4dd7-8494-833abcb29a17/scratchpad/dev_f8caaa1
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
OUT=tland_deploy_$(date -u +%Y%m%dT%H%MZ).txt
{
for H in bmcam003 bmcam004; do
  for i in $(seq 1 30); do ssh "${O[@]}" pi@$H true </dev/null 2>/dev/null && break; sleep 4; done
  echo "== $H $(date -u +%FT%TZ)"
  ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; for f in pool_sequence.py rc_preview.py; do [ -f $f ] && cp -p $f $f.36a4e67; done; [ -f /home/pi/pool_mode.sh ] && cp -p /home/pi/pool_mode.sh /home/pi/pool_mode.sh.36a4e67; true' </dev/null
  scp -q "${O[@]}" "$SRC/BM_Devel_Pi/pool_sequence.py" "$SRC/BM_Devel_Pi/rc_preview.py" pi@$H:/home/pi/BM_Devel_Pi/ && scp -q "${O[@]}" "$SRC/tools/pool_mode.sh" pi@$H:/home/pi/pool_mode.sh && echo "scp rc=0"
  ssh "${O[@]}" pi@$H 'cd /home/pi/BM_Devel_Pi; sha256sum pool_sequence.py rc_preview.py /home/pi/pool_mode.sh | cut -c1-12,65-; timeout 60 python3 pool_sequence.py --print-config 2>&1 | grep -oE "\"focus\": \{[^}]*\}|POOL_CONFIG_HASH=\S+|\"exposure_profile\": \"[a-z_]+\"|hash=[0-9a-f]+" | sort -u' </dev/null
done
echo "== expected: 5fe40e143442 pool_sequence.py, 571b6b4564f8 rc_preview.py, 031dc0caa7e2 pool_mode.sh"
} > "$OUT" 2>&1
echo "done -> $OUT"; cat "$OUT"
