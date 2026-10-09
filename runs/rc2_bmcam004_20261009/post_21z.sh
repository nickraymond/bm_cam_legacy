#!/bin/bash
# bmcam004 RC2 POST (after deploy + re-arm, cycle stopped, before the halt). Nick's OK 16:03Z: clear the 3-key overlay,
# verify the effective config, collect BUILD_RECORD inputs.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/rc2_bmcam004_20261009; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
ssh "${O[@]}" pi@bmcam004 'bash -s' <<'REMOTE' > $R/pulled/post_21z.txt 2>&1
cd /home/pi/BM_Devel_Pi || exit 3
TS=$(date -u +%Y%m%dT%H%M%SZ); cp -p bm_command_state_v2.json /home/pi/hil_backup/bm_command_state_v2.json.rc2_004_$TS && echo "BACKUP /home/pi/hil_backup/bm_command_state_v2.json.rc2_004_$TS"
python3 - <<'PY'
import json, os
st = json.load(open("bm_command_state_v2.json")); print("OVERLAY before:", st.get("overlay"), st.get("overlay_ids"))
st["overlay"], st["overlay_ids"] = {}, {}
open("bm_command_state_v2.json.tmp", "w").write(json.dumps(st)); os.replace("bm_command_state_v2.json.tmp", "bm_command_state_v2.json"); print("OVERLAY cleared")
PY
python3 -c "
import config_v2 as c, supervisor_config as S
b = c.load_config('camera_config.yaml', strict=True).base
st = c.read_state(S.state_path_for(b)); e = S.resolve(b, st); v = e.values
print('EFFECTIVE', {k: v.get(k) for k in ('mode.media','mode.run','still.format','still.raw.layout','camera.exposure.profile','uplink.msg_interval_s','uplink.chunk_chars','uplink.network_type','uplink.lane.enabled','still.message_cap','still.crop','still.output_width','camera.image_processing.contrast','schedule.window.start','schedule.window.end','power.halt.enabled','power.halt.dry_run')}, 'overlay', st.get('overlay'), 'dropped', e.dropped, 'base_hash', c.config_hash(b))
"
echo "== sha"; head -c 40 software_sha.txt; echo
echo "== sha256"; sha256sum camera_config.yaml camera_schedule.yaml bm_command_state_v2.json
echo "== cjxl"; cjxl --version 2>&1 | head -1
echo "== crontab"; crontab -l
REMOTE
ssh "${O[@]}" pi@bmcam004 'cat /home/pi/BM_Devel_Pi/camera_config.yaml' </dev/null > $R/pulled/build_camera_config.yaml 2>&1
grep -E "^(BACKUP|OVERLAY|EFFECTIVE)" $R/pulled/post_21z.txt | cut -c1-700; echo "POST done"
