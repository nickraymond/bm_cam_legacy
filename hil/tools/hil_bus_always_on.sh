#!/bin/bash
# hil_bus_always_on.sh — hold a bench Spotter's BM bus ON (bridgePowerControllerEnabled 0) for bench work that needs the
# camera Pi up longer than a 10-min window (Sprint28 JPEG-XL R0–R3, Nick's OK 2026-10-04 in the TE chat), and catch
# the Pi awake so it does NOT run + halt its armed per_boot cycle (a halted Pi on an always-on bus never comes back).
#
# Inputs:   $1 SPOT-ID (bench rigs only). Preconditions (REFUSES otherwise): the unit is HALTED (ssh fails).
# Steps:    read back → set bridgePowerControllerEnabled 0 → commit (bus on, stays on) → poll ssh every 2 s →
#           in ONE session: back up the ARMED crontab, disarm the @reboot RC line, SIGTERM the cycle (no finally →
#           no halt) → read back. Writes the restore commands into gate.log BEFORE acting.
# Outputs:  gate.log lines in $HIL_RUN_DIR; exit 0 = bus held on, Pi up, cron disarmed, no cycle running.
# Restore:  ssh pi@<host> 'crontab ~/hil_backup/<TS>/crontab_ARMED.txt' ; halt the Pi (tuned_halt.sh) ;
#           hil/tools/hil_restore_schedule.sh <SPOT>   (controller 1, 3600000/600000, commit, stub boot halted)
# Example:  HIL_RUN_DIR=runs/s28_ladder_20261005 hil/tools/hil_bus_always_on.sh SPOT-31593C
# Limits:   if the Pi is not reachable within 120 s of the commit it is LEFT on the always-on bus running its cycle,
#           which will halt it: then restore the schedule (the next window boots it) and retry.
set -u
SPOT="${1:?SPOT-ID}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_spot "$SPOT"
LINE=$(_hil_rigs | awk -F= -v s="$SPOT" '$1 == s')
H=$(echo "$LINE" | cut -d= -f3); BR=$(echo "$LINE" | cut -d= -f4)
[ -n "$H" ] && [ -n "$BR" ] || { echo "[busOn] rig line for $SPOT lacks host/bridge"; exit 2; }
hil_require_host "$H"
RUN="$HIL_RUN_DIR"; mkdir -p "$RUN"
CON="$HIL_TOOLS_DIR/hil_console.sh"
log() { echo "$(date -u +%FT%TZ) [busOn $SPOT/$H] $*" | tee -a "$RUN/gate.log"; }
up() { ssh -o BatchMode=yes -o ConnectTimeout=3 "pi@$H" true < /dev/null 2>/dev/null; }
rb() { v=$("$CON" "$SPOT" "bridge cfg get $BR s bridgePowerControllerEnabled" 3 2>/dev/null | grep -oE "Value: *[0-9]+" | grep -oE "[0-9]+" | tail -1); echo "$v"; }
if up; then log "REFUSED: $H is UP (halt it first; a commit cuts a running Pi)"; exit 2; fi
TS=$(date -u +%Y%m%dT%H%M%SZ)
log "before: bridgePowerControllerEnabled=$(rb). RESTORE: ssh pi@$H 'crontab ~/hil_backup/$TS/crontab_ARMED.txt'; halt; hil/tools/hil_restore_schedule.sh $SPOT"
"$CON" "$SPOT" "bridge cfg set $BR s u bridgePowerControllerEnabled 0" 4 | grep -E "Value|Succes" | tee -a "$RUN/gate.log"
"$CON" "$SPOT" "bridge cfg commit $BR s" 4 | grep -E "Reboot info|bus power|power on for" | tee -a "$RUN/gate.log"
log "committed (bus held on); catching the boot"
T0=$(date -u +%s)
until up; do sleep 2; [ $(( $(date -u +%s) - T0 )) -gt 120 ] && { log "FAIL: Pi not reachable in 120 s (its armed cycle will run and halt it)"; exit 1; }; done
ssh -o BatchMode=yes "pi@$H" "mkdir -p ~/hil_backup/$TS && crontab -l > ~/hil_backup/$TS/crontab_ARMED.txt && crontab -l | sed -E 's|^(@reboot.*rc_run_capture_cycle)|# DISARMED_BUS_ON \1|' | crontab - && pkill -TERM -f 'rc_run_capture_cycle.sh|rc_progressive_jpeg.py|main_pi_camera.py'; sleep 2; echo up=\$(cut -d. -f1 /proc/uptime)s; pgrep -af 'rc_run_capture_cycle|rc_progressive_jpeg' || echo no-cycle; crontab -l | grep -E '@reboot'" < /dev/null 2>&1 | tee -a "$RUN/gate.log"
A=$(rb); log "after: bridgePowerControllerEnabled=$A"
[ "$A" = 0 ] && up && { log "bus held ON, $H up + disarmed (backup ~/hil_backup/$TS)"; exit 0; }
log "read-back/ssh check failed (controller=$A)"; exit 1
