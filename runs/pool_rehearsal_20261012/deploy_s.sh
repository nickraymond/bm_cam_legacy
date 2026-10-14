#!/bin/bash
# deploy_s.sh — held-bus deploy of development S (bm #146 + #147) to bmcam003/bmcam004 + the bmcam003 -p memory check
# + the console switch-on (Nick's typed OK 2026-10-13 9:03 PM PDT, APPROVALS.md). One subcommand per step so every
# step is run, read and logged by hand (DEPLOY_146_PLAN.md "REVISED" section = the sequence).
#
# Subcommands (HOST = bmcam003|bmcam004; the Spotter/bridge come from hil/hil.env, checked against tools/rig_map.json
# by hil_common's #170 guard inside the hil tools):
#   hold HOST              hil_bus_always_on.sh <SPOT> (unit must be HALTED) -> Pi up, cron disarmed, no cycle
#   pre HOST               read-only: runtime sha, crontab, ARMED backup dir; pull the last 3 cycle logs (baseline)
#   deploy HOST S7 [accept] rc_field_update --ref development --leave-disarmed [--accept-print-config-diff];
#                          verifies software_sha.txt starts with S7; log -> deploy_s/HOST/field_update_*.log
#   rearm HOST             crontab <- newest ~/hil_backup/<TS>/crontab_ARMED.txt; byte-check the @reboot line
#   halt HOST              tuned_halt.sh, wait until dark (the always-on bus stays up, the Pi stays down)
#   boot HOST              console `bridge cfg commit` = bus power-cycle -> production boot; starts the cjxl argv/
#                          VmHWM sampler; waits for the post-transmit listen window; prints the cycle log path
#   send HOST ID 'KVJSON'  console set {"id":ID,"c":"set","kv":KVJSON} via hil_cmd.sh (listen window must be open)
#   reset HOST ID          console reset of the 2 keys (rollback; drops them from the overlay)
#   report HOST [LOG]      key lines of a cycle log (CFG/KEY/RAW/HEAL/msgs/halt) + the sampler summary
#   down HOST              wait until the Pi is dark (it halts itself after the listen window)
# Outputs: runs/pool_rehearsal_20261012/deploy_s/<host>/..., gate.log lines.
# Limits: bash 3.2 (macOS); no unit write happens outside the subcommand you call.
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..; ROOT=$PWD
source hil/hil.env
G=$RUN/gate.log
O=(-o BatchMode=yes -o ConnectTimeout=4 -o ServerAliveInterval=5 -o ServerAliveCountMax=4)
PROD='@reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh'
log(){ echo "$(date -u +%FT%TZ) [TE2 deploy_s $H] $*" | tee -a "$G"; }
S(){ ssh "${O[@]}" "pi@$H" "$@" </dev/null; }
up(){ S true 2>/dev/null; }
rig(){ # host -> "SPOT BRIDGE" from hil.env (HIL_RIG_x="SPOT=BMCAM_00N=host=bridge")
  for r in "$HIL_RIG_A" "$HIL_RIG_B"; do
    IFS='=' read -r sp bm ho br <<<"$r"; [ "$ho" = "$1" ] && { echo "$sp $br"; return 0; }
  done; return 1; }
CMD=${1:?subcommand}; H=${2:?host}
case "$H" in bmcam003|bmcam004) ;; *) echo "REFUSED: $H is not a bench unit" >&2; exit 5;; esac
read -r SPOT BR <<<"$(rig "$H")"; [ -n "${SPOT:-}" ] && [ -n "${BR:-}" ] || { echo "no rig for $H in hil.env" >&2; exit 5; }
D=$RUN/deploy_s/$H; mkdir -p "$D"
profile(){ echo "$H/live_20260925"; }
wait_window(){ # $1 = log to skip (previous boot); echo the new cycle log once its listen window is open
  local prev=$1 st f a b
  for i in $(seq 1 150); do
    st=$(S 'f=$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1); echo "$f $(grep -c "post-transmit listen window: " $f) $(grep -c "post-transmit listen window done" $f)"' 2>/dev/null)
    read -r f a b <<<"$st"; [ "${a:-0}" = 1 ] && [ "${b:-1}" = 0 ] && [ "$f" != "$prev" ] && { echo "$f"; return 0; }
    sleep 4
  done; return 1; }
