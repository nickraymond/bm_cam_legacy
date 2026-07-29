#!/usr/bin/env bash
# filename: catch_awake_disarm.sh
# description: Sprint11 pre-flight — catch each bmcam Pi the moment its BM-bus
#              power window opens and disarm it before its cycle can halt.
#
# Adapted verbatim in mechanism from runs/sprint10_phaseE_20260728 (proven
# 2026-07-29). Only the run dir, backup filenames and deadline changed. The
# comments below are kept because every one of them records a bench failure
# that already cost time -- do not "clean them up".
#
# WHY
#   Both units are in Sprint10 test config: @reboot cron runs a capture cycle
#   on every power-up, that cycle HALTS the box in its finally block, and the
#   Spotter only powers the bus 20 min in every 30. So the Pi is reachable for
#   a short window that opens on the Spotter's schedule, not ours. This script
#   polls until SSH answers, then does the three disarm steps in the safe
#   order before the running cycle can halt:
#     1. SIGTERM any in-flight cycle - NOT SIGKILL, NOT halt
#     2. back up crontab, comment out @reboot
#     3. power_halt enabled:false / dry_run:true in camera_schedule.yaml
#
# INPUTS   $1 outdir (default runs/sprint11_20260729), $2 deadline minutes
# OUTPUTS  <outdir>/disarm_<host>_<TS>.log   full transcript per unit
#          <outdir>/disarm_state.json        machine-readable result per unit
#          crontab backup ON the Pi: /home/pi/crontab_backup_sprint11_<TS>.txt
#                                    (this filename is the RE-ARM source)
#
# EXAMPLE  ./runs/sprint11_20260729/catch_awake_disarm.sh
#
# LIMITATIONS
#   - Assumes Tailscale SSH key auth already works.
#   - Does NOT touch Spotter power config (done separately over the USB
#     console with `bridge cfg set` + `bridge cfg commit`).
set -uo pipefail

OUTDIR="${1:-runs/sprint11_20260729}"
DEADLINE_MIN="${2:-45}"          # 45 min > one full 20/10 Spotter period
HOSTS=("100.103.35.24:bmcam003" "100.119.14.92:bmcam000")
mkdir -p "$OUTDIR"
STATE="$OUTDIR/disarm_state.json"

sshq() {  # ssh with the Tailscale auth banner filtered out
  ssh -o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=no \
      -o ServerAliveInterval=5 -o ServerAliveCountMax=3 \
      "pi@$1" "$2" 2>&1 | grep -viE "tailscale|authenticate"
}

