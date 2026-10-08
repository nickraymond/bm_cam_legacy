#!/bin/bash
# REEF-RC setup + deploy on bmcam003 in the 05Z window (10 PM PDT). Nick's OK (TE2 chat 2026-10-08 02:25Z): bench config
# edits on bmcam003 for REEF-RC: reef values in the base YAML, clear the remote overlay, remove r2_start_delay_s; back up
# first, verify after, log every change in gate.log.
# Steps (one ssh session, ~10 s): backup -> v1 camera_schedule.yaml: bm_serial.image_transmit_delay_seconds 1.3 -> 1.0,
#   capture_mode -> progressive_jpeg (= v2 mode.media still; config_v1_reader.py:293) -> v2 camera_config.yaml:
#   mode.media "video" -> "still" -> rm r2_start_delay_s -> strict-load verify (revert ALL on failure). Then, only if the setup verified and it is before :03:
#   hil_deploy_window.sh bmcam003 fix/sent-prune-spotter-time c8bea4d (#143 head); its v1-vs-v2 parity check validates the
#   two config edits. POST hook post_05z.sh (cycle stopped): overlay clear + effective verify + BUILD_RECORD inputs.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/reef_rc_20261007; G=$R/gate.log; P=$R/pulled/setup_05z.txt
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,5,0,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 30); do ssh "${O[@]}" pi@bmcam003 true </dev/null 2>/dev/null && break; sleep 4; done
ssh "${O[@]}" pi@bmcam003 'bash -s' <<'REMOTE' > $P 2>&1
cd /home/pi/BM_Devel_Pi || exit 3
TS=$(date -u +%Y%m%dT%H%M%SZ); BK=/home/pi/hil_backup/reefrc_$TS; mkdir -p $BK
cp -p camera_schedule.yaml camera_config.yaml $BK/ && { [ -f r2_start_delay_s ] && cp -p r2_start_delay_s $BK/ || true; } && echo "BACKUP $BK" || { echo "BACKUP FAILED"; exit 4; }
revert() { cp -p $BK/camera_schedule.yaml $BK/camera_config.yaml . ; [ -f $BK/r2_start_delay_s ] && cp -p $BK/r2_start_delay_s .; echo "REVERTED ALL from $BK: $1"; exit 5; }
python3 - <<'PY' || revert "edit failed"
import os, re
def sub1(path, pat, repl):
    s = open(path).read(); n = len(re.findall(pat, s, flags=re.M))
    if n != 1: raise SystemExit(f"{path}: {pat!r} matched {n} times (want 1)")
    open(path + ".tmp", "w").write(re.sub(pat, repl, s, flags=re.M)); os.replace(path + ".tmp", path)
    print(f"EDIT {path}: {pat!r} -> {repl!r}")
sub1("camera_schedule.yaml", r'^(\s*image_transmit_delay_seconds:\s*)[0-9.]+', r'\g<1>1.0')
sub1("camera_schedule.yaml", r'^capture_mode:\s*"?[a-z_]+"?', 'capture_mode: "progressive_jpeg"')
sub1("camera_config.yaml", r'^(  media:\s*)"video"', r'\g<1>"still"')
PY
rm -f r2_start_delay_s && echo "REMOVED r2_start_delay_s"
python3 -c "
import config_v2 as c
b = c.load_config('camera_config.yaml', strict=True).base
print('STRICT LOAD OK base_hash', c.config_hash(b))
" || revert "strict load failed"
python3 rc_progressive_jpeg.py --print-config 2>&1 | grep -E "^\[CFG\] config v2|capture_mode=|pacing|message cap|transmit_phase|schedule:|geometry"
[ -f r2_start_delay_s ] && echo "r2 file STILL PRESENT" || echo "r2 file gone"
REMOTE
rc=$?
cat $P
echo "$(date -u +%FT%TZ) [TE2] REEF-RC setup on bmcam003 rc=$rc: $(grep -E '^(BACKUP|EDIT|REMOVED|STRICT|REVERTED)' $P | tr '\n' ' ')" | tee -a $G
if [ $rc = 0 ] && grep -q "^STRICT LOAD OK" $P && [ "$(grep -c '^EDIT' $P)" = 3 ] && [ "$(date -u +%M)" -lt 3 ]; then
  POST="bash runs/reef_rc_20261007/post_05z.sh"
  HIL_RUN_DIR=$R ACCEPT_DIFF=1 POST="$POST" hil/tools/hil_deploy_window.sh bmcam003 fix/sent-prune-spotter-time c8bea4d bmcam003/live_20260925 2026-10-08T05:00:00Z reefrc 2>&1 | tail -30
else
  echo "$(date -u +%FT%TZ) [TE2] REEF-RC deploy NOT started in 05Z (setup not verified or too late)" | tee -a $G
fi
