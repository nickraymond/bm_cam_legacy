#!/bin/bash
# READ-ONLY: bmcam004's current config vs the RC2 build record (bmcam003), for the RC2 move at 21Z. 16Z wake (9 AM PDT).
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/rc2_bmcam004_20261009/pulled; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,9,16,1,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 20); do ssh "${O[@]}" pi@bmcam004 true </dev/null 2>/dev/null && break; sleep 4; done
ssh "${O[@]}" pi@bmcam004 'cd /home/pi/BM_Devel_Pi; echo "== sha"; head -c 40 software_sha.txt; echo; echo "== cjxl"; cjxl --version 2>&1 | head -1; echo "== crontab"; crontab -l; echo "== sha256"; sha256sum camera_config.yaml camera_schedule.yaml bm_command_state_v2.json; python3 -c "
import config_v2 as c, supervisor_config as S
b = c.load_config(\"camera_config.yaml\", strict=True).base
st = c.read_state(S.state_path_for(b)); e = S.resolve(b, st); v = e.values
print(\"OVERLAY\", st.get(\"overlay\"), st.get(\"overlay_ids\"))
print(\"EFFECTIVE\", {k: v.get(k) for k in (\"mode.media\",\"mode.run\",\"still.format\",\"still.raw.layout\",\"camera.exposure.profile\",\"uplink.msg_interval_s\",\"uplink.chunk_chars\",\"uplink.network_type\",\"uplink.lane.enabled\",\"still.message_cap\",\"still.crop\",\"still.output_width\",\"camera.image_processing.enabled\",\"camera.image_processing.contrast\",\"schedule.window.enabled\",\"schedule.window.start\",\"schedule.window.end\",\"power.halt.enabled\",\"power.halt.dry_run\",\"media_key.enabled\")}, \"dropped\", e.dropped, \"base_hash\", c.config_hash(b))
"' </dev/null > $R/bmcam004_16z_state.txt 2>&1
ssh "${O[@]}" pi@bmcam004 'cat /home/pi/BM_Devel_Pi/camera_config.yaml' </dev/null > $R/bmcam004_16z_camera_config.yaml 2>&1
cat $R/bmcam004_16z_state.txt | cut -c1-900
diff <(grep -vE "^\s*#|^\s*$" runs/jxl_rc_20261008/pulled/build_camera_config.yaml | sed 's/  #.*//') <(grep -vE "^\s*#|^\s*$" $R/bmcam004_16z_camera_config.yaml | sed 's/  #.*//') > $R/base_diff_bmcam003RC2_vs_bmcam004.txt; echo "== base YAML diff lines: $(grep -c '^[<>]' $R/base_diff_bmcam003RC2_vs_bmcam004.txt)"; head -60 $R/base_diff_bmcam003RC2_vs_bmcam004.txt
