#!/bin/bash
# PRUNE gate, read-only: during wake HH (UTC, date D), repeatedly save bmcam004's sent/ listing + the newest cycle log's
# [KEY]/[HEAL]/START lines until the Pi halts; the LAST good read = end-of-wake state. Script piped via stdin (no file
# written on the Pi). Usage: eow_collect.sh YYYY-MM-DD HH
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
D=$1; HH=$2; R=runs/prune_gate_20261007; mkdir -p $R/sent_lists $R/pulled
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=2)
t=$(python3 -c "import datetime as d,sys;print(int(d.datetime.fromisoformat(sys.argv[1]+'T'+sys.argv[2]+':02:00+00:00').timestamp()))" $D $HH)
until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
end=$(( t + 600 )); ok=0
while [ "$(date +%s)" -lt "$end" ]; do
  if ssh "${O[@]}" pi@bmcam004 'cd /home/pi/BM_Devel_Pi && python3 -' < /private/tmp/claude-501/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy--claude-worktrees-sprint26-s3a-runtime-parity-092958/3cd7e446-c576-4dd7-8494-833abcb29a17/scratchpad/sent_list.py > $R/sent_lists/.tmp 2>/dev/null && [ -s $R/sent_lists/.tmp ]; then
    mv $R/sent_lists/.tmp $R/sent_lists/${HH}Z_end.tsv; ok=$((ok+1))
    ssh "${O[@]}" pi@bmcam004 'cd /home/pi/BM_Devel_Pi; f=$(ls -t cron_logs/rc_cycle_*.log | head -1); echo "LOG $f"; cat software_sha.txt | head -c 40; echo; cat "$f"' < /dev/null > $R/pulled/.cyc 2>/dev/null && mv $R/pulled/.cyc $R/pulled/cycle_${HH}Z.log
  fi
  sleep 15
done
echo "wake ${HH}Z: $ok good reads; listing $(wc -l < $R/sent_lists/${HH}Z_end.tsv 2>/dev/null) records"
grep -nE "\[KEY\]|\[HEAL\] sent|START|halt" $R/pulled/cycle_${HH}Z.log 2>/dev/null | cut -c1-200 | head -20
