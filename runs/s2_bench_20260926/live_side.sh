#!/bin/bash
# One side of the live A/B (S2 bench): config-step timing, import timing, then a real
# video cycle and a real stills cycle over the BM bus from bench copies (halt dry-run).
B=/home/pi/s2bench; A=/home/pi/BM_Devel_Pi; L=$B/live; mkdir -p $L
cd $A
echo "[side] $(hostname) sha=$(cat software_sha.txt) v2=$([ -f camera_config.yaml ] && echo yes || echo no) start=$(date -u +%FT%TZ)"
python3 $B/make_bench_dirs.py
python3 $B/import_time.py >/dev/null 2>&1
for i in 1 2 3 4 5; do python3 $B/import_time.py; done
for i in 1 2 3; do   # whole config step incl. the v2 load/render/resolve check (print-config)
  S=$(date +%s%N); python3 rc_progressive_jpeg.py --print-config --config-path $B/cfg_video/camera_schedule.yaml > /dev/null 2>&1; E=$(date +%s%N)
  echo "[print-config] $(( (E-S)/1000000 )) ms"
done
for MODE in video stills; do
  echo "[cycle] $MODE start=$(date -u +%FT%TZ)"
  python3 -u $B/rssrun.py rc_progressive_jpeg.py --config-path $B/cfg_$MODE/camera_schedule.yaml --transmit --skip-time-window > $L/cycle_$MODE.log 2>&1
  echo "[cycle] $MODE end=$(date -u +%FT%TZ) $(grep '\[BENCH\]' $L/cycle_$MODE.log)"
  grep -E '^\[BOOT\]|^\[CFG\]' $L/cycle_$MODE.log | sed "s/^/[cycle-$MODE] /"
done
echo "[side] DONE $(date -u +%FT%TZ)"
