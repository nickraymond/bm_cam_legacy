#!/usr/bin/env bash
# filename: set_spotter_schedules.sh
# description: Sprint11 — set each Spotter's bus-power schedule, then READ IT BACK.
#
# THE LAST STEP. Only run after both Pis are configured, verified and armed:
# the `bridge cfg commit` re-inits the bridge and blips bus power, which is
# what starts the first cycle of the run.
#
# SCHEDULES (SPEC §4 + DESIGN D7)
#   Unit A  bmcam003 / SPOT-33507C / bridge c3c564b91856226c
#           15 min on / 15 min off  (period 30 min, duration 15 min)
#   Unit B  bmcam000 / SPOT-31593C / bridge 0e582dd12c1e1480
#           20 min on / 10 min off  (period 30 min, duration 20 min)
#
# WHY THE WINDOW IS THE ENERGY LEVER (D7): the bus stays powered for the whole
# window regardless of when the Pi halts, so the halted-Pi baseline (0.424 W)
# is 79 % of a fast cycle's energy. Measured on twelve real on-windows,
# 20 -> 15 min saves 19.7 % — more than the entire 5.0 -> 1.0 s pacing change
# (20.4 %) and, unlike pacing, it costs no delivery at all.
#
# TWO HARD-WON RULES
#   1. `bm cfg ...` FAILS SILENTLY. Use `bridge cfg set <node_id> s u ...`
#      followed by `bridge cfg commit <node_id> s`.
#   2. ALWAYS read back with `bridge cfg status`. A write you did not verify
#      is a write that did not happen.
#
# Commands go out through the spotter_serial_monitor's cmd.txt FIFO, the same
# path tools/overnight_ab_runner.py uses.
#
# INPUT   $1 log-root (default ~/spotter_logs)
# OUTPUT  <rundir>/spotter_schedule_<TS>.log ; verify by reading the console
set -uo pipefail

LOG_ROOT="${1:-$HOME/spotter_logs}"
RUNDIR="${2:-runs/sprint11_20260729}"
mkdir -p "$RUNDIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$RUNDIR/spotter_schedule_${TS}.log"

# spot:bridge_node:duration_ms:label
UNITS=(
  "SPOT-33507C:c3c564b91856226c:900000:bmcam003 Unit A 15on/15off"
  "SPOT-31593C:0e582dd12c1e1480:1200000:bmcam000 Unit B 20on/10off"
)
PERIOD_MS=1800000     # 30 min, both units, so both align to :00 and :30

send() {  # send() <spot> <command line>
  echo "  -> $2"
  printf '%s\n' "$2" > "$LOG_ROOT/$1/cmd.txt"
  sleep 2
}

{
  echo "=== Sprint11 Spotter power schedules $(date -u +%FT%TZ) ==="
  for entry in "${UNITS[@]}"; do
    IFS=: read -r spot node dur label <<< "$entry"
    echo "--- $spot ($label) ---"
    send "$spot" "bridge cfg set $node s u bridgePowerControllerEnabled 1"
    send "$spot" "bridge cfg set $node s u sampleIntervalMs $PERIOD_MS"
    send "$spot" "bridge cfg set $node s u sampleDurationMs $dur"
    send "$spot" "bridge cfg set $node s u samplesPerReport 1"
    send "$spot" "bridge cfg commit $node s"
    sleep 3
    echo "  -> reading back"
    send "$spot" "bridge cfg status $node s"
    sleep 3
  done
  echo "=== sent; now VERIFY on the consoles ==="
} 2>&1 | tee "$LOG"

cat <<NEXT

=========================================================================
READ BACK — do not skip. Check each console for the committed values:

  tail -80 $LOG_ROOT/SPOT-33507C/console_\$(date -u +%Y%m%d).log | grep -iE 'sampleInterval|sampleDuration|samplesPerReport|bridgePower'
  tail -80 $LOG_ROOT/SPOT-31593C/console_\$(date -u +%Y%m%d).log | grep -iE 'sampleInterval|sampleDuration|samplesPerReport|bridgePower'

EXPECT  SPOT-33507C: sampleIntervalMs 1800000, sampleDurationMs 900000
        SPOT-31593C: sampleIntervalMs 1800000, sampleDurationMs 1200000

Then start the sweep runner:
  python3 tools/overnight_ab_runner.py --out $RUNDIR/timeline.jsonl --until-utc HH:MM
=========================================================================
NEXT
