#!/usr/bin/env bash
# filename: arm_overnight.sh
# description: Arm both bmcam units for the overnight A/B (halt + cron), then verify.
#
# ORDER IS LOAD-BEARING. Do the Pi side FIRST and the Spotter power schedule
# LAST (that step is NOT in this script — see the printout at the end).
# Once power_halt is real AND the Spotter is cycling, the unit halts itself at
# every cycle end and only the next power-on brings it back; a mistake made
# after that point costs a full 30-minute cycle to correct, and a mistake made
# while the bus is CONTINUOUSLY powered is unrecoverable without a human
# (soak finding 004).
#
# WHAT IT ARMS (per unit)
#   1. power_halt: enabled false->true, dry_run true->false   (real halt)
#   2. @reboot cron re-enabled from the Phase E pre-disarm backup
#   3. verifies both, and prints the resulting crontab
#
# INPUT   none (hosts + backup timestamps are pinned below)
# OUTPUT  <rundir>/arm_<host>_<TS>.log per unit
#
# Backups referenced here were captured by catch_awake_disarm.sh at the start
# of Phase E; they are the authoritative pre-disarm crontabs.
set -uo pipefail

RUNDIR="${1:-runs/sprint10_overnight_20260729}"
mkdir -p "$RUNDIR"

# host:name:crontab-backup-timestamp
UNITS=(
  "100.103.35.24:bmcam003:20260729T005002Z"
  "100.119.14.92:bmcam000:20260729T005317Z"
)

sshq() {
  ssh -n -o ConnectTimeout=12 -o BatchMode=yes "pi@$1" "$2" 2>&1 \
    | grep -viE "tailscale|authenticate"
}

for entry in "${UNITS[@]}"; do
  ip="${entry%%:*}"; rest="${entry#*:}"; name="${rest%%:*}"; bts="${rest##*:}"
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  log="$RUNDIR/arm_${name}_${ts}.log"
  {
    echo "=== ARM $name ($ip) $(date -u +%FT%TZ) ==="

    echo "--- 1. power_halt -> REAL ---"
    sshq "$ip" "cd /home/pi/BM_Devel_Pi && cp camera_schedule.yaml camera_schedule.yaml.prearm_${ts} && /usr/bin/python3 - <<PY
import re
p='camera_schedule.yaml'; s=open(p).read()
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*enabled:) false', r'\1 true', s, count=1)
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*dry_run:) true', r'\1 false', s, count=1)
open(p,'w').write(s)
i=s.find('power_halt:'); print(s[i:i+150])
PY"

    echo "--- 2. @reboot cron -> ARMED (from Phase E backup) ---"
    sshq "$ip" "test -f /home/pi/crontab_backup_phaseE_${bts}.txt \
      && crontab /home/pi/crontab_backup_phaseE_${bts}.txt \
      && echo 'restored from crontab_backup_phaseE_${bts}.txt' \
      || echo 'ERROR: backup crontab_backup_phaseE_${bts}.txt NOT FOUND'"

    echo "--- 3. verify ---"
    sshq "$ip" "echo -n 'active @reboot lines: '; crontab -l | grep -c '^@reboot'
      echo -n 'DISABLED markers left: '; crontab -l | grep -c 'DISABLED' || true
      echo 'crontab:'; crontab -l | grep -v '^#'
      echo -n 'halt enabled: '; grep -A2 'power_halt:' /home/pi/BM_Devel_Pi/camera_schedule.yaml | grep -oE 'enabled: (true|false)'
      echo -n 'halt dry_run: '; grep -A3 'power_halt:' /home/pi/BM_Devel_Pi/camera_schedule.yaml | grep -oE 'dry_run: (true|false)'
      echo -n 'txd: '; grep -oE 'image_transmit_delay_seconds: [0-9.]+' /home/pi/BM_Devel_Pi/camera_schedule.yaml
      echo -n 'budget: '; grep -oE 'max_run_time_min: [0-9]+' /home/pi/BM_Devel_Pi/camera_schedule.yaml
      echo -n 'cap: '; grep -oE 'message_cap: [0-9]+' /home/pi/BM_Devel_Pi/camera_schedule.yaml"
    echo "=== done $name ==="
  } 2>&1 | tee "$log"
  echo
done

cat <<'NEXT'
=========================================================================
NEXT AND LAST: the Spotter power schedule (20 min on / 10 min off).
Do this only after the verification above reads correctly for BOTH units.

  bridge cfg set <node_id> s u bridgePowerControllerEnabled 1
  bridge cfg set <node_id> s u sampleIntervalMs 1800000     # 30 min period
  bridge cfg set <node_id> s u sampleDurationMs 1200000     # 20 min on
  bridge cfg set <node_id> s u samplesPerReport 1
  bridge cfg commit <node_id> s
  bridge cfg status <node_id> s        # READ BACK - `bm cfg` silently no-ops

  SPOT-33507C -> c3c564b91856226c   (bmcam003, Unit B, txd 1.0 s)
  SPOT-31593C -> 0e582dd12c1e1480   (bmcam000, Unit A, txd 5.0 s)

The commit re-inits the bridge and blips bus power, which is what starts
the first cycle. From that moment each unit halts itself at cycle end.
=========================================================================
NEXT
