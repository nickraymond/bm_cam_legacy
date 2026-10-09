#!/bin/bash
# bmcam004 -> RC2 in the 21Z window (2 PM PDT). Nick's OK 16:03Z ("OK for the bmcam004 RC2 edits in EDIT_LIST.md").
# 21:00:30Z: backup -> v2 media still + still.format nrjxl + msg_interval_s 1.0; v1 delay 1.0 + capture_mode
# progressive_jpeg + video_tx.enabled false -> strict load (revert all on failure) -> (tip still 9ec4cb7?) deploy development
# 9ec4cb7 ACCEPT_DIFF=1 with POST post_21z.sh (overlay clear + verify + BUILD_RECORD inputs). Detached (nohup): log rc2_004.log.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/rc2_bmcam004_20261009; G=$R/gate.log; P=$R/pulled/setup_21z.txt
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,9,21,0,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
git fetch -q origin development; TIP=$(git rev-parse --short=7 origin/development)
[ "$TIP" = 9ec4cb7 ] || { echo "$(date -u +%FT%TZ) [TE2] ABORT: development tip $TIP != 9ec4cb7 -> nothing done on bmcam004" | tee -a $G; exit 9; }
for i in $(seq 1 30); do ssh "${O[@]}" pi@bmcam004 true </dev/null 2>/dev/null && break; sleep 4; done
ssh "${O[@]}" pi@bmcam004 'bash -s' <<'REMOTE' > $P 2>&1
cd /home/pi/BM_Devel_Pi || exit 3
TS=$(date -u +%Y%m%dT%H%M%SZ); BK=/home/pi/hil_backup/rc2_004_$TS; mkdir -p $BK
cp -p camera_schedule.yaml camera_config.yaml $BK/ && echo "BACKUP $BK" || { echo "BACKUP FAILED"; exit 4; }
revert() { cp -p $BK/camera_schedule.yaml $BK/camera_config.yaml . ; echo "REVERTED ALL from $BK: $1"; exit 5; }
python3 - <<'PY' || revert "edit failed"
import os, re
def sub1(path, pat, repl):
    s = open(path).read(); n = len(re.findall(pat, s, flags=re.M))
    if n != 1: raise SystemExit(f"{path}: {pat!r} matched {n} times (want 1)")
    open(path + ".tmp", "w").write(re.sub(pat, repl, s, flags=re.M)); os.replace(path + ".tmp", path); print(f"EDIT {path}: {pat!r} -> {repl!r}")
sub1("camera_schedule.yaml", r'^(\s*image_transmit_delay_seconds:\s*)[0-9.]+', r'\g<1>1.0')
sub1("camera_schedule.yaml", r'^capture_mode:\s*"?[a-z_]+"?', 'capture_mode: "progressive_jpeg"')
sub1("camera_schedule.yaml", r'^(video_tx:\s*\n\s+enabled:\s*)(true|false)', r'\g<1>false')
sub1("camera_config.yaml", r'^(  media:\s*)"video"', r'\g<1>"still"')
sub1("camera_config.yaml", r'^(  msg_interval_s:\s*)1\.3\b', r'\g<1>1.0')
L = open("camera_config.yaml").read().split("\n")
st = [i for i, l in enumerate(L) if l.split("#", 1)[0].rstrip() == "still:"]
if len(st) != 1: raise SystemExit("still: not unique")
s = st[0]; e = next((i for i in range(s + 1, len(L)) if L[i][:1].strip() and not L[i].startswith("#")), len(L))
if any(re.match(r'^  format:', L[i]) for i in range(s + 1, e)): raise SystemExit("still.format already present")
L.insert(s + 1, '  format: "nrjxl"  # RC2 (Nick OK 2026-10-09): JPEG XL stills')
open("camera_config.yaml.tmp", "w").write("\n".join(L)); os.replace("camera_config.yaml.tmp", "camera_config.yaml"); print("EDIT camera_config.yaml: inserted still.format nrjxl")
PY
python3 -c "
import config_v2 as c
b = c.load_config('camera_config.yaml', strict=True).base
print('STRICT LOAD OK base_hash', c.config_hash(b))
" || revert "strict load failed"
REMOTE
rc=$?; cat $P
echo "$(date -u +%FT%TZ) [TE2] bmcam004 RC2 setup rc=$rc: $(grep -E '^(BACKUP|EDIT|STRICT|REVERTED)' $P | tr '\n' ' ')" | tee -a $G
if [ $rc = 0 ] && grep -q "^STRICT LOAD OK" $P && [ "$(grep -c '^EDIT' $P)" = 6 ] && [ "$(date -u +%M)" -lt 3 ]; then
  HIL_RUN_DIR=$R ACCEPT_DIFF=1 POST="bash $R/post_21z.sh" hil/tools/hil_deploy_window.sh bmcam004 development 9ec4cb7 bmcam004/live_20260925 2026-10-09T21:00:00Z rc2 2>&1 | tail -30
else
  echo "$(date -u +%FT%TZ) [TE2] bmcam004 RC2 deploy NOT started (setup not verified or too late)" | tee -a $G
fi
echo "CHAIN DONE $(date -u +%FT%TZ)"
