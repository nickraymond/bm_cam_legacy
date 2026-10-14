#!/bin/bash
# hourly_verify.sh HOST — read-only verify of one HOURLY production wake after the fdeb01a switch-on: from the top of
# the hour poll ssh; when up, run the cjxl argv/VmHWM sampler; at the listen window save the key cycle-log lines
# (CFG/KEY/RAW/HEAL/ERR) + sampler summary to deploy_s/HOST/hourly_<UTC hour>.txt. Usage: bash hourly_verify.sh bmcam003
set -u; cd "$(dirname "$0")"; H=$1
O=(-o BatchMode=yes -o ConnectTimeout=4 -o ServerAliveInterval=5 -o ServerAliveCountMax=4)
S(){ ssh "${O[@]}" pi@$H "$@" </dev/null; }
HR=$(python3 -c "import datetime as d;n=d.datetime.now(d.timezone.utc);h=n.replace(minute=0,second=0,microsecond=0);print((h if n.minute<10 else h+d.timedelta(hours=1)).strftime('%Y%m%dT%HZ'))")
T0=$(python3 -c "import datetime as d,sys;print(int(d.datetime.strptime(sys.argv[1],'%Y%m%dT%HZ').replace(tzinfo=d.timezone.utc).timestamp()))" $HR)
OUT=deploy_s/$H/hourly_$HR.txt; SMP=deploy_s/$H/cjxl_sampler_hourly_$HR.txt
until [ "$(date +%s)" -ge $((T0 - 5)) ]; do sleep 5; done
until S true 2>/dev/null || [ "$(date +%s)" -ge $((T0 + 300)) ]; do sleep 2; done
ssh "${O[@]}" pi@$H 'for i in $(seq 1 700); do for p in $(pgrep -x cjxl); do echo "$(date -u +%T.%N | cut -c1-12) pid=$p hwm=$(awk "/VmHWM/{print \$2}" /proc/$p/status 2>/dev/null) argv=$(tr "\0" " " < /proc/$p/cmdline 2>/dev/null)"; done; sleep 0.3; done' </dev/null > $SMP 2>/dev/null & SP=$!
L=""; while [ "$(date +%s)" -lt $((T0 + 600)) ]; do
  st=$(S 'f=$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1); echo "$f $(grep -c "post-transmit listen window: " $f)"' 2>/dev/null)
  set -- $st; [ "${2:-0}" -ge 1 ] && { L=$1; break; }; sleep 5; done
kill $SP 2>/dev/null
{ echo "== $H hourly wake $HR (log ${L:-NONE})"
  [ -n "$L" ] && S "grep -nE '\[CFG\] config v2|\[KEY\]|\[RAW\] (search|nrjxl|FALLBACK)|\[HEAL\]|ERR|cycle start|listen window' $L | cut -c1-230"
  grep -v '^bash' $SMP | awk '{split($2,a,"=");split($3,b,"=");p=a[2];h=b[2]+0;if(h>m[p])m[p]=h;if(index($0," -p "))pp[p]=1;n[p]++}END{for(p in m)printf "SAMPLER pid=%s samples=%d max_hwm=%d %s\n",p,n[p],m[p],(p in pp)?"P_FLAG":"NO_P"}'
} > $OUT 2>&1
cat $OUT
