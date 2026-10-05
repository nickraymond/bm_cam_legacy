#!/bin/bash
# Run one cjxl under the production guard (rc_raw_jxl GUARD_SH: ulimit -v 256000 KiB) and
# sample its /proc status every 10 ms: VmPeak (virtual, what ulimit -v caps) and VmHWM (RSS).
# usage: measure.sh <label> <guard_kib|none> cjxl args...
label=$1; kib=$2; shift 2
if [ "$kib" = none ]; then sh -c 'exec "$0" "$@"' "$@" >/dev/null 2>/tmp/err_$label & else
  sh -c "ulimit -v $kib; exec \"\$0\" \"\$@\"" "$@" >/dev/null 2>/tmp/err_$label & fi
pid=$!; peak=0; hwm=0; t0=$(date +%s.%N)
while kill -0 $pid 2>/dev/null; do
  if [ "$(cat /proc/$pid/comm 2>/dev/null)" = cjxl ]; then
    p=$(awk '/VmPeak/{print $2}' /proc/$pid/status 2>/dev/null); h=$(awk '/VmHWM/{print $2}' /proc/$pid/status 2>/dev/null)
    [ -n "$p" ] && [ "$p" -gt "$peak" ] && peak=$p; [ -n "$h" ] && [ "$h" -gt "$hwm" ] && hwm=$h
  fi; sleep 0.01; done
wait $pid; rc=$?; t1=$(date +%s.%N)
echo "$label guard=$kib rc=$rc VmPeak_kB=$peak VmHWM_kB=$hwm wall_s=$(echo "$t1 - $t0" | bc) stderr=$(tail -c 120 /tmp/err_$label | tr '\n' ' ')"
