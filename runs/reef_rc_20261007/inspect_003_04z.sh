#!/bin/bash
# READ-ONLY inspection of bmcam003 in the 04Z wake, for the REEF-RC write list. Prints to stdout only.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
R=runs/reef_rc_20261007/pulled
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,4,1,0,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 20); do ssh "${O[@]}" pi@bmcam003 true </dev/null 2>/dev/null && break; sleep 4; done
ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; echo "== sha"; head -c 60 software_sha.txt; echo; echo "== r2 file"; ls -la r2_start_delay_s 2>&1; cat r2_start_delay_s 2>/dev/null; echo "== crontab"; crontab -l; echo "== camera_config.yaml"; cat camera_config.yaml; echo "== state files"; ls -la *.json 2>/dev/null | head; ls -la ~/.config/bmcam* 2>/dev/null' </dev/null > $R/bmcam003_04z_inspect.txt 2>&1
ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; python3 rc_progressive_jpeg.py --print-config 2>&1' </dev/null > $R/bmcam003_04z_print_config.txt 2>&1
ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; python3 rc_progressive_jpeg.py --print-config --json --config-path camera_schedule.yaml --config-format v1 2>&1' </dev/null > $R/bmcam003_04z_v1.json 2>&1
ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; python3 rc_progressive_jpeg.py --print-config --json --config-format v2 2>&1' </dev/null > $R/bmcam003_04z_v2.json 2>&1
ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; python3 -c "import config_v2 as c, supervisor_config as S; b=c.load_config(\"camera_config.yaml\").base; st=c.read_state(S.state_path_for(b)); print(\"state_path\", S.state_path_for(b)); print(\"overlay\", st)"' </dev/null > $R/bmcam003_04z_overlay.txt 2>&1
wc -c $R/bmcam003_04z_*; python3 tools/config_parity.py json $R/bmcam003_04z_v1.json $R/bmcam003_04z_v2.json 2>&1 | head -30
