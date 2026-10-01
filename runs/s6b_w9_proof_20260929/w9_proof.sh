#!/bin/bash
# w9_proof.sh HOST SPOT BRIDGE DEVICE PROFILE — S6b step 2 (W9) bench proof on one unit, the
# bmcam003 recipe (RESULTS.md) as one script. The unit must be running stay_on on a HELD bus
# with cron armed; nereus000 holds the admin token. Steps: disarm + back up -> SIGTERM ->
# deploy development -> reset mode.run (console, exit 72) -> proof clip (python directly,
# --bench-drop-chunks start,5,17; real halt) -> wait for the backend missing set -> heal
# passes (backend-allocated rsd at the subscribe of a manual wrapper run) until complete ->
# sha256 of the stored clip vs the unit's sent record. The unit is left DISARMED + halted.
set -u
H=$1; SPOT=$2; BR=$3; DEV=$4; PROF=$5
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE"
W="$(cd ../.. && pwd)"
N=pi@192.168.1.45
log() { echo "$(date -u +%FT%TZ) [$H] $*" | tee -a gate.log; }
api() { ssh -o BatchMode=yes $N "set -a; . ~/.config/nereus/heal_driver.env; set +a; curl -s -m 60 -H \"Authorization: Bearer \$ADMIN_TOKEN\" $*" </dev/null; }
up() { ssh -o BatchMode=yes -o ConnectTimeout=3 pi@$H true </dev/null 2>/dev/null; }
newest() { ssh -o BatchMode=yes pi@$H 'ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1' </dev/null; }

log "start: disarm, back up, stop stay_on"
ssh -o BatchMode=yes pi@$H 'set -e; B=/home/pi/w9proof/backup; A=/home/pi/BM_Devel_Pi; mkdir -p $B
[ -f $B/crontab_ARMED.txt ] || crontab -l > $B/crontab_ARMED.txt   # never overwrite the ARMED backup on a re-run
crontab -l | sed "s|^@reboot \(.*rc_run_capture_cycle\.sh\)\$|# DISABLED w9proof: @reboot \1|" | crontab -
pkill -TERM -f "[r]c_run_capture_cycle.sh|[r]c_progressive_jp[e]g.py" || true
for i in $(seq 1 60); do pgrep -f "[r]c_progressive_jp[e]g.py" >/dev/null || break; sleep 1; done
for f in camera_config.yaml bm_command_state_v2.json config_journal.jsonl software_sha.txt; do [ -f $B/$f ] || cp -p $A/$f $B/; done
crontab -l | grep reboot' </dev/null 2>&1 | tee -a gate.log

DEV_SHA=$(git -C "$W" rev-parse --short=12 origin/development)
if [ "$(ssh -o BatchMode=yes pi@$H 'cat /home/pi/BM_Devel_Pi/software_sha.txt' </dev/null)" = "$DEV_SHA" ]; then
  log "development $DEV_SHA already deployed: skip"
else
log "deploy development"
git -C "$W" show origin/development:tools/rc_field_update.sh > /tmp/rc_field_update_w9.sh
scp -q /tmp/rc_field_update_w9.sh pi@$H:/tmp/rc_field_update.sh
ssh -o BatchMode=yes pi@$H "bash /tmp/rc_field_update.sh --repo /home/pi/repos/bm_cam_legacy --ref development --profile $PROF --leave-disarmed" </dev/null > ${H}_deploy_w9.log 2>&1
grep -E "SUMMARY|sha:" ${H}_deploy_w9.log | tee -a gate.log
grep -q "SUMMARY: PASS" ${H}_deploy_w9.log || { log "DEPLOY FAILED"; exit 1; }
fi

log "reset mode.run (stay_on process started directly; exit 72 expected)"
ssh -o BatchMode=yes pi@$H 'cd /home/pi/BM_Devel_Pi; nohup setsid /usr/bin/python3 -u rc_progressive_jpeg.py --transmit > /home/pi/w9proof/reset_run.log 2>&1 < /dev/null & sleep 1; echo started' </dev/null
until ssh -o BatchMode=yes pi@$H 'grep -q "SUP. stay_on: media=" /home/pi/w9proof/reset_run.log' </dev/null 2>/dev/null; do sleep 3; done
./cmd.sh W9.$H.reset-mode-run '{"id":63001,"c":"reset","k":["mode.run"]}' 6 $SPOT | grep -E 'OK id|REJ'
sleep 10
ssh -o BatchMode=yes pi@$H 'pgrep -f "[r]c_progressive_jp[e]g" >/dev/null && echo STILL-RUNNING || echo exited; python3 -c "import json; print(\"overlay\", json.load(open(\"/home/pi/BM_Devel_Pi/bm_command_state_v2.json\"))[\"overlay\"])"' </dev/null | tee -a gate.log

