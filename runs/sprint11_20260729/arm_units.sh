#!/usr/bin/env bash
# filename: arm_units.sh
# description: Sprint11 — re-arm both units (real power_halt + @reboot cron), then verify.
#
# ORDER IS LOAD-BEARING. Pi side FIRST, Spotter power schedule LAST (that is
# set_spotter_schedules.sh, not this script). Once power_halt is real AND the
# Spotter is cycling, the unit halts itself at every cycle end and only the
# next power-on brings it back; a mistake made after that point costs a full
# 30-minute cycle to correct, and a mistake made while the bus is
# CONTINUOUSLY powered is unrecoverable without a human (soak finding 004).
#
# WHAT IT ARMS (per unit)
#   1. power_halt: enabled false->true, dry_run true->false   (real halt)
#   2. @reboot cron restored from the catch_awake_disarm.sh backup
#   3. verifies both and prints the resulting state
#
# The crontab backup timestamps are read from the disarm marker files that
# catch_awake_disarm.sh wrote — NOT hardcoded, so a re-run after a second
# disarm pass cannot restore a stale crontab.
#
# INPUT   $1 rundir (default runs/sprint11_20260729)
# OUTPUT  <rundir>/arm_<host>_<TS>.log per unit
set -uo pipefail

RUNDIR="${1:-runs/sprint11_20260729}"
mkdir -p "$RUNDIR"

UNITS=("100.103.35.24:bmcam003" "100.119.14.92:bmcam000")

# Hard wall-clock bound on every ssh (sshto.sh / INCIDENT_tailscale_ssh_check).
. "$(dirname "${BASH_SOURCE[0]}")/sshto.sh"

sshq() {
  local out rc
  out="$(ssh_to 90 "$1" "$2" 2>&1)"; rc=$?
  printf '%s\n' "$out" | grep -viE "^[[:space:]]*tailscale (login|status)"
  [ "$rc" -eq 124 ] && echo "[sshq][TIMEOUT] $1 did not answer within 90 s"
  return $rc
}

fail=0
for entry in "${UNITS[@]}"; do
  ip="${entry%%:*}"; name="${entry##*:}"
  marker="$RUNDIR/.disarmed_${name}"
  if [ ! -f "$marker" ]; then
    echo "[ARM][ERROR] no disarm marker for $name ($marker) — refusing to"
    echo "             guess a crontab backup timestamp. Run the catcher first."
    fail=1
    continue
  fi
  bts="$(cat "$marker")"
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  log="$RUNDIR/arm_${name}_${ts}.log"
  {
    echo "=== ARM $name ($ip) $(date -u +%FT%TZ) ==="
    echo "--- crontab backup source: /home/pi/crontab_backup_sprint11_${bts}.txt ---"

    echo "--- 1. power_halt -> REAL ---"
    sshq "$ip" "/usr/bin/python3 /tmp/patch_camera_schedule.py \
        /home/pi/BM_Devel_Pi/camera_schedule.yaml \
        --set power_halt.enabled=true --set power_halt.dry_run=false"

    echo "--- 2. @reboot cron -> ARMED ---"
    sshq "$ip" "test -f /home/pi/crontab_backup_sprint11_${bts}.txt \
      && crontab /home/pi/crontab_backup_sprint11_${bts}.txt \
      && echo 'restored from crontab_backup_sprint11_${bts}.txt' \
      || echo 'ERROR: crontab_backup_sprint11_${bts}.txt NOT FOUND'"

    echo "--- 3. verify ---"
    sshq "$ip" "echo -n 'active @reboot lines: '; crontab -l | grep -c '^@reboot'
      echo -n 'DISABLED markers left: '; crontab -l | grep -c 'DISABLED' || true
      echo 'crontab:'; crontab -l | grep -v '^#'
      echo -n 'halt enabled: '; sed -n '/^power_halt:/,/^[^ #]/p' /home/pi/BM_Devel_Pi/camera_schedule.yaml | grep -oE 'enabled: *(true|false)'
      echo -n 'halt dry_run: '; sed -n '/^power_halt:/,/^[^ #]/p' /home/pi/BM_Devel_Pi/camera_schedule.yaml | grep -oE 'dry_run: *(true|false)'
      echo -n 'txd: '; grep -oE 'image_transmit_delay_seconds: *[0-9.]+' /home/pi/BM_Devel_Pi/camera_schedule.yaml
      echo -n 'win: '; grep -oE 'max_run_time_min: *[0-9]+' /home/pi/BM_Devel_Pi/camera_schedule.yaml
      echo -n 'cap: '; grep -oE 'message_cap: *[0-9]+' /home/pi/BM_Devel_Pi/camera_schedule.yaml"
    echo "=== done $name ==="
  } 2>&1 | tee "$log"
  echo
done

[ "$fail" -eq 0 ] || exit 1

cat <<'NEXT'
=========================================================================
BOTH UNITS ARE NOW ARMED: they will halt at the end of every cycle.
From here on, SSH is only available in the minutes after a power-on.

LAST STEP: ./runs/sprint11_20260729/set_spotter_schedules.sh
  Unit A bmcam003 / SPOT-33507C -> 15 on / 15 off
  Unit B bmcam000 / SPOT-31593C -> 20 on / 10 off
=========================================================================
NEXT
