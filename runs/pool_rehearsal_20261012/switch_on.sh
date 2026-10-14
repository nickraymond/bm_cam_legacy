#!/bin/bash
# switch_on.sh — console switch-on of bm #146 + #147 after the fdeb01a deploys (Nick's typed OK 2026-10-13 9:03 PM PDT),
# in the units' HOURLY wakes (no bus hold: nereus000's input was weak at 9 PM). Bars set before the test
# (DEPLOY_146_PLAN.md "Regression bars").
#
#   bash switch_on.sh prog003   11 PM wake: wait for bmcam003's listen window; require software_sha fdeb01a +
#                               [CFG] registry=v13; console set still.raw.progressive true (id 66006).
#   bash switch_on.sh mem12     12 AM wake: cjxl argv/VmHWM sampler on bmcam003 from boot; at its listen window judge
#                               the -p memory check:
#                                 PASS = every "[RAW] searchN … peak_rss" ≤ 153600 KiB (150 MiB; guard 250) AND
#                                        "[RAW] nrjxl ready: … M msgs" with M ≤ 195 AND no "[RAW] FALLBACK" AND the
#                                        sampler saw cjxl with -p.
#                               PASS → bmcam003 set uplink.media_key.heal_order after (66007), then at bmcam004's
#                                      listen window set both keys (66008).
#                               FAIL → bmcam003 set still.raw.progressive false (66007); bmcam004 untouched.
# Outputs: gate.log, deploy_s/<host>/switch_*.txt, sampler files.  Needs nereus000's console (hil_cmd.sh).
# Limits: bash 3.2; each listen window is ~150 s, so this script decides on the pre-set bar without waiting for a human.
set -u
cd "$(dirname "$0")"; RUN=$PWD; DS="bash $RUN/deploy_s.sh"
O=(-o BatchMode=yes -o ConnectTimeout=4 -o ServerAliveInterval=5 -o ServerAliveCountMax=4)
log(){ echo "$(date -u +%FT%TZ) [TE2 switch_on] $*" | tee -a "$RUN/gate.log"; }
S(){ local h=$1; shift; ssh "${O[@]}" pi@$h "$@" </dev/null; }
wait_listen(){ # $1 host, $2 deadline epoch -> echo the cycle log whose listen window is open
  local st f a b
  while [ "$(date +%s)" -lt "$2" ]; do
    st=$(S $1 'f=$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1); echo "$f $(grep -c "post-transmit listen window: " $f) $(grep -c "post-transmit listen window done" $f)"' 2>/dev/null)
    read -r f a b <<<"$st"; [ "${a:-0}" = 1 ] && [ "${b:-1}" = 0 ] && { echo "$f"; return 0; }; sleep 4
  done; return 1; }
at(){ python3 -c "import datetime,sys;print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$1"; }
hour_utc(){ # next top of the hour (UTC ISO) from now, or this one if within the first 10 min
  python3 -c "import datetime as d;n=d.datetime.now(d.timezone.utc);h=n.replace(minute=0,second=0,microsecond=0);print((h if n.minute<10 else h+d.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))"; }
MODE=${1:?prog003|mem12}
case "$MODE" in
prog003)
  H=bmcam003; W=$(hour_utc); DL=$(( $(at "$W") + 600 ))
  log "prog003: waiting for $H's listen window in the $W wake"
  L=$(wait_listen $H $DL) || { log "prog003: no listen window by $W+10 min: NOT sent"; exit 1; }
  SHA=$(S $H 'cat /home/pi/BM_Devel_Pi/software_sha.txt'); CFG=$(S $H "grep -m1 '\[CFG\] config v2' $L")
  log "prog003: window open ($L); sha=$SHA; $CFG"
  case "$SHA" in fdeb01a*) ;; *) log "prog003: STOP, sha is not fdeb01a"; exit 1;; esac
  echo "$CFG" | grep -q 'registry=v13' || { log "prog003: STOP, registry is not v13"; exit 1; }
  $DS send $H 66006 '{"still.raw.progressive":true}' | tee "$RUN/deploy_s/$H/switch_66006.txt" ;;