disarm_one() {
  local ip="$1" name="$2" ts log
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  log="$OUTDIR/disarm_${name}_${ts}.log"
  {
    echo "=== $name ($ip) disarm at $(date -u +%FT%TZ) ==="
    # ALL THREE DISARM STEPS IN ONE SSH ROUND TRIP. This is a RACE against
    # the @reboot cycle's power_halt. Separate ssh calls cost a round trip
    # each; one compound call closes the window to ~1-2 s.
    #
    # SIGTERM FIRST: the imminent threat is the RUNNING cycle's halt, and
    # @reboot cron has already fired by definition so it cannot start a
    # second one. Cron is disabled immediately after.
    #
    # NOTE the [r] bracket trick in every pkill/pgrep pattern below.
    # BUG FOUND ON THE BENCH 2026-07-29T00:50Z: `pkill -f
    # 'rc_run_capture_cycle.sh|...'` matches the REMOTE SHELL'S OWN COMMAND
    # LINE, because ssh passes this whole script as one argv string that
    # literally contains the pattern. pkill SIGTERM'd its own parent shell,
    # so bmcam003 got step 1 and nothing else -- cron and power_halt left
    # ARMED on a now-permanently-powered bus, the exact one-way trap this
    # script exists to prevent (finding 004). `[r]c_progressive_jpeg`
    # matches the real process but NOT this command line, which contains
    # the literal text `[r]c_...`.
    sshq "$ip" "set -x
      pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py'
      crontab -l > /home/pi/crontab_backup_sprint11_${ts}.txt
      crontab -l | sed 's|^@reboot |# DISABLED sprint11 ${ts}: @reboot |' | crontab -
      cp /home/pi/BM_Devel_Pi/camera_schedule.yaml \
         /home/pi/BM_Devel_Pi/camera_schedule.yaml.sprint11_${ts}
      python3 - <<'PYEOF'
import re
p='/home/pi/BM_Devel_Pi/camera_schedule.yaml'
s=open(p).read()
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*enabled:) true', r'\1 false', s, count=1)
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*dry_run:) false', r'\1 true', s, count=1)
open(p,'w').write(s)
i=s.find('power_halt:')
print('power_halt block now:'); print(s[i:i+220])
PYEOF
      set +x
      echo 'BACKUP=/home/pi/crontab_backup_sprint11_${ts}.txt'
      echo '--- verify: crontab ---'; crontab -l
      echo '--- verify: no cycle running ---'
      sleep 2; pgrep -af '[r]c_progressive|[r]c_run_capture' || echo 'no cycle running'
      echo '--- identity ---'; hostname; uptime; date -u
      echo '--- deployed sha ---'; cat /home/pi/BM_Devel_Pi/software_sha.txt 2>/dev/null
      echo '--- repo head ---'
      git -C /home/pi/repos/bm_cam_legacy rev-parse --short HEAD 2>/dev/null
      echo '--- deployed pacing ---'
      grep -A4 '^bm_serial:' /home/pi/BM_Devel_Pi/camera_schedule.yaml"
    echo "=== done $name ==="
  } 2>&1 | tee "$log"
  echo "$ts"
}

echo "[catch] waiting for bus power on both units (deadline ${DEADLINE_MIN} min)"
echo "[catch] $(date -u +%FT%TZ) start"
# macOS ships bash 3.2 (no associative arrays) -> per-unit marker files hold
# the disarm timestamp, which doubles as a restart-safe record of the re-arm
# source filename.
end=$(( $(date +%s) + DEADLINE_MIN * 60 ))
ndone=0
while [ "$(date +%s)" -lt "$end" ]; do
  ndone=0
  for entry in "${HOSTS[@]}"; do
    ip="${entry%%:*}"; name="${entry##*:}"
    marker="$OUTDIR/.disarmed_${name}"
    if [ -f "$marker" ]; then ndone=$((ndone + 1)); continue; fi
    if ssh -o ConnectTimeout=4 -o BatchMode=yes -o StrictHostKeyChecking=no \
         "pi@$ip" true >/dev/null 2>&1; then
      echo "[catch] $(date -u +%FT%TZ) $name IS UP -> disarming now"
      disarm_one "$ip" "$name" | tail -1 > "$marker"
      ndone=$((ndone + 1))
    fi
  done
  if [ "$ndone" -eq "${#HOSTS[@]}" ]; then
    echo "[catch] both units disarmed"
    break
  fi
  sleep 3   # tight poll: race against the cycle's power_halt
done

{
  echo "{"
  echo "  \"finished_utc\": \"$(date -u +%FT%TZ)\","
  echo "  \"units\": {"
  first=1
  for entry in "${HOSTS[@]}"; do
    name="${entry##*:}"
    marker="$OUTDIR/.disarmed_${name}"
    [ $first -eq 0 ] && echo ","
    first=0
    if [ -f "$marker" ]; then
      printf '    "%s": {"disarmed": true, "backup_ts": "%s"}' \
        "$name" "$(cat "$marker")"
    else
      printf '    "%s": {"disarmed": false, "backup_ts": null}' "$name"
    fi
  done
  echo ""
  echo "  }"
  echo "}"
} > "$STATE"
cat "$STATE"
[ "$ndone" -eq "${#HOSTS[@]}" ] || { echo "[catch] TIMEOUT - not all units caught"; exit 1; }
