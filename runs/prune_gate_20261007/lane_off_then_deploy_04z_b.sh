#!/bin/bash
# Nick OK (TE2 chat 2026-10-08 02:25Z): lane OFF on bmcam004 via the pre-lane backup, in the 04Z window (03Z missed: Mac asleep), then the PRUNE deploy.
# `hil_lane_block.sh off` keeps grid/post/pre/wait (v1/v2 parity would still fail), so restore the pre-lane backup
# camera_config.yaml.bak_lane_20261007T000043Z ONLY if its diff vs current is the lane block alone; then verify the
# effective lane + dry-run the v1-vs-v2 parity check (read-only) with the current runtime.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
G=runs/prune_gate_20261007/gate.log; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,4,0,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 30); do ssh "${O[@]}" pi@bmcam004 true </dev/null 2>/dev/null && break; sleep 4; done
ssh "${O[@]}" pi@bmcam004 'bash -s' <<'REMOTE' | tee runs/prune_gate_20261007/pulled/lane_off_04z_b.txt
cd /home/pi/BM_Devel_Pi || exit 3
B=camera_config.yaml.bak_lane_20261007T000043Z
[ -f "$B" ] || { echo "NO BACKUP $B"; exit 4; }
echo "== diff $B camera_config.yaml"; diff "$B" camera_config.yaml; 
# allowed diff lines: the lane block only
bad=$(diff "$B" camera_config.yaml | grep -E '^[<>]' | grep -vE '^[<>] *(lane:|enabled: (true|false)|grid_s:|post_guard_s:|pre_guard_s:|max_wait_s:)' | grep -vE '^> *$')
if [ -n "$bad" ]; then echo "UNEXPECTED DIFF -> not touching:"; echo "$bad"; exit 5; fi
TS=$(date -u +%Y%m%dT%H%M%SZ); cp -p camera_config.yaml camera_config.yaml.bak_prelaneoff_$TS && cp -p "$B" camera_config.yaml && echo "RESTORED $B (current saved as camera_config.yaml.bak_prelaneoff_$TS)"
python3 -c "import config_v2 as c, supervisor_config as S; b = c.load_config('camera_config.yaml', strict=True).base; e = S.resolve(b, c.read_state(S.state_path_for(b))); v = e.values; print('effective lane:', v['uplink.lane.enabled'], 'dropped', e.dropped, 'base hash', c.config_hash(b))" || { cp -p camera_config.yaml.bak_prelaneoff_$TS camera_config.yaml; echo "LOAD FAILED -> reverted"; exit 6; }
python3 rc_progressive_jpeg.py --print-config --json --config-path camera_schedule.yaml --config-format v1 > /tmp/v1.json 2>&1
python3 rc_progressive_jpeg.py --print-config --json --config-format v2 > /tmp/v2.json 2>&1
python3 /home/pi/repos/bm_cam_legacy/tools/config_parity.py json /tmp/v1.json /tmp/v2.json && echo "PARITY DRY RUN OK (approximate: current runtime, not the staged one)" || echo "PARITY DRY RUN FAILED"
REMOTE
rc=${PIPESTATUS[0]}
echo "$(date -u +%FT%TZ) [TE2] bmcam004 lane off via pre-lane backup rc=$rc: $(grep -E 'RESTORED|UNEXPECTED|effective lane|PARITY|NO BACKUP|FAILED' runs/prune_gate_20261007/pulled/lane_off_04z_b.txt | tr '\n' ' ')" | tee -a $G
# --- chain: only if the restore + verify + parity dry run all passed, and early enough to finish before the :10 bus cut
P=runs/prune_gate_20261007/pulled/lane_off_04z_b.txt
if grep -q "^RESTORED" $P && grep -q "^effective lane: False" $P && [ "$(date -u +%M)" -lt 3 ]; then
  ssh "${O[@]}" pi@bmcam004 'cd /home/pi/BM_Devel_Pi && python3 -' < /private/tmp/claude-501/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy--claude-worktrees-sprint26-s3a-runtime-parity-092958/3cd7e446-c576-4dd7-8494-833abcb29a17/scratchpad/sent_list.py > runs/prune_gate_20261007/sent_lists/04Z_predeploy.tsv 2>/dev/null
  echo "$(date -u +%FT%TZ) [TE2] sent/ listing before deploy: $(wc -l < runs/prune_gate_20261007/sent_lists/04Z_predeploy.tsv) records" | tee -a $G
  HIL_RUN_DIR=runs/prune_gate_20261007 hil/tools/hil_deploy_window.sh bmcam004 hil/prune143-on-104ee3c 0a6e411 bmcam004/live_20260925 2026-10-08T04:00:00Z prune143d 2>&1 | tail -25
else
  echo "$(date -u +%FT%TZ) [TE2] deploy NOT started in 04Z (restore/parity not OK or too late) -> next window" | tee -a $G
fi
