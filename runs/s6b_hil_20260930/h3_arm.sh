#!/bin/bash
# h3_arm.sh HOST SPOT BRIDGE CMDID — S6b HIL H3 (PLAN_S6 §9.14): put a HALTED, DISARMED unit
# on a HELD bus into stay_on, trigger-only, ARMED. Bus-cycle wake -> restore the armed
# crontab (fires only at boot, so arming mid-run is safe) -> one manual per_boot wake ->
# `set mode.run stay_on` (console, id CMDID) right after it subscribes -> it halts -> bus-cycle
# wake -> cron starts stay_on. Prints the stay_on startup line.
set -u
H=$1; SPOT=$2; BR=$3; CID=$4
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE"
log() { echo "$(date -u +%FT%TZ) [$H] $*" | tee -a gate.log; }
up() { ssh -o BatchMode=yes -o ConnectTimeout=3 pi@$H true </dev/null 2>/dev/null; }
newest() { ssh -o BatchMode=yes pi@$H 'ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1' </dev/null; }
wake() { ./console.sh $SPOT "bridge cfg commit $BR s" 4 | grep -E "power on for"; until up; do sleep 2; done; }

up && { log "UP: expected halted; refusing"; exit 2; }
log "wake 1 (bus cycle)"; wake
ssh -o BatchMode=yes pi@$H 'crontab /home/pi/w9proof/backup/crontab_ARMED.txt && crontab -l | grep reboot' </dev/null | tee -a gate.log
OLD=$(newest)
ssh -o BatchMode=yes pi@$H 'nohup setsid /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh >/dev/null 2>&1 </dev/null & echo started' </dev/null
until [ "$(newest)" != "$OLD" ] && ssh -o BatchMode=yes pi@$H "grep -q '\[CMD\] subscribed' \$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1)" </dev/null 2>/dev/null; do sleep 1; done
./cmd.sh H3.$H.set-stay_on "{\"id\":$CID,\"c\":\"set\",\"kv\":{\"mode.run\":\"stay_on\"}}" 5 $SPOT | grep -E 'OK id|REJ' | tee -a gate.log
log "waiting for the per_boot wake to halt"; until ! up; do sleep 10; done; sleep 8
log "wake 2 (bus cycle) -> cron -> stay_on"; wake
until ssh -o BatchMode=yes pi@$H "grep -q 'SUP. stay_on: media=' \$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1)" </dev/null 2>/dev/null; do sleep 3; done
ssh -o BatchMode=yes pi@$H "grep -E 'SUP. stay_on: media=|RUNTIME' \$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1); crontab -l | grep reboot; cat /home/pi/BM_Devel_Pi/software_sha.txt" </dev/null | tee -a gate.log
log "H3 armed: stay_on, trigger-only, held bus"
