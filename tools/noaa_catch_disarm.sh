#!/usr/bin/env bash
# noaa_catch_disarm.sh — catch an armed, self-halting unit awake and disarm it in ONE ssh session.
#
# Repo path: tools/noaa_catch_disarm.sh (runbook runs/noaa_update_20261027/RUNBOOK.md, step C)
# Pattern:   .claude/skills/bmcam-field-update "Pre-flight" (watcher) + bmcam-hotspot-update Phase 3
#
# Purpose: an armed unit (cron @reboot cycle + real halt) is reachable for only ~1–3 min
# per power-on. This script is piped over ssh from the Mac, so nothing has to be copied to
# the unit first:
#   until ssh -o ConnectTimeout=3 pi@bmcam002 'bash -s' < tools/noaa_catch_disarm.sh; do sleep 2; done
# On the unit it:
#   1. saves the ARMED crontab to /home/pi/crontab_backup_fieldupdate_<TS>.txt (the re-arm file);
#   2. writes a disarmed copy (every @reboot cycle line commented) to a file, checks it is
#      non-empty and has no live cycle line, then installs it (never `crontab -l | sed | crontab -`);
#   3. SIGTERMs a running cycle (SIGTERM skips Python's finally, so no halt fires);
#   4. prints a read-only survey for runbook step 0.
# Outputs: the lines above on stdout; exit 0 = disarmed, 1 = could not disarm (crontab untouched
# or restored). Touches nothing else.
# Known limitations: matches the RC (rc_run_capture_cycle.sh / rc_progressive_jpeg.py) and the
# legacy HEIC (run_capture_cycle.sh / main_pi_camera.py) cycle lines only.

set -u
TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARMED="/home/pi/crontab_backup_fieldupdate_${TS}.txt"
DISARMED="/home/pi/crontab_disarmed_${TS}.txt"
CYCLE='(rc_run_capture_cycle\.sh|rc_progressive_jpeg\.py|run_capture_cycle\.sh|main_pi_camera\.py)'

echo "[CATCH] $(hostname) caught at ${TS} (uptime: $(cut -d' ' -f1 /proc/uptime) s)"
crontab -l > "$ARMED" 2>/dev/null || true
sed -E "s%^([[:space:]]*@reboot[^#]*${CYCLE}.*)\$%# DISABLED noaa_catch ${TS}: \\1%" "$ARMED" > "$DISARMED"
if [[ -s "$ARMED" && ! -s "$DISARMED" ]]; then
  echo "[CATCH][FAIL] disarmed copy is empty; crontab NOT changed" >&2; exit 1
fi
if grep -Eq "^[[:space:]]*@reboot[^#]*${CYCLE}" "$DISARMED"; then
  echo "[CATCH][FAIL] a live cycle line is still in the disarmed copy; crontab NOT changed" >&2; exit 1
fi
crontab "$DISARMED"
if crontab -l 2>/dev/null | grep -Eq "^[[:space:]]*@reboot[^#]*${CYCLE}"; then
  crontab "$ARMED"; echo "[CATCH][FAIL] still armed after install; armed crontab restored" >&2; exit 1
fi
# Bracketed patterns so pkill never matches this shell's own command line.
pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py|[m]ain_pi_camera.py' && echo "[CATCH] cycle SIGTERMed"
echo "[CATCH] DISARMED. Armed crontab saved: $ARMED (sha256 $(sha256sum "$ARMED" | cut -c1-12))"
echo "[SURVEY] software_sha.txt: $(cat /home/pi/BM_Devel_Pi/software_sha.txt 2>/dev/null || echo MISSING)"
echo "[SURVEY] camera_schedule.yaml sha256: $(sha256sum /home/pi/BM_Devel_Pi/camera_schedule.yaml 2>/dev/null | cut -d' ' -f1)"
echo "[SURVEY] md5: $(md5sum /home/pi/BM_Devel_Pi/camera_schedule.yaml 2>/dev/null | cut -d' ' -f1)"
for f in camera_config.yaml bm_command_state.json bm_command_state_v2.json FORCE_SHRINK_FALLBACK NO_HALT; do
  [[ -e "/home/pi/BM_Devel_Pi/$f" ]] && echo "[SURVEY] PRESENT /home/pi/BM_Devel_Pi/$f" || echo "[SURVEY] absent  $f"
done
echo "[SURVEY] repo: $(git -C /home/pi/repos/bm_cam_legacy rev-parse --short=12 HEAD 2>/dev/null || echo 'NO CHECKOUT at /home/pi/repos/bm_cam_legacy')"
echo "[SURVEY] PyYAML: $(python3 -c 'import yaml; print(yaml.__version__)' 2>&1 | tail -1)"
echo "[SURVEY] disk free: $(df -m /home/pi | awk 'NR==2 {print $4}') MB; SD data: $(du -sh /home/pi/BM_Devel_Pi/images /home/pi/BM_Devel_Pi/cron_logs 2>/dev/null | tr '\n' ' ')"
echo "[SURVEY] serial0 -> $(readlink /dev/serial0 2>/dev/null || echo MISSING); processes: $(pgrep -af '[r]c_progressive_jpeg|[m]ain_pi_camera' | wc -l) cycle(s)"
echo "[SURVEY] crontab now:"; crontab -l | sed 's/^/[SURVEY]   /'
exit 0
