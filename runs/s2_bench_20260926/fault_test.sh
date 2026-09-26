#!/bin/bash
# S2 bench, bmcam003: "boot never bricks" on the real unit, on BENCH COPIES only.
#  f1 corrupt camera_config.yaml          -> v1 file migrated in memory (level v1_migrated)
#  f2 bad value on an ACTIVE key          -> same fallback
#  f3 both files garbage, no LKG          -> SAFE-MINIMAL: exit 0, nothing opened
#  f4 render dir unwritable               -> the v1 file as before S2
#  f5 f1 for real: a stills --transmit cycle over the BM bus on the fallback
B=/home/pi/s2bench; A=/home/pi/BM_Devel_Pi; cd $A
python3 $B/make_bench_dirs.py f1 f2 f3 f4 stills_f5
echo 'garbage: [' > $B/cfg_f1/camera_config.yaml
sed -i 's/^    fps: 10  #/    fps: 999  #/' $B/cfg_f2/camera_config.yaml; grep -n 'fps: 999' $B/cfg_f2/camera_config.yaml
echo 'garbage: [' > $B/cfg_f3/camera_config.yaml; echo 'garbage: [' > $B/cfg_f3/camera_schedule.yaml
echo 'garbage: [' > $B/cfg_stills_f5/camera_config.yaml
for f in f1 f2 f4; do
  echo "=== $f"
  if [ $f = f4 ]; then export BMCAM_RENDER_DIR=/proc/no_such_dir; else unset BMCAM_RENDER_DIR; fi
  python3 rc_progressive_jpeg.py --print-config --config-path $B/cfg_$f/camera_schedule.yaml > $B/fault_$f.txt 2>&1; echo "exit=$?"
  grep -E '^\[CFG\]|capture_mode|video_tx:' $B/fault_$f.txt | cut -c1-200
done
unset BMCAM_RENDER_DIR
echo "=== f3 (--transmit: must do nothing, open nothing)"
S=$(date +%s%N); python3 rc_progressive_jpeg.py --transmit --config-path $B/cfg_f3/camera_schedule.yaml > $B/fault_f3.txt 2>&1; echo "exit=$? $(( ($(date +%s%N)-S)/1000000 )) ms"
grep -E '^\[CFG\]|SAFE|\[CMD\]|BRIDGE|serial' $B/fault_f3.txt | cut -c1-200
echo "=== f5 real stills cycle on a corrupt v2 (fallback to v1 values)"
python3 -u $B/rssrun.py rc_progressive_jpeg.py --transmit --skip-time-window --config-path $B/cfg_stills_f5/camera_schedule.yaml > $B/fault_f5.txt 2>&1
grep -E '^\[CFG\]|\[BENCH\]|capture_mode|START|END|sent' $B/fault_f5.txt | head -12 | cut -c1-200
echo "=== DONE $(date -u +%FT%TZ)"
