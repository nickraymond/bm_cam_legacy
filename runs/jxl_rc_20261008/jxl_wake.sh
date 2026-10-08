#!/bin/bash
# JXL-RC per wake (copy of reef_wake.sh) (read-only): from HH:01:30Z keep pulling bmcam003's newest cycle log until the Pi halts (last good copy
# = the wake's log; first wake also pulls the v1 camera_schedule.yaml + its sha256), then at HH:16Z the SPOT-33507C console
# wake report + hil_r2_score.py. Prints the [CFG]/[KEY]/[HEAL]/[CMD] lines + the score JSON. Usage: reef_wake.sh YYYY-MM-DD HH
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
D=$1; HH=$2; R=runs/jxl_rc_20261008; mkdir -p $R/pulled $R/console $R/analysis
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
caffeinate -i -w $$ &
at() { local t; t=$(python3 -c "import datetime as d,sys;print(int(d.datetime.fromisoformat(sys.argv[1]+'+00:00').timestamp()))" "$1"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done; }
at ${D}T$HH:01:30
end=$(( $(date +%s) + 540 )); ok=0
while [ "$(date +%s)" -lt "$end" ]; do
  if ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; f=$(ls -t cron_logs/rc_cycle_*.log | head -1); echo "LOG $f"; head -c 40 software_sha.txt; echo; cat "$f"' </dev/null > $R/pulled/.cyc 2>/dev/null && [ -s $R/pulled/.cyc ]; then
    mv $R/pulled/.cyc $R/pulled/cycle_${HH}Z.log; ok=$((ok+1))
    mkdir -p $R/sent_lists; ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi && python3 -' < /private/tmp/claude-501/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy--claude-worktrees-sprint26-s3a-runtime-parity-092958/3cd7e446-c576-4dd7-8494-833abcb29a17/scratchpad/sent_list.py > $R/sent_lists/.tmp 2>/dev/null && [ -s $R/sent_lists/.tmp ] && mv $R/sent_lists/.tmp $R/sent_lists/${HH}Z_end.tsv
    [ -f $R/pulled/build_camera_schedule_v1.yaml ] || ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; sha256sum camera_schedule.yaml; cat camera_schedule.yaml' </dev/null > $R/pulled/build_camera_schedule_v1.yaml 2>/dev/null
  fi
  sleep 15
done
at ${D}T$HH:16:00
HIL_RUN_DIR=$R hil/tools/hil_wake_report.sh SPOT-33507C ${D}T$HH:00 2>&1 | grep '^WAKE'
python3 hil/tools/hil_r2_score.py --console $R/console/wake_SPOT-33507C_${D}T${HH}00.txt --arm R --out-json $R/analysis/wake_${D}T${HH}.json 2>&1 | grep -vi deprecat | tail -1 | cut -c1-700
echo "cycle log: $ok good reads"; grep -nE "nrjxl|cjxl|\[B3A\]|\[JXL\]|exposure|^LOG|^[0-9a-f]{12}$|\[CFG\] config v2|\[KEY\]|\[HEAL\] sent|\[CMD\] (applied|ack)|Remote|<CF|halt" $R/pulled/cycle_${HH}Z.log 2>/dev/null | cut -c1-220 | head -25