log "proof clip: --bench-drop-chunks start,5,17"
T0=$(date -u +%s)
ssh -o BatchMode=yes pi@$H 'cd /home/pi/BM_Devel_Pi; nohup setsid /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /usr/bin/python3 -u rc_progressive_jpeg.py --transmit --bench-drop-chunks start,5,17 > /home/pi/w9proof/proof_run.log 2>&1 < /dev/null & sleep 2; echo started' </dev/null
until ssh -o BatchMode=yes pi@$H 'grep -q "NOT sending START" /home/pi/w9proof/proof_run.log' </dev/null 2>/dev/null; do sleep 5; done
ssh -o BatchMode=yes pi@$H 'grep -E "KEY\] (media key|sent record)|BENCH" /home/pi/w9proof/proof_run.log' </dev/null | tee -a gate.log
KEY=$(ssh -o BatchMode=yes pi@$H 'grep -o "media key [0-9a-z]*" /home/pi/w9proof/proof_run.log | head -1 | cut -d" " -f3' </dev/null)
SENT=$(ssh -o BatchMode=yes pi@$H 'grep -o "sent record: [^ ]*" /home/pi/w9proof/proof_run.log | head -1 | cut -d" " -f3' </dev/null)
UNIT_SHA=$(ssh -o BatchMode=yes pi@$H "python3 -c \"import json; print(json.load(open('$SENT'))['sha256'])\"" </dev/null)
log "key=$KEY unit_sha=$UNIT_SHA"
until ! up; do sleep 10; done; log "proof run halted"

log "wait for the backend row and a settled missing set"
MID=""
while [ -z "$MID" ]; do
  MID=$(api "\"https://nereus-vision-staging.onrender.com/admin/ingest/devices/$DEV/heal-candidates?hours=6\"" | python3 -c "
import sys,json; d=json.load(sys.stdin)
for c in (d.get('candidates') or []) + (d.get('skipped') or []):
    if c.get('media_key')=='$KEY': print(c['media_id']); break" 2>/dev/null)
  [ -z "$MID" ] && sleep 60
done
log "row media_id=$MID"
until out=$(api "https://nereus-vision-staging.onrender.com/admin/ingest/media/$MID/missing"); ! echo "$out" | grep -q still_arriving; do sleep 60; done
echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); print('expected', d.get('expected'), 'received', d.get('received'), 'missing', d.get('ranges'), d.get('detail'))" | tee -a gate.log

for pass in 1 2 3; do
  out=$(api "https://nereus-vision-staging.onrender.com/admin/ingest/media/$MID/missing")
  echo "$out" | grep -q '"missing"' || break
  RSD=$(api "-X POST -H 'Content-Type: application/json' -d '{\"media_ids\":[$MID]}' https://nereus-vision-staging.onrender.com/admin/ingest/devices/$DEV/heal-commands" | python3 -c "import sys,json; print(json.load(sys.stdin)['command'])")
  log "heal pass $pass: $RSD"
  ./console.sh $SPOT "bridge cfg commit $BR s" 4 | grep -E "power on for"
  until up; do sleep 2; done
  OLD=$(newest)
  ssh -o BatchMode=yes pi@$H 'nohup setsid /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh >/dev/null 2>&1 </dev/null & echo started' </dev/null
  until [ "$(newest)" != "$OLD" ] && ssh -o BatchMode=yes pi@$H "grep -q '\[CMD\] subscribed' \$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1)" </dev/null 2>/dev/null; do sleep 1; done
  ./cmd.sh W9.$H.rsd$pass "$RSD" 4 $SPOT | grep -E 'OK id|REJ'
  ./waitlog.sh 'rsd id=' 'HEAL\] sent|Traceback' 180 $H | grep -E 'HEAL' | tee -a gate.log
  until ! up; do sleep 10; done; log "heal wake $pass halted"
  for i in $(seq 1 60); do
    st=$(api "https://nereus-vision-staging.onrender.com/admin/ingest/media/$MID/missing")
    echo "$st" | grep -q '"complete"\|nothing_missing' && break
    echo "$st" | grep -q '"missing"' && echo "$st" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if (d.get('received_age_s') or 0) >= 600 else 1)" && break
    sleep 60
  done
done

K=$(api "\"https://nereus-vision-staging.onrender.com/devices/$DEV/media?page_size=20\"" | python3 -c "
import sys,json
for r in json.load(sys.stdin):
    if r['media_id']==$MID: print(r['received_chunks'], r['expected_chunks'], r['is_complete'], r['r2_key'])")
log "final row: $K"
R2=$(echo "$K" | awk '{print $4}')
BK_SHA=$(ssh -o BatchMode=yes $N "set -a; . ~/.config/nereus/heal_driver.env; set +a; curl -s -L -m 60 -H \"Authorization: Bearer \$ADMIN_TOKEN\" \"https://nereus-vision-staging.onrender.com/media/view?r2_key=$R2\" -o /tmp/w9_$MID.h264; sha256sum /tmp/w9_$MID.h264 | cut -d' ' -f1" </dev/null)
log "backend_sha=$BK_SHA unit_sha=$UNIT_SHA"
[ "$BK_SHA" = "$UNIT_SHA" ] && log "W9 PASS" || log "W9 NOT YET (sha differs)"