case "$CMD" in
hold)
  up && { echo "STOP: $H is up; hil_bus_always_on needs it HALTED"; exit 1; }
  log "hold: hil_bus_always_on $SPOT"
  HIL_RUN_DIR=$RUN hil/tools/hil_bus_always_on.sh "$SPOT"; rc=$?; log "hil_bus_always_on rc=$rc"; exit $rc ;;
pre)
  S 'echo sha=$(cat /home/pi/BM_Devel_Pi/software_sha.txt); echo up=$(cut -d. -f1 /proc/uptime)s; crontab -l | grep -E "@reboot"; B=$(ls -td /home/pi/hil_backup/*/ | head -1); echo armed_backup=$B; cat $B/crontab_ARMED.txt | grep -E "^@reboot"; pgrep -af "[r]c_run_capture_cycle|[c]jxl" || echo no-cycle; df -h / | tail -1; free -m | sed -n 2p' | tee "$D/pre_$(date -u +%H%MZ).txt"
  for f in $(S 'ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -3'); do scp -q "${O[@]}" "pi@$H:$f" "$D/baseline_$(basename "$f")"; done
  ls -l "$D"/baseline_* | awk '{print $5, $9}' ;;
deploy)
  S7=${3:?S7}; EXTRA=""; [ "${4:-}" = accept ] && EXTRA="--accept-print-config-diff"
  up || { echo "STOP: $H not up (hold first)"; exit 1; }
  S 'crontab -l | grep -E "^@reboot.*rc_run_capture_cycle"' && { echo "STOP: $H cron is ARMED (hold disarms it)"; exit 1; }
  FL=$D/field_update_$(date -u +%Y%m%dT%H%M%SZ)_${4:-check}.log
  log "deploy development want=$S7 profile=$(profile) ${EXTRA:-no-accept} -> $FL"
  S "cd /home/pi/repos/bm_cam_legacy && git fetch -q origin development && git show origin/development:tools/rc_field_update.sh > /tmp/rc_field_update_s.sh && bash /tmp/rc_field_update_s.sh --repo /home/pi/repos/bm_cam_legacy --ref development --profile $(profile) --leave-disarmed $EXTRA" > "$FL" 2>&1
  rc=$?; SHA=$(S 'cat /home/pi/BM_Devel_Pi/software_sha.txt' 2>/dev/null)
  grep -nE 'PARITY|parity|DIFF|^[-+] |restore command|effective hash|software_sha|SUMMARY|FAIL|ERROR' "$FL" | cut -c1-220 | tail -30
  log "rc_field_update rc=$rc sha=$SHA (want $S7) log=$(basename "$FL")"
  case "$SHA" in $S7*) [ $rc = 0 ] && log "DEPLOYED $SHA" || log "rc=$rc although sha matches: READ $FL";; *) log "NOT deployed (sha $SHA)";; esac ;;
rearm)
  B=$(S 'ls -td /home/pi/hil_backup/*/ | head -1'); B=${B%/}
  A=$(S "grep -E '^@reboot' $B/crontab_ARMED.txt")
  [ "$A" = "$PROD" ] || { echo "STOP: ARMED backup $B @reboot line differs from production: $A"; exit 1; }
  S "crontab $B/crontab_ARMED.txt"; NOW=$(S 'crontab -l | grep -E "^@reboot"')
  [ "$NOW" = "$PROD" ] && log "RE-ARMED from $B (byte-identical production @reboot line)" || { log "RE-ARM MISMATCH: $NOW"; exit 1; } ;;
