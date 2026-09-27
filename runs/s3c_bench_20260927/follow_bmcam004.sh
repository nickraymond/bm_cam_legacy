#!/bin/bash
# Sprint26 S3c step 0: bmcam004 follows S3b (DESIGN §8.3 "Hardware": the control moves to the
# previous stage at the start of the next). Inside ONE hourly bus window (10 min, no Spotter
# change), after watcher_s3b.sh bmcam004 disarmed it and backed up to /home/pi/s3bbench/backup:
#   deploy development (post-#80, S3b) -> verify v2 + supervisor + per_boot -> re-arm -> halt.
# commands.runtime: supervisor is already in bmcam004's camera_config.yaml (S3b step 0); the
# field update never edits it. On any failure the unit is halted DISARMED on its old runtime.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; H=bmcam004
echo "[follow] start $(date -u +%T)"
ssh -o BatchMode=yes pi@$H 'cd /home/pi/repos/bm_cam_legacy && timeout 90 git fetch -q origin development && bash tools/rc_field_update.sh --ref development --profile bmcam004/live_20260925 --leave-disarmed' < /dev/null > "$HERE/bmcam004_deploy_s3b.log" 2>&1
rc=$?
grep -E "PARITY|parity OK|SUMMARY|sha:|effective hash|\[CFG\] config v2" "$HERE/bmcam004_deploy_s3b.log" | cut -c1-160
if [ $rc -ne 0 ] || ! grep -q "SUMMARY: PASS" "$HERE/bmcam004_deploy_s3b.log"; then
  echo "[follow] DEPLOY FAILED (rc=$rc): halting DISARMED on the old runtime"
  ssh -o BatchMode=yes pi@$H 'nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh > /dev/null 2>&1 < /dev/null &' < /dev/null
  exit 1
fi
ssh -o BatchMode=yes pi@$H 'set -e; A=/home/pi/BM_Devel_Pi; cd $A
python3 -c "
import config_v2, rc_progressive_jpeg as rc
b = config_v2.load_for_boot(\"$A/camera_schedule.yaml\", \"$A/camera_config.yaml\", \"$A/camera_config.lkg.json\")
r = rc.resolve_runtime(None, b)
m = rc.resolve_run_mode(b, r[0])
print(\"[follow] level\", b.level, \"runtime\", r, \"run\", m, \"hash\", config_v2.config_hash(b.values))
assert b.level == \"v2\" and r[0] == \"supervisor\" and m[0] == \"per_boot\""
crontab /home/pi/s3bbench/backup/crontab_ARMED.txt && echo "[follow] crontab: $(crontab -l | grep reboot)"
cat software_sha.txt
nohup sudo -n /bin/bash $A/tuned_halt.sh > /dev/null 2>&1 < /dev/null & echo "[follow] halt issued $(date -u +%T)"' < /dev/null 2>&1
sleep 25
ssh -o ConnectTimeout=4 -o BatchMode=yes pi@$H true < /dev/null 2>/dev/null && echo "[follow] WARNING $H still up" || echo "[follow] $H dark $(date -u +%T)"
