#!/bin/bash
# pool_day_0b.sh — runbook §0B for Wed 10/14 (pool at 1 PM), bmcam003/SPOT-31593C + bmcam004/SPOT-33507C, current code
# fdeb01a with keys ON. Run ONLY after (a) Nick's typed pool-day line in TE2's chat and (b) the consoles are back
# (ttyACM0/1 live). One subcommand per step; read each result before the next.
#   pre       pairing + both units halted + consoles live (read-only)
#   hold      §0B.2: hil_bus_always_on both (parallel)
#   sha       §0B.4: tools/pool_sha_check.sh (STOP unless ALL MATCH)
#   on        §0B.5: pool_mode.sh args --lens-position 0.8 && on && start, both
#   status    §0B.6: pool_cmd.py both status (on nereus000)
#   preview U one preview on unit U (JPEG only: the preview path never runs cjxl) + the same memory sampler
#   memloop   the -p memory check: ONE loop iteration on bmcam003 (the only pool path that runs cjxl, with -p since
#             still.raw.progressive=true reaches pool_sequence via the render), sampler on; then pool stop + restart
#             for a fresh run folder (a run with loop frames refuses a new loop).
#             bar: iteration 1 has the nrjxl product, no [ERR]/FALLBACK, cjxl peak <= 150 MiB, MemAvailable min >= 50 MiB
# Outputs: gate.log, deploy_s/pool_day/*. Bash 3.2.
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..; source hil/hil.env
G=$RUN/gate.log; D=$RUN/deploy_s/pool_day; mkdir -p "$D"
O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=4)
log(){ echo "$(date -u +%FT%TZ) [TE2 pool_day] $*" | tee -a "$G"; }
case "${1:?pre|hold|sha|on|status|preview|memloop}" in
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
memloop)
  U=bmcam003; S=$D/memloop_${U}_$(date -u +%H%MZ).txt
  ssh "${O[@]}" pi@$U 'for i in $(seq 1 600); do echo "$(date -u +%T) avail=$(awk "/MemAvailable/{print \$2}" /proc/meminfo) pool_hwm=$(for p in $(pgrep -f "[p]ool_sequence.py"); do awk "/VmHWM/{print \$2}" /proc/$p/status; done | sort -n | tail -1) $(for p in $(pgrep -x cjxl); do echo "cjxl=$p:$(awk "/VmHWM/{print \$2}" /proc/$p/status 2>/dev/null):$(tr "\0" " " < /proc/$p/cmdline | grep -c -- " -p ")"; done)"; sleep 0.5; done' </dev/null > "$S" 2>&1 & SP=$!
  sleep 2; log "memloop: loop on $U (sampler -> $(basename "$S"))"
  ssh "${O[@]}" pi@192.168.1.45 "python3 /home/pi/pool_tools/pool_cmd.py $U loop" </dev/null 2>&1 | cut -c1-200
  for i in $(seq 1 16); do sleep 15; st=$(ssh "${O[@]}" pi@192.168.1.45 "python3 /home/pi/pool_tools/pool_cmd.py $U status" </dev/null 2>&1 | grep -oE 'loop=[0-9]+/[0-9]+' | tail -1); echo "$(date -u +%T) $st"; case "$st" in loop=1/*|loop=2/*) break;; esac; done
  sleep 20; kill $SP 2>/dev/null
  ssh "${O[@]}" pi@192.168.1.45 "python3 /home/pi/pool_tools/pool_cmd.py $U stop" </dev/null 2>&1 | cut -c1-200
  ssh "${O[@]}" pi@$U 'L=$(ls -t /home/pi/pool_runs/*/pool.log 2>/dev/null | head -1); echo "log $L"; grep -E "nrjxl|ERR|FALLBACK|iteration 1|loop 1" "$L" | tail -8' </dev/null 2>&1 | cut -c1-200
  MINA=$(grep -oE 'avail=[0-9]+' "$S" | cut -d= -f2 | sort -n | head -1); PH=$(grep -oE 'pool_hwm=[0-9]+' "$S" | cut -d= -f2 | sort -n | tail -1)
  CJ=$(grep -oE 'cjxl=[0-9]+:[0-9]+:[0-9]' "$S" | awk -F: '{print $2}' | sort -n | tail -1); NP=$(grep -oE 'cjxl=[0-9]+:[0-9]+:1' "$S" | wc -l | tr -d ' '); N0=$(grep -oE 'cjxl=[0-9]+:[0-9]+:0' "$S" | wc -l | tr -d ' ')
  log "memloop $U: MemAvailable min=${MINA:-?} KiB (bar >= 51200), pool_sequence VmHWM=${PH:-?} KiB, cjxl peak=${CJ:-none} KiB (bar <= 153600), cjxl samples -p=$NP no-p=$N0. NEXT: restart for a fresh run folder (pool_mode.sh stop; wait; start) + status"
  ssh "${O[@]}" pi@$U 'bash /home/pi/pool_mode.sh stop; for i in $(seq 1 60); do pgrep -f "[p]ool_sequence.py" >/dev/null || break; sleep 3; done; bash /home/pi/pool_mode.sh start' </dev/null 2>&1 | grep -E 'SIGTERM|started|ERROR' | cut -c1-160
  log "memloop: $U pool process restarted (fresh run folder); check status lp=0.8 cal=0/0 loop=0/0" ;;
esac