halt)
  S 'pgrep -af "[r]c_run_capture_cycle|[c]jxl"' && { echo "STOP: a cycle is running on $H"; exit 1; }
  S 'nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null & echo halt-issued'
  for i in $(seq 1 30); do up || { log "dark after halt"; exit 0; }; sleep 3; done; log "WARNING still up 90 s after halt"; exit 1 ;;
boot)
  up && { echo "STOP: $H is up (a commit would cut it)"; exit 1; }
  PREV=$(cat "$D/last_log" 2>/dev/null || echo none)
  log "boot: bridge cfg commit $BR on $SPOT (bus power-cycle -> production boot)"
  HIL_RUN_DIR=$RUN hil/tools/hil_console.sh "$SPOT" "bridge cfg commit $BR s" 4 | grep -E "bus power|power on for|Reboot info"
  for i in $(seq 1 60); do up && break; sleep 2; done; up || { log "STOP: not up 120 s after commit"; exit 1; }
  SMP=$D/cjxl_sampler_$(date -u +%Y%m%dT%H%M%SZ).txt
  # cjxl argv + VmHWM every 0.3 s for 200 s (covers capture + the <= 3 encodes); read-only
  nohup ssh "${O[@]}" "pi@$H" 'for i in $(seq 1 660); do for p in $(pgrep -x cjxl); do echo "$(date -u +%T.%N | cut -c1-12) pid=$p hwm=$(awk "/VmHWM/{print \$2}" /proc/$p/status 2>/dev/null) argv=$(tr "\0" " " < /proc/$p/cmdline 2>/dev/null)"; done; sleep 0.3; done' </dev/null > "$SMP" 2>&1 &
  log "up; sampler -> $(basename "$SMP"); waiting for the listen window"
  L=$(wait_window "$PREV") || { log "STOP: no listen window"; exit 1; }
  echo "$L" > "$D/last_log"; log "listen window OPEN in $L"; echo "$L" ;;
send)
  ID=${3:?id}; KV=${4:?kvjson}
  log "send id=$ID set $KV via $SPOT"
  HIL_RUN_DIR=$RUN hil/tools/hil_cmd.sh "DEPLOY_S.$H.$ID" "{\"id\":$ID,\"c\":\"set\",\"kv\":$KV}" 12 "$SPOT" ;;
reset)
  ID=${3:?id}
  log "send id=$ID reset still.raw.progressive + uplink.media_key.heal_order via $SPOT"
  HIL_RUN_DIR=$RUN hil/tools/hil_cmd.sh "DEPLOY_S.$H.$ID" "{\"id\":$ID,\"c\":\"reset\",\"k\":[\"still.raw.progressive\",\"uplink.media_key.heal_order\"]}" 12 "$SPOT" ;;
report)
  L=${3:-$(cat "$D/last_log")}
  S "grep -nE '\[CFG\] config v2|\[KEY\]|\[RAW\]|\[HEAL\]|msgs|cycle start|listen window|halt|ERR|FALLBACK|overrun|skip' $L | cut -c1-230"
  SMP=$(ls -t "$D"/cjxl_sampler_*.txt 2>/dev/null | head -1)
  [ -n "$SMP" ] && { echo "-- sampler $(basename "$SMP"): per cjxl pid, max VmHWM KiB, -p in argv?";
    awk '{split($2,a,"=");split($3,b,"=");p=a[2];h=b[2]+0;if(h>m[p])m[p]=h;if(index($0," -p "))pp[p]="-p";n[p]++;if(!(p in f))f[p]=$1}END{for(p in m)printf "%s pid=%s samples=%d max_hwm=%d KiB (%.1f MiB) %s\n",f[p],p,n[p],m[p],m[p]/1024,(p in pp)?"ARGV HAS -p":"no -p"}' "$SMP" | sort; } ;;
down)
  for i in $(seq 1 120); do up || { log "dark"; exit 0; }; sleep 5; done; log "WARNING still up after 10 min"; exit 1 ;;
*) echo "unknown subcommand $CMD" >&2; exit 2 ;;
esac
