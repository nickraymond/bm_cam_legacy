#!/bin/bash
# soak_controller.sh — Sprint26 S2 soak controller on nereus000 (cron: 20,50 * * * *).
# Runs with the camera Mac offline. Both slots are BUS-OFF on the 30-min schedule, so
# nothing here collides with bm-heal-driver (it publishes at bus-on +30..260 s).
#  1. queued console commands: /home/pi/s2soak/queue/<YYYYmmddHHMM>_<SPOT>.cmd
#     (one console line each) whose time <= now are sent via the monitor's cmd.txt,
#     then moved to queue/sent/. Used for the mote-cache test: a command sent while the
#     bus is off must reach the camera at the next wake.
#  2. `note sync` on both Spotters: drain the Notecard (it only syncs ~hourly by
#     itself and filled to 34% on 2026-09-26 with extra bursts).
# Log: /home/pi/s2soak/controller.log (UTC). Stop: crontab -r (backup: crontab_before_s2soak.txt).
Q=/home/pi/s2soak/queue; L=/home/pi/s2soak/controller.log; ROOT=/home/pi/spotter_logs
mkdir -p $Q/sent
now=$(date -u +%Y%m%d%H%M)
send() {  # send SPOT LINE — wait for the monitor to consume, never clobber a pending line
  local d=$ROOT/$1; for i in $(seq 1 60); do [ -s $d/cmd.txt ] || break; sleep 1; done
  printf '%s\n' "$2" > $d/cmd.txt
  for i in $(seq 1 20); do [ -s $d/cmd.txt ] || break; sleep 0.5; done
  echo "$(date -u +%FT%TZ) $1 > $2 $([ -s $d/cmd.txt ] && echo NOT-CONSUMED || echo consumed)" >> $L
}
for f in $(ls $Q/*.cmd 2>/dev/null | sort); do
  b=$(basename $f .cmd); due=${b%%_*}; spot=${b#*_}
  if [ "$due" -le "$now" ]; then send "$spot" "$(cat $f)"; mv $f $Q/sent/; sleep 3; fi
done
for s in SPOT-33507C SPOT-31593C; do
  pct=$(grep -h "Notecard is" $ROOT/$s/console_$(date -u +%Y%m%d).log 2>/dev/null | tail -1 | grep -o '[0-9.]* pct')
  echo "$(date -u +%FT%TZ) $s notecard before sync: ${pct:-unknown}" >> $L
  send "$s" "note sync"; sleep 2
done
