#!/usr/bin/env bash
# filename: restore_field_normal.sh
# description: Sprint10 Phase E teardown — put both bmcam units back to
#              field-normal exactly per PHASE_E.md §6, and prove it.
#
# WHY
#   Phase E leaves both units DISARMED (no @reboot cron, power_halt off) and
#   both Spotters on continuous bus power. That combination is safe for the
#   run and wrong for the field: the units would never sleep and never image
#   on their own. This script reverses every change in the reverse order it
#   was made, and — the part that matters — VERIFIES each reversal instead of
#   assuming the command took.
#
# ORDER (deliberate, mirrors the disarm)
#   1. remove the Phase E harness from the runtime dir (keep deploys clean)
#   2. power_halt back ON in camera_schedule.yaml
#   3. re-arm cron from the PRE-disarm backup captured by catch_awake_disarm.sh
#   4. LAST: Spotter power schedule back to 20-on/40-off + LED
#      Power goes last on purpose: once the controller is cycling again the
#      unit will halt itself at the end of its next cycle, so everything
#      Pi-side must already be correct before that happens.
#
# INPUTS   $1 run dir holding disarm_state.json (default runs/sprint10_phaseE_20260728)
#          --no-spotter   skip step 4 (do the Spotter side by hand/console)
# OUTPUTS  <outdir>/restore_<host>_<TS>.log, <outdir>/restore_verify.json
#
# EXAMPLE  ./restore_field_normal.sh runs/sprint10_phaseE_20260728
#
# LIMITATIONS
#   - Step 4 is emitted as console lines for the operator/monitor cmd.txt
#     path; this script does not open the Spotter serial port itself.
#   - Does not run a capture cycle; imaging verification is a separate,
#     deliberate step (you want to watch that one).
set -uo pipefail

OUTDIR="${1:-runs/sprint10_phaseE_20260728}"
HOSTS=("100.103.35.24:bmcam003" "100.119.14.92:bmcam000")
VERIFY="$OUTDIR/restore_verify.json"

sshq() {
  ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no \
      "pi@$1" "$2" 2>&1 | grep -viE "tailscale|authenticate"
}

restore_one() {
  local ip="$1" name="$2" ts log backup
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  log="$OUTDIR/restore_${name}_${ts}.log"
  # The re-arm source is whatever catch_awake_disarm.sh recorded for this unit.
  backup=$(cat "$OUTDIR/.disarmed_${name}" 2>/dev/null)
  {
    echo "=== $name ($ip) restore at $(date -u +%FT%TZ) ==="
    if [ -z "$backup" ]; then
      echo "!! no disarm marker for $name — falling back to newest backup on the Pi"
      backup=$(sshq "$ip" "ls -1t /home/pi/crontab_backup_phaseE_*.txt 2>/dev/null | head -1" \
               | sed 's|.*crontab_backup_phaseE_||; s|\.txt||')
      echo "   using ts=$backup"
    fi

    echo "--- step 1: remove Phase E harness from runtime ---"
    sshq "$ip" "rm -f /home/pi/BM_Devel_Pi/test_queue_drain.py; \
      ls /home/pi/BM_Devel_Pi/test_queue_drain.py 2>/dev/null || echo 'harness removed'"

    echo "--- step 2: power_halt back ON ---"
    sshq "$ip" "python3 - <<'EOF'
import re
p='/home/pi/BM_Devel_Pi/camera_schedule.yaml'
s=open(p).read()
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*enabled:) false', r'\1 true', s, count=1)
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*dry_run:) true', r'\1 false', s, count=1)
open(p,'w').write(s)
i=s.find('power_halt:')
print('VERIFY power_halt block:'); print(s[i:i+220])
EOF"

    echo "--- step 3: re-arm cron from PRE-disarm backup ---"
    sshq "$ip" "test -f /home/pi/crontab_backup_phaseE_${backup}.txt && \
      crontab /home/pi/crontab_backup_phaseE_${backup}.txt && \
      echo 'VERIFY crontab now:' && crontab -l | grep -c '^@reboot' && crontab -l"

    echo "--- verify: no DISABLED marker should remain, @reboot must be live ---"
    sshq "$ip" "crontab -l | grep -c 'DISABLED phaseE' || true"
    echo "=== done $name ==="
  } 2>&1 | tee "$log"
}

echo "[restore] $(date -u +%FT%TZ) starting field-normal restore"
for entry in "${HOSTS[@]}"; do
  restore_one "${entry%%:*}" "${entry##*:}"
done

cat <<'SPOTTER'

=========================================================================
STEP 4 (Spotter side) — run these on EACH Spotter console, one line at a
time. `bm cfg commit` REBOOTS the Spotter and blips bus power, so the Pi
hard power-cycles: only do this once steps 1-3 above verified clean.

  bm cfg set 0 s u bridgePowerControllerEnabled 1
  bm cfg set 0 s u sampleIntervalMs 3600000
  bm cfg set 0 s u sampleDurationMs 1200000
  bm cfg set 0 s u samplesPerReport 1
  bm cfg commit 0 s

Plus, outstanding from the 07-27/28 soak:
  SPOT-33507C only:  cfg vle 1     (visibility LED, disabled for bench)
                     cfg save
  bmcam003:          message_cap back to 195 if the A/B value (100) is
                     still in camera_schedule.yaml

Injection path with the monitor running:
  echo "bm cfg set 0 s u bridgePowerControllerEnabled 1" > ~/spotter_logs/SPOT-33507C/cmd.txt
=========================================================================
SPOTTER

{
  echo "{\"restored_utc\": \"$(date -u +%FT%TZ)\", \"logs\": \"$OUTDIR\","
  echo " \"note\": \"step 4 (Spotter power schedule + LED) is operator-driven; see stdout\"}"
} > "$VERIFY"
echo "[restore] wrote $VERIFY"
