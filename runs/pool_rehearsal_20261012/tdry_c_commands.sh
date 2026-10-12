#!/bin/bash
# T-DRY step C (bmcam004): pool commands over the nereus000 console with pool_cmd.py (Nick OK b), compressed timings,
# + a READ-ONLY /proc sampler on the Pi (VmHWM/VmRSS of pool_sequence + children every 1 s) for peak RSS,
# + the :8090 viewer polled from the Mac for preview seconds-to-image and OK/PARTIAL.
# Output: tdry_c.log, tdry_rss.log, tdry_previews.json
set -u
cd "$(dirname "$0")"
H=bmcam004; MON=pi@192.168.1.45; O=(-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
L=tdry_c.log; ts(){ date -u +%FT%TZ; }
log(){ echo "$(ts) $*" | tee -a $L; }
P(){ log ">> pool_cmd $H $*"; ssh "${O[@]}" $MON "python3 /home/pi/pool_tools/pool_cmd.py $H $* --wait 30" </dev/null 2>&1 | tee -a $L; log "<< rc=${PIPESTATUS[0]}"; }
n_prev(){ curl -s -m 5 http://192.168.1.45:8090/previews.json | python3 -c "import json,sys;d=json.load(sys.stdin);print(len([x for x in d if 'bmcam004' in json.dumps(x)]))" 2>/dev/null || echo -1; }
wait_preview(){ local n0=$1 t0=$(date +%s); while [ $(( $(date +%s) - t0 )) -lt 120 ]; do n=$(n_prev); [ "$n" -gt "$n0" ] && { log "preview: viewer has a new bmcam004 entry after $(( $(date +%s) - t0 )) s"; curl -s -m 5 http://192.168.1.45:8090/previews.json > tdry_previews.json; python3 -c "import json;d=json.load(open('tdry_previews.json'));print(json.dumps(d[-1] if isinstance(d,list) else d)[:600])" | tee -a $L; return 0; }; sleep 2; done; log "preview: NO new viewer entry within 120 s"; }
# /proc sampler, read-only, up to 30 min
ssh "${O[@]}" pi@$H 'for i in $(seq 1 1800); do for p in $(pgrep -f "pool_sequence|cjxl|rpicam|libcamera|ffmpeg"); do [ -r /proc/$p/status ] && echo "$(date -u +%T) $p $(tr -d "\0" < /proc/$p/cmdline | cut -c1-40) $(grep -E "VmHWM|VmRSS" /proc/$p/status | tr -s " " | tr "\n" " ")"; done; sleep 1; done' </dev/null > tdry_rss.log 2>&1 &
RSSPID=$!
log "== T-DRY step C start"
P status
n0=$(n_prev); P preview; wait_preview "$n0"
P cal --duration-s 120 --interval-s 10
sleep 140; P status
P mark card_1m
P loop --iterations 3 --period-s 120
sleep 400; P status
for k in 1 2 3 4 5 6; do ssh "${O[@]}" $MON "tail -n 400 /home/pi/spotter_logs/SPOT-31593C/console_\$(date -u +%Y%m%d).log | grep -c 'pool loop 3/3'" </dev/null | grep -q '^[1-9]' && break; sleep 30; done
P status
n0=$(n_prev); P preview --lines 2-4,9; wait_preview "$n0"
P status
log "== T-DRY step C end"
kill $RSSPID 2>/dev/null
