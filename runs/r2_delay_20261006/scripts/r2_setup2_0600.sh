#!/bin/bash
# 06:00Z setup wake (not counted): after this cycle's send decision (uptime > 80 s), install the patch with budget
# logging (80c3662) over the 05:00Z one (same backup), confirm this cycle's [R2DELAY] line, keep r2_start_delay_s=0 (A at 07Z).
set -u
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c
R=runs/r2_delay_20261006; G=$R/gate.log; H=bmcam003
log() { echo "$(date -u +%FT%TZ) [r2 setup2] $*" | tee -a $G; }
B() { perl -e '$t=shift; $p=fork; if(!$p){setpgrp(0,0); exec @ARGV or exit 127} $SIG{ALRM}=sub{kill "TERM", -$p; sleep 1; kill "KILL", -$p; exit 124}; alarm $t; waitpid($p,0); exit($? >> 8)' "$@"; }
S() { B "${SB:-30}" ssh -o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=2 pi@$H "$@" < /dev/null; }
T=$(python3 -c "import datetime;print(int(datetime.datetime(2026,10,7,6,0,30,tzinfo=datetime.timezone.utc).timestamp()))")
until [ "$(date +%s)" -ge "$T" ]; do sleep 5; done
for i in $(seq 1 40); do SB=8 S true 2>/dev/null && break; sleep 3; done
SB=8 S true 2>/dev/null || { log "ABORT: $H not up"; exit 1; }
for i in $(seq 1 30); do up=$(SB=8 S "cut -d. -f1 /proc/uptime" 2>/dev/null); [ "${up:-0}" -ge 80 ] && break; sleep 5; done
log "uptime ${up:-?} s"
B 30 scp -q -o BatchMode=yes -o ConnectTimeout=3 $R/scripts/rc_progressive_jpeg.r2.py pi@$H:/tmp/rc_progressive_jpeg.r2.py || { log "ABORT: scp"; exit 2; }
S "cd /home/pi/BM_Devel_Pi && python3 -m py_compile /tmp/rc_progressive_jpeg.r2.py && cp /tmp/rc_progressive_jpeg.r2.py rc_progressive_jpeg.py && sha256sum rc_progressive_jpeg.py | cut -c1-16 && echo 0 > r2_start_delay_s && cat r2_start_delay_s && grep -h '\[R2DELAY\]' \$(ls -t cron_logs/rc_cycle_*.log | head -1) | tail -1" 2>&1 | tee -a $G
log "expected sha de34a3f160ff1119 (80c3662); r2_start_delay_s=0 for the 07:00Z A wake"
