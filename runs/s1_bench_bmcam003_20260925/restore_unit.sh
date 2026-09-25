#!/bin/bash
# Restore bmcam003 to its pre-bench state (everything except cron, which is re-armed
# separately at the right moment of the Spotter restore). Prints a verification table.
#   code + software_sha.txt : from backup/BM_Devel_Pi_code_before_s1bench.tgz (a71b6c7)
#   files the bench added   : removed (bm_codec/bm_port/rc_capture/rc_telemetry.py)
#   camera_schedule.yaml    : from backup
#   bm_command_state.json   : verified unchanged (the bench used copies)
#   bm_media_key_last.txt   : NOT restored — bench sends advanced it legitimately;
#                             rolling it back could reuse a key that real media now owns
#   repo checkout           : back to feature/s5-rsd-heal (a71b6c7)
set -u
B=/home/pi/s1bench; A=/home/pi/BM_Devel_Pi; R=/home/pi/repos/bm_cam_legacy
T=$(mktemp -d); tar xzf $B/backup/BM_Devel_Pi_code_before_s1bench.tgz -C $T
echo "[restore] extracting code files from the backup"
( cd $T/BM_Devel_Pi && find . -maxdepth 1 -type f \( -name '*.py' -o -name '*.sh' -o -name 'software_sha.txt' \) -print0 | xargs -0 -I{} cp -p {} $A/{} )
for f in bm_codec.py bm_port.py rc_capture.py rc_telemetry.py; do
  if [ ! -e "$T/BM_Devel_Pi/$f" ] && [ -e "$A/$f" ]; then rm -f "$A/$f"; echo "[restore] removed bench-only $f"; fi
done
cp -p $B/backup/camera_schedule.yaml $A/camera_schedule.yaml
rm -rf $A/__pycache__
git -C $R checkout -q "$(cat $B/backup/repo_branch.txt)"
echo "[restore] verification"
bad=0
( cd $T/BM_Devel_Pi && find . -maxdepth 1 -type f \( -name '*.py' -o -name '*.sh' -o -name 'software_sha.txt' \) ) | while read f; do
  cmp -s "$T/BM_Devel_Pi/$f" "$A/$f" || echo "  MISMATCH $f"
done
extra=$(cd $A && ls *.py | while read f; do [ -e "$T/BM_Devel_Pi/$f" ] || echo $f; done)
echo "  extra .py not in backup: ${extra:-none}"
cmp -s $B/backup/camera_schedule.yaml $A/camera_schedule.yaml && echo "  camera_schedule.yaml: restored (identical)" || echo "  camera_schedule.yaml: MISMATCH"
cmp -s $B/backup/bm_command_state.json $A/bm_command_state.json && echo "  bm_command_state.json: unchanged since backup" || echo "  bm_command_state.json: CHANGED (inspect)"
echo "  software_sha.txt: $(cat $A/software_sha.txt) (backup: $(cat $B/backup/software_sha.txt))"
echo "  repo: $(git -C $R branch --show-current) $(git -C $R rev-parse --short HEAD) (backup: $(cat $B/backup/repo_branch.txt) $(cut -c1-7 $B/backup/repo_head.txt))"
echo "  media key last: $(cat $A/bm_media_key_last.txt) (pre-bench $(cat $B/backup/bm_media_key_last.txt); kept advanced on purpose)"
echo "  crontab now: $(crontab -l | grep reboot)"
cd $A && python3 rc_progressive_jpeg.py --print-config --config-path $A/camera_schedule.yaml > /dev/null 2>&1 && echo "  print-config: OK" || echo "  print-config: FAILED"
rm -rf $T