mem12)
  W=$(hour_utc); T0=$(at "$W"); DL=$(( T0 + 600 )); H=bmcam003
  log "mem12: waiting for $H to boot in the $W wake (sampler from boot)"
  until [ "$(date +%s)" -ge $(( T0 - 10 )) ]; do sleep 5; done
  until S $H true 2>/dev/null || [ "$(date +%s)" -ge "$DL" ]; do sleep 2; done
  SMP=$RUN/deploy_s/$H/cjxl_sampler_mem12_$(date -u +%Y%m%dT%H%M%SZ).txt
  ssh "${O[@]}" pi@$H 'for i in $(seq 1 700); do for p in $(pgrep -x cjxl); do echo "$(date -u +%T.%N | cut -c1-12) pid=$p hwm=$(awk "/VmHWM/{print \$2}" /proc/$p/status 2>/dev/null) argv=$(tr "\0" " " < /proc/$p/cmdline 2>/dev/null)"; done; sleep 0.3; done' </dev/null > "$SMP" 2>&1 &
  SP=$!; log "mem12: $H up; sampler -> $(basename "$SMP")"
  L=$(wait_listen $H $DL) || { log "mem12: no listen window on $H: NOTHING sent (judge from the log later)"; kill $SP 2>/dev/null; exit 1; }
  kill $SP 2>/dev/null
  OUT=$RUN/deploy_s/$H/switch_mem12.txt
  S $H "grep -nE '\[CFG\] config v2|\[KEY\]|\[RAW\]|\[HEAL\]' $L | cut -c1-230" > "$OUT"
  awk '{split($2,a,"=");split($3,b,"=");p=a[2];h=b[2]+0;if(h>m[p])m[p]=h;if(index($0," -p "))pp[p]=1;n[p]++}END{for(p in m)printf "SAMPLER pid=%s samples=%d max_hwm=%d %s\n",p,n[p],m[p],(p in pp)?"P_FLAG":"NO_P"}' "$SMP" >> "$OUT"
  cat "$OUT"
  MAXRSS=$(grep -oE 'peak_rss=[0-9]+' "$OUT" | cut -d= -f2 | sort -n | tail -1)
  MSGS=$(grep -oE 'nrjxl ready: [0-9]+ B, [0-9]+ msgs' "$OUT" | awk '{print $5}' | tail -1)
  FB=$(grep -c 'FALLBACK' "$OUT"); PF=$(grep -c 'P_FLAG' "$OUT"); NP=$(grep -c 'NO_P' "$OUT")
  V=PASS; WHY=""
  [ -n "$MAXRSS" ] && [ "$MAXRSS" -le 153600 ] || { V=FAIL; WHY="$WHY peak_rss=${MAXRSS:-none}"; }
  [ -n "$MSGS" ] && [ "$MSGS" -le 195 ] || { V=FAIL; WHY="$WHY msgs=${MSGS:-none}"; }
  [ "$FB" = 0 ] || { V=FAIL; WHY="$WHY fallback"; }
  [ "$PF" -ge 1 ] && [ "$NP" = 0 ] || { V=FAIL; WHY="$WHY p_flag=$PF/no_p=$NP"; }
  log "mem12 VERDICT $V: max cjxl peak_rss=${MAXRSS:-none} KiB (bar 153600), nrjxl msgs=${MSGS:-none} (bar 195), fallbacks=$FB, cjxl pids with -p=$PF without=$NP${WHY:+ ; FAIL:$WHY}"
  if [ "$V" = PASS ]; then
    $DS send $H 66007 '{"uplink.media_key.heal_order":"after"}' | tee "$RUN/deploy_s/$H/switch_66007.txt"
    H4=bmcam004; L4=$(wait_listen $H4 $DL) || { log "mem12: no listen window on $H4: 66008 NOT sent"; exit 1; }
    SHA4=$(S $H4 'cat /home/pi/BM_Devel_Pi/software_sha.txt')
    case "$SHA4" in fdeb01a*) $DS send $H4 66008 '{"still.raw.progressive":true,"uplink.media_key.heal_order":"after"}' | tee "$RUN/deploy_s/$H4/switch_66008.txt" ;;
      *) log "mem12: $H4 sha $SHA4 is not fdeb01a: 66008 NOT sent";; esac
  else
    $DS send $H 66007 '{"still.raw.progressive":false}' | tee "$RUN/deploy_s/$H/switch_66007_rollback.txt"
  fi ;;
*) echo "prog003|mem12" >&2; exit 2 ;;
esac
