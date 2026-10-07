#!/bin/bash
# REMOTE-RESET prep (EM card, Nick-approved 12:30 PDT): make bmcam004 safe for an unscheduled bus cut on SPOT-31593C.
# In the ~20:02:25Z window: back up the ARMED crontab, replace the cycle with `@reboot sleep 45 && halt` (the Pi boots,
# idles 45 s, halts — halted long before the :05/:06 sync where a cloud reset would execute), stop the running cycle
# (bracket pkill), read back, halt now. Restore: crontab ~/hil_backup/<TS>/crontab_ARMED.txt (then the next window runs
# the normal cycle). Every ssh is bounded.
set -u
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c
R=runs/remote_reset_20261007; G=$R/gate.log; H=bmcam004
log() { echo "$(date -u +%FT%TZ) [reset-prep] $*" | tee -a $G; }
B() { perl -e '$t=shift; $p=fork; if(!$p){setpgrp(0,0); exec @ARGV or exit 127} $SIG{ALRM}=sub{kill "TERM", -$p; sleep 1; kill "KILL", -$p; exit 124}; alarm $t; waitpid($p,0); exit($? >> 8)' "$@"; }
S() { B "${SB:-20}" ssh -o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=2 pi@$H "$@" < /dev/null; }
T=$(python3 -c "import datetime;print(int(datetime.datetime(2026,10,7,20,2,20,tzinfo=datetime.timezone.utc).timestamp()))")
until [ "$(date +%s)" -ge "$T" ]; do sleep 5; done
for i in $(seq 1 50); do SB=8 S true 2>/dev/null && break; sleep 3; done
SB=8 S true 2>/dev/null || { log "ABORT: $H not up"; exit 1; }
TS=$(date -u +%Y%m%dT%H%M%SZ)
S "mkdir -p ~/hil_backup/$TS && crontab -l > ~/hil_backup/$TS/crontab_ARMED.txt && { crontab -l | sed -E 's|^(@reboot.*[r]c_run_capture_cycle)|# DISARMED_FOR_REMOTE_RESET \1|'; echo '@reboot sleep 45 && sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh # REMOTE_RESET_SAFE_MODE'; } | crontab - && pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py|[m]ain_pi_camera.py'; sleep 1; crontab -l | grep -E '@reboot'" 2>&1 | tee -a $G
log "safe mode installed (backup ~/hil_backup/$TS/crontab_ARMED.txt). RESTORE: ssh pi@$H 'crontab ~/hil_backup/$TS/crontab_ARMED.txt'"
S 'nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null & echo halt-issued' 2>&1 | tee -a $G
for i in $(seq 1 10); do sleep 3; SB=8 S true 2>/dev/null || break; done
SB=8 S true 2>/dev/null && log "WARNING: still up" || log "$H dark $(date -u +%T); from the next window it boots and halts ~45 s after boot"
