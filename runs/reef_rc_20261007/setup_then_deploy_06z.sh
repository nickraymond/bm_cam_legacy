#!/bin/bash
# REEF-RC retry on bmcam003 in the 06Z window (11 PM PDT). Nick's OK (TE2 chat 02:25Z). The 05Z setup edits are in place
# (v1 pacing 1.0 + capture_mode progressive_jpeg, v2 mode.media still, r2 file removed); the 05Z deploy failed SAFE on
# the v1-vs-v2 parity: video_tx.enabled true (v1) vs false (v2, media still). This run: backup -> v1 video_tx.enabled
# true -> false -> approximate parity dry run (current runtime; config_path ignored) -> deploy #143 head c8bea4d with the
# POST hook (overlay clear + verify + BUILD_RECORD inputs), only if the edit verified and it is before :03.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/reef_rc_20261007; G=$R/gate.log; P=$R/pulled/setup_06z.txt
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,6,0,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 30); do ssh "${O[@]}" pi@bmcam003 true </dev/null 2>/dev/null && break; sleep 4; done
ssh "${O[@]}" pi@bmcam003 'bash -s' <<'REMOTE' > $P 2>&1
cd /home/pi/BM_Devel_Pi || exit 3
TS=$(date -u +%Y%m%dT%H%M%SZ); BK=/home/pi/hil_backup/reefrc_$TS; mkdir -p $BK
cp -p camera_schedule.yaml camera_config.yaml $BK/ && echo "BACKUP $BK" || { echo "BACKUP FAILED"; exit 4; }
python3 - <<'PY' || { cp -p $BK/camera_schedule.yaml .; echo "REVERTED v1 from $BK"; exit 5; }
import os, re
p = "camera_schedule.yaml"; s = open(p).read(); pat = r'^(video_tx:\s*\n\s+enabled:\s*)true'
n = len(re.findall(pat, s, flags=re.M))
if n != 1: raise SystemExit(f"{pat!r} matched {n} times (want 1)")
open(p + ".tmp", "w").write(re.sub(pat, r'\g<1>false', s, flags=re.M)); os.replace(p + ".tmp", p)
print("EDIT camera_schedule.yaml: video_tx.enabled true -> false")
PY
python3 rc_progressive_jpeg.py --print-config --json --config-path camera_schedule.yaml --config-format v1 > /tmp/v1.json 2>&1
python3 rc_progressive_jpeg.py --print-config --json --config-format v2 > /tmp/v2.json 2>&1
python3 - <<'PY'
import json
a, b = (json.loads(open(f).read()[open(f).read().index("{"):]) for f in ("/tmp/v1.json", "/tmp/v2.json"))
def norm(x):
    if isinstance(x, dict): return {k: norm(v) for k, v in x.items() if k != "config_path"}
    return x
d = sorted(k for k in set(a) | set(b) if norm(a.get(k)) != norm(b.get(k)))
print("PARITY DRY RUN (approx, config_path ignored):", "OK" if not d else f"DIFF {d}")
for k in d: print("  ", k, "v1:", json.dumps(norm(a.get(k)))[:300], "| v2:", json.dumps(norm(b.get(k)))[:300])
PY
REMOTE
rc=$?
cat $P
echo "$(date -u +%FT%TZ) [TE2] REEF-RC setup retry on bmcam003 rc=$rc: $(grep -E '^(BACKUP|EDIT|REVERTED|PARITY)' $P | tr '\n' ' ')" | tee -a $G
if [ $rc = 0 ] && grep -q "^EDIT camera_schedule.yaml: video_tx.enabled" $P && [ "$(date -u +%M)" -lt 3 ]; then
  HIL_RUN_DIR=$R ACCEPT_DIFF=1 POST="bash runs/reef_rc_20261007/post_05z.sh" hil/tools/hil_deploy_window.sh bmcam003 fix/sent-prune-spotter-time c8bea4d bmcam003/live_20260925 2026-10-08T06:00:00Z reefrc2 2>&1 | tail -30
else
  echo "$(date -u +%FT%TZ) [TE2] REEF-RC deploy NOT started in 06Z (edit not verified or too late)" | tee -a $G
fi
