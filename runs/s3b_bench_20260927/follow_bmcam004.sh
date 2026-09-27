#!/bin/bash
# Sprint26 S3b step 0 (copied from runs/s3a_bench_20260926, ref -> development 1636c8b, same tree as S3a 77d97ec): bmcam004 follows bmcam003 after the S3a gate passed (DESIGN §8.3
# "Hardware"): inside ONE hourly bus window (10 min, no Spotter change), after
# watcher_host.sh bmcam004 disarmed it and took backups:
#   deploy S3a -> commands.runtime: supervisor -> verify -> re-arm -> halt -> dark.
# Stops (unit left DISARMED, it halts at the :10 bus cut... so halt it) on any failure.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; H=bmcam004
echo "[follow] start $(date -u +%T)"
ssh -o BatchMode=yes pi@$H 'cd /home/pi/repos/bm_cam_legacy && timeout 90 git fetch -q origin development && bash tools/rc_field_update.sh --ref development --profile bmcam004/live_20260925 --leave-disarmed' < /dev/null > "$HERE/bmcam004_deploy_s3a.log" 2>&1
rc=$?
grep -E "PARITY|parity OK|SUMMARY|sha:|\[CFG\] config v2" "$HERE/bmcam004_deploy_s3a.log" | cut -c1-160
if [ $rc -ne 0 ] || ! grep -q "SUMMARY: PASS" "$HERE/bmcam004_deploy_s3a.log"; then
  echo "[follow] DEPLOY FAILED (rc=$rc): halting DISARMED on the old runtime"
  ssh -o BatchMode=yes pi@$H 'nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh > /dev/null 2>&1 < /dev/null &' < /dev/null
  exit 1
fi
ssh -o BatchMode=yes pi@$H 'set -e; A=/home/pi/BM_Devel_Pi; cd $A
python3 - <<EOF
p="camera_config.yaml"
s=open(p).read()
assert "\n  runtime:" not in s.split("\ncommands:",1)[1].split("\n\n",1)[0]
s=s.replace("\ncommands:\n","\ncommands:\n  runtime: \"supervisor\"  # s3abench 2026-09-26: S3a follows bmcam003 (S3b control)\n",1)
open(p,"w").write(s)
EOF
python3 -c "
import config_v2, rc_progressive_jpeg as rc
b = config_v2.load_for_boot(\"$A/camera_schedule.yaml\", \"$A/camera_config.yaml\", \"$A/camera_config.lkg.json\")
r = rc.resolve_runtime(None, b)
print(\"[follow] level\", b.level, \"runtime\", r, \"hash\", config_v2.config_hash(b.values))
assert b.level == \"v2\" and r[0] == \"supervisor\""
crontab /home/pi/s3abench/backup/crontab_ARMED.txt && echo "[follow] crontab: $(crontab -l | grep reboot)"
cat software_sha.txt
nohup sudo -n /bin/bash $A/tuned_halt.sh > /dev/null 2>&1 < /dev/null & echo "[follow] halt issued $(date -u +%T)"' < /dev/null 2>&1
sleep 25
ssh -o ConnectTimeout=4 -o BatchMode=yes pi@$H true < /dev/null 2>/dev/null && echo "[follow] WARNING $H still up" || echo "[follow] $H dark $(date -u +%T)"
