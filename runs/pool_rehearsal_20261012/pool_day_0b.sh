#!/bin/bash
# pool_day_0b.sh — runbook §0B for Wed 10/14 (pool at 1 PM), bmcam003/SPOT-31593C + bmcam004/SPOT-33507C, current code
# fdeb01a with keys ON. Run ONLY after (a) Nick's typed pool-day line in TE2's chat and (b) the consoles are back
# (ttyACM0/1 live). One subcommand per step; read each result before the next.
#   pre       pairing + both units halted + consoles live (read-only)
#   hold      §0B.2: hil_bus_always_on both (parallel)
#   sha       §0B.4: tools/pool_sha_check.sh (STOP unless ALL MATCH)
#   on        §0B.5: pool_mode.sh args --lens-position 0.8 && on && start, both
#   status    §0B.6: pool_cmd.py both status (on nereus000)
#   preview U §0B+: one preview on unit U with a memory sampler (pool_sequence VmHWM, cjxl argv/VmHWM, MemAvailable)
#             bar: no kill / FALLBACK, cjxl peak <= 150 MiB, MemAvailable min >= 50 MiB
# Outputs: gate.log, deploy_s/pool_day/*. Bash 3.2.
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..; source hil/hil.env
G=$RUN/gate.log; D=$RUN/deploy_s/pool_day; mkdir -p "$D"
O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=4)
log(){ echo "$(date -u +%FT%TZ) [TE2 pool_day] $*" | tee -a "$G"; }
case "${1:?pre|hold|sha|on|status|preview}" in
pre)
  grep -E '^export HIL_RIG_[AB]=' hil/hil.env | cut -c1-80
  for h in bmcam003 bmcam004; do ssh "${O[@]}" pi@$h true </dev/null 2>/dev/null && echo "$h UP (wait for halt)" || echo "$h halted"; done
  ssh "${O[@]}" pi@192.168.1.45 'ls /dev/ttyACM* 2>&1; for s in SPOT-33507C SPOT-31593C; do echo "$s last: $(tail -1 /home/pi/spotter_logs/$s/console_$(date -u +%Y%m%d).log | cut -c1-21)"; done; date -u +%FT%TZ' </dev/null ;;
hold)
  log "§0B.2 hold both"
  HIL_RUN_DIR=$RUN hil/tools/hil_bus_always_on.sh SPOT-31593C > "$D/hold_003.out" 2>&1 & a=$!
  HIL_RUN_DIR=$RUN hil/tools/hil_bus_always_on.sh SPOT-33507C > "$D/hold_004.out" 2>&1 & b=$!
  wait $a; ra=$?; wait $b; rb=$?; tail -2 "$D/hold_003.out" "$D/hold_004.out" | cut -c1-160; log "hold rc 003=$ra 004=$rb" ;;
sha)
  tools/pool_sha_check.sh 2>&1 | tee "$D/sha_check_$(date -u +%H%MZ).txt"; rc=${PIPESTATUS[0]}; log "pool_sha_check rc=$rc" ;;
on)
  for h in bmcam003 bmcam004; do echo "== $h"; ssh "${O[@]}" pi@$h 'bash /home/pi/pool_mode.sh args --lens-position 0.8 && bash /home/pi/pool_mode.sh on && bash /home/pi/pool_mode.sh start' </dev/null 2>&1 | tee "$D/pool_on_$h.txt" | grep -E 'wrote|crontab|WARN|hash|started|ERROR' | cut -c1-170; log "$h pool_mode args/on/start rc=${PIPESTATUS[0]}"; done ;;
status)
  ssh "${O[@]}" pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both status' </dev/null 2>&1 | tee "$D/status_$(date -u +%H%MZ).txt" | cut -c1-200 ;;
preview)
  U=${2:?unit}; S=$D/preview_mem_${U}_$(date -u +%H%MZ).txt
  ssh "${O[@]}" pi@$U 'for i in $(seq 1 400); do echo "$(date -u +%T) avail=$(awk "/MemAvailable/{print \$2}" /proc/meminfo) pool_hwm=$(for p in $(pgrep -f "[p]ool_sequence.py"); do awk "/VmHWM/{print \$2}" /proc/$p/status; done | sort -n | tail -1) $(for p in $(pgrep -x cjxl); do echo "cjxl=$p:$(awk "/VmHWM/{print \$2}" /proc/$p/status 2>/dev/null):$(tr "\0" " " < /proc/$p/cmdline | grep -c -- " -p ")"; done)"; sleep 0.5; done' </dev/null > "$S" 2>&1 & SP=$!
  sleep 2; log "preview $U (memory sampler -> $(basename "$S"))"
  ssh "${O[@]}" pi@192.168.1.45 "python3 /home/pi/pool_tools/pool_cmd.py $U preview" </dev/null 2>&1 | tee "$D/preview_$U.txt" | cut -c1-200
  sleep 75; kill $SP 2>/dev/null   # pool_cmd may return at the ack, before the ~45 s capture+encode
  MINA=$(grep -oE 'avail=[0-9]+' "$S" | cut -d= -f2 | sort -n | head -1); PH=$(grep -oE 'pool_hwm=[0-9]+' "$S" | cut -d= -f2 | sort -n | tail -1)
  CJ=$(grep -oE 'cjxl=[0-9]+:[0-9]+:[0-9]' "$S" | awk -F: '{print $2}' | sort -n | tail -1); NP=$(grep -oE 'cjxl=[0-9]+:[0-9]+:1' "$S" | wc -l | tr -d ' ')
  log "preview $U memory: MemAvailable min=${MINA:-?} KiB (bar >= 51200), pool_sequence VmHWM=${PH:-?} KiB, cjxl peak=${CJ:-none} KiB (bar <= 153600), cjxl samples with -p=$NP" ;;
esac
