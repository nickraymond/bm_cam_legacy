#!/bin/bash
# S2 bench, bmcam003 (config v2): v8 commands over the real BM bus land in the v2 state
# file of a BENCH COPY (cfg_cmd), are journaled, dedupe by id, and change the config hash.
# Side A (this script, on the Pi): build cfg_cmd, freeze its render, start the #76 bench
# listener (production CommandDaemon) on it for 240 s, no halt. Side B (the Mac) sends the
# console commands meanwhile. Afterwards: `cmd_test.sh report`.
B=/home/pi/s2bench; A=/home/pi/BM_Devel_Pi; D=$B/cfg_cmd
if [ "${1:-}" = report ]; then
  cd $A
  echo "--- v8 section"; python3 -c "import json;d=json.load(open('$D/bm_command_state_v2.json'));v=d['v8'];print('settings',v['settings']);print('touched',v['touched']);print('applied_ids',v['applied_ids'][-8:]);print('v2 fields kept:',{k:d[k] for k in ('boot_counter','high_water','result_cache','migrated_from')})"
  echo "--- journal"; cat $D/config_journal.jsonl
  echo "--- hash after"; python3 rc_progressive_jpeg.py --print-config --config-path $D/camera_schedule.yaml 2>&1 | grep -E '^\[CFG\] config v2|power_halt|crop|schedule'
  echo "--- listener"; grep -E '\[BENCH\]|\[CMD\]' $B/cmd_listener.log | tail -20
  exit 0
fi
python3 $B/make_bench_dirs.py cmd
cd $A
python3 rc_progressive_jpeg.py --print-config --config-path $D/camera_schedule.yaml > $B/cmd_before.txt 2>&1
grep '^\[CFG\] config v2' $B/cmd_before.txt
cp /dev/shm/bmcam/camera_schedule.yaml $D/render_v1.yaml
UP=$(cut -d. -f1 /proc/uptime)
nohup python3 -u /home/pi/repos/bm_cam_legacy/tools/bm_cmd_bench_listener.py --config-path $D/render_v1.yaml \
  --subscribe-at 0 --halt-at $((UP + 240)) --no-halt > $B/cmd_listener.log 2>&1 < /dev/null &
sleep 8; grep -E 'subscribed|state=' $B/cmd_listener.log | head -3
