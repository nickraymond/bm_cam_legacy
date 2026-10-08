#!/bin/bash
# REEF-RC POST hook (run by hil_deploy_window.sh after deploy + re-arm, cycle STOPPED, before the halt). Nick's OK 02:25Z.
# bmcam003: back up + clear the remote overlay (overlay {}, overlay_ids {}) in bm_command_state_v2.json, verify the
# effective config, and collect the BUILD_RECORD inputs (sha, sha256s, print-config, base YAML). Mac side.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/reef_rc_20261007; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
ssh "${O[@]}" pi@bmcam003 'bash -s' <<'REMOTE' > $R/pulled/post_05z.txt 2>&1
cd /home/pi/BM_Devel_Pi || exit 3
TS=$(date -u +%Y%m%dT%H%M%SZ); cp -p bm_command_state_v2.json /home/pi/hil_backup/bm_command_state_v2.json.reefrc_$TS && echo "BACKUP /home/pi/hil_backup/bm_command_state_v2.json.reefrc_$TS"
python3 - <<'PY'
import json, os
st = json.load(open("bm_command_state_v2.json"))
print("OVERLAY before:", st.get("overlay"), st.get("overlay_ids"))
st["overlay"], st["overlay_ids"] = {}, {}
open("bm_command_state_v2.json.tmp", "w").write(json.dumps(st)); os.replace("bm_command_state_v2.json.tmp", "bm_command_state_v2.json")
print("OVERLAY cleared")
PY
python3 -c "
import config_v2 as c, supervisor_config as S
b = c.load_config('camera_config.yaml', strict=True).base
e = S.resolve(b, c.read_state(S.state_path_for(b))); v = e.values
print('EFFECTIVE', {k: v.get(k) for k in ('mode.media','mode.run','uplink.msg_interval_s','uplink.chunk_chars','uplink.network_type','uplink.lane.enabled','still.message_cap','still.crop','still.output_width','camera.image_processing.enabled','camera.image_processing.contrast','schedule.window.enabled','schedule.window.start','schedule.window.end','power.halt.enabled','power.halt.dry_run')}, 'dropped', e.dropped, 'base_hash', c.config_hash(b))
"
echo "== sha"; head -c 40 software_sha.txt; echo
echo "== sha256"; sha256sum camera_config.yaml camera_schedule.yaml bm_command_state_v2.json
echo "== r2 file"; ls r2_start_delay_s 2>&1
echo "== crontab"; crontab -l
echo "== print-config"; python3 rc_progressive_jpeg.py --print-config 2>&1
REMOTE
ssh "${O[@]}" pi@bmcam003 'cat /home/pi/BM_Devel_Pi/camera_config.yaml' < /dev/null > $R/pulled/build_camera_config.yaml 2>&1
grep -E "^(BACKUP|OVERLAY|EFFECTIVE)" $R/pulled/post_05z.txt; echo "POST done"
