#!/bin/bash
# One side of the live bench: import timing, (dev only) old boot py_compile cost,
# then a real video cycle and a real stills cycle over the BM bus. Halt is dry-run
# in the bench configs; the live YAML and command state are not touched.
SIDE=$1
B=/home/pi/s1bench; A=/home/pi/BM_Devel_Pi; L=$B/live_$SIDE; mkdir -p $L
cd $A
echo "[side] $SIDE sha=$(cat software_sha.txt) start=$(date -u +%FT%TZ)"
python3 $B/make_bench_cfgs.py $SIDE
python3 $B/import_time.py >/dev/null 2>&1   # warm pyc
for i in 1 2 3 4 5; do python3 $B/import_time.py; done
if [ "$SIDE" = dev ]; then
  LIST=$(sed -n '/py_compile \\/,/^if /p' rc_run_capture_cycle.sh | grep -v "py_compile\|^if " | tr -d '\\')
  S=$(date +%s%N); /usr/bin/python3 -m py_compile $LIST; E=$(date +%s%N)
  echo "[old-boot-py_compile] $(( (E-S)/1000000 )) ms for $(echo $LIST | wc -w) files"
fi
for MODE in video stills; do
  echo "[cycle] $MODE start=$(date -u +%FT%TZ)"
  python3 -u $B/rssrun.py rc_progressive_jpeg.py --config-path $B/cfg_${SIDE}_${MODE}.yaml --transmit --skip-time-window > $L/cycle_$MODE.log 2>&1
  echo "[cycle] $MODE end=$(date -u +%FT%TZ) $(grep '\[BENCH\]' $L/cycle_$MODE.log)"
  grep -E '^\[BOOT\]' $L/cycle_$MODE.log | sed "s/^/[cycle-$MODE] /"
done
echo "[side] $SIDE DONE $(date -u +%FT%TZ)"
