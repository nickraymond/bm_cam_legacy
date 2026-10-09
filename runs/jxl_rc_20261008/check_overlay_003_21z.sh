#!/bin/bash
# READ-ONLY: bmcam003 overlay + exposure sidecar in the 21Z wake (EM ask: confirm the low-gain overlay 9c980489 holds for the weekend).
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,9,21,3,0,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 20); do ssh "${O[@]}" pi@bmcam003 true </dev/null 2>/dev/null && break; sleep 4; done
ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; f=$(ls -t cron_logs/rc_cycle_*.log | head -1); grep -E "\[CFG\] config v2|\[EXP\]" "$f" | head -3; python3 -c "
import json; st=json.load(open(\"bm_command_state_v2.json\")); print(\"OVERLAY\", st.get(\"overlay\"), st.get(\"overlay_ids\"))"; m=$(ls -t images/*capture_metadata.json | head -1); echo "SIDECAR $m"; grep -oE "\"(exposure_profile|exposure_profile_applied|exposure_max_gain|exposure_max_shutter_us|AnalogueGain|ExposureTime|Lux)\": *[^,}]*" "$m"' </dev/null 2>&1 | tee runs/jxl_rc_20261008/pulled/overlay_check_21z.txt
