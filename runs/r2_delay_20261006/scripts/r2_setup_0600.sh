#!/bin/bash
# R2-DELAY setup in bmcam003's 06:00Z window (no cycle stop, no cron change: files take effect at the next boot).
# Bounded ssh everywhere (process-group timeout), no pkill. Writes: the patched module (backup first), r2_start_delay_s=0,
# base YAML uplink.msg_interval_s 1.0 (backup first). Then a read-only effective-config check.
set -u
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c
R=runs/r2_delay_20261006; G=$R/gate.log; H=bmcam003
log() { echo "$(date -u +%FT%TZ) [r2 setup] $*" | tee -a $G; }
B() { perl -e '$t=shift; $p=fork; if(!$p){setpgrp(0,0); exec @ARGV or exit 127} $SIG{ALRM}=sub{kill "TERM", -$p; sleep 1; kill "KILL", -$p; exit 124}; alarm $t; waitpid($p,0); exit($? >> 8)' "$@"; }
S() { B "${SB:-30}" ssh -o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=2 pi@$H "$@" < /dev/null; }
T=$(python3 -c "import datetime;print(int(datetime.datetime(2026,10,7,6,0,30,tzinfo=datetime.timezone.utc).timestamp()))")
[ "${NOW:-0}" = 1 ] || until [ "$(date +%s)" -ge "$T" ]; do sleep 5; done
for i in $(seq 1 40); do SB=8 S true 2>/dev/null && break; sleep 3; done
SB=8 S true 2>/dev/null || { log "ABORT: $H not up"; exit 1; }
TS=$(date -u +%Y%m%dT%H%M%SZ)
S "mkdir -p ~/hil_backup/r2_$TS && cp -p /home/pi/BM_Devel_Pi/rc_progressive_jpeg.py /home/pi/BM_Devel_Pi/camera_config.yaml ~/hil_backup/r2_$TS/ && cat /home/pi/BM_Devel_Pi/software_sha.txt | head -1 && sha256sum ~/hil_backup/r2_$TS/rc_progressive_jpeg.py" 2>&1 | tee -a $G
log "backup ~/hil_backup/r2_$TS (rc_progressive_jpeg.py + camera_config.yaml). RESTORE: cp ~/hil_backup/r2_$TS/rc_progressive_jpeg.py ~/hil_backup/r2_$TS/camera_config.yaml /home/pi/BM_Devel_Pi/ && rm -f /home/pi/BM_Devel_Pi/r2_start_delay_s"
B 30 scp -q -o BatchMode=yes -o ConnectTimeout=3 $R/scripts/rc_progressive_jpeg.r2.py pi@$H:/tmp/rc_progressive_jpeg.r2.py || { log "ABORT: scp failed"; exit 2; }
S "cd /home/pi/BM_Devel_Pi && python3 -m py_compile /tmp/rc_progressive_jpeg.r2.py && cp /tmp/rc_progressive_jpeg.r2.py rc_progressive_jpeg.py && echo 0 > r2_start_delay_s && sha256sum rc_progressive_jpeg.py && grep -c R2DELAY rc_progressive_jpeg.py && cat r2_start_delay_s" 2>&1 | tee -a $G
log "local sha256 of the patch: $(shasum -a 256 $R/scripts/rc_progressive_jpeg.r2.py | cut -c1-64)"
S "cd /home/pi/BM_Devel_Pi && python3 - <<'PY'
lines = open('camera_config.yaml').read().split('\n')
up = next((i for i, l in enumerate(lines) if l.split('#', 1)[0].rstrip() == 'uplink:'), None)
assert up is not None, 'no uplink: block'
done = False
for i in range(up + 1, len(lines)):
    l = lines[i]
    if l[:1].strip() and not l.startswith('#'):
        break
    if l.split('#', 1)[0].strip().startswith('msg_interval_s:'):
        ind = len(l) - len(l.lstrip(' ')); lines[i] = ' ' * ind + 'msg_interval_s: 1.0  # R2-DELAY reef pacing (was: ' + l.split(':', 1)[1].strip() + ')'; done = True; break
assert done, 'no uplink.msg_interval_s line'
open('camera_config.yaml', 'w').write('\n'.join(lines)); print('set uplink.msg_interval_s 1.0')
PY
diff ~/hil_backup/r2_$TS/camera_config.yaml camera_config.yaml" 2>&1 | tee -a $G
S "cd /home/pi/BM_Devel_Pi && python3 -c \"import config_v2 as c, supervisor_config as S; b = c.load_config('camera_config.yaml').base; e = S.resolve(b, c.read_state(S.state_path_for(b))); v = e.values; print('effective:', {k: v[k] for k in ('mode.media','mode.run','still.format','still.crop','still.output_width','still.message_cap','uplink.chunk_chars','uplink.msg_interval_s','uplink.network_type','uplink.lane.enabled')}, 'overlay', sorted(e.overlay), 'dropped', e.dropped)\"" 2>&1 | tee -a $G $R/pulled/bmcam003_effective_setup.txt
log "setup done (applies from the next boot; 06:00Z wake = setup, not counted)"
