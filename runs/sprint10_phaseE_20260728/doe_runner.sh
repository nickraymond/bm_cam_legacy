#!/usr/bin/env bash
# filename: doe_runner.sh   (RUNS ON THE PI, next to test_queue_drain.py)
# description: Sprint10 Phase E DOE — 2x2 factorial, chunk SIZE x DELAY,
#              at production message count, n=3 replicates.
#
# THE QUESTION (Nick, 2026-07-29)
#   Production ran 100 % at 300 chars / 5.0 s and now runs ~97 % at
#   384 chars / 1.0 s. TWO things changed. Sprint09 concluded size was
#   safe below a ~400 B fast-path cliff — but measured that on TEN-message
#   bursts, and Sprint09 Phase C separately hit 113/113 at 384 B while
#   ~190-message images lose 1-3 %. So the effect is scale-dependent and a
#   10-message result cannot clear 384 B at 200-message scale. Size is
#   therefore a FACTOR here, not an assumption.
#
# DESIGN
#   size  : 300 vs 384 characters
#   delay : 1.0 vs 1.5 s   (1.0 s = 79 % of the measured 1.27 msg/s
#           sustained drain; 1.5 s = 52 %)
#   count : fixed 200 messages (production sweet spot)
#   n = 3 replicates per unit, both units in parallel => n=6 pooled.
#
# WHY THE RUN TAG ENCODES SIZE AND REPLICATE
#   test_queue_drain.py builds burst_id as <run>C<count>D<delay_ms> — it
#   contains NEITHER size NOR replicate. Two sizes, or three replicates,
#   under one run tag would produce identical burst_ids: the sendlog file
#   would be overwritten AND backend arrivals would share one seq space,
#   so a seq lost in one burst but delivered in another scores as
#   DELIVERED and loss is silently undercounted. Tags are therefore
#   <unit>S<size>R<rep>, e.g. D3S384R2 -> burst id D3S384R2C200D1000.
#
# COUNTERBALANCING
#   Size order alternates per replicate and delay order alternates within
#   each run, so neither factor is confounded with position in the session
#   (Notecard fill, signal drift, thermal).
#
# INPUT   $1 = unit tag: D3 (bmcam003) or D0 (bmcam000)
# OUTPUT  /home/pi/phaseE/sendlog_<TAG>S<size>R<n>C200D<delay>.jsonl
#         /home/pi/phaseE/manifest_<TAG>S<size>R<n>.json
# TIMING  12 bursts, 2400 messages, ~1 h 45 min per unit.
set -u
TAG="${1:?unit tag, e.g. D3}"
cd /home/pi/BM_Devel_Pi || exit 1
OUT=/home/pi/phaseE
mkdir -p "$OUT"

run() {  # run <tag> <size> <matrix>
  echo "### $(date -u +%FT%TZ) START $1 size=$2 matrix=$3"
  python3 -u test_queue_drain.py --run "$1" --size "$2" --matrix "$3" \
          --drain-s 300 --out-dir "$OUT"
  echo "### $(date -u +%FT%TZ) END   $1"
}

echo "=== Phase E size x delay DOE start $(date -u +%FT%TZ) unit=$TAG ==="

# replicate 1 — 384 first, delays 1.0 then 1.5
run "${TAG}S384R1" 384 '200@1000,200@1500'
echo "--- drain 300s ---"; sleep 300
run "${TAG}S300R1" 300 '200@1500,200@1000'
echo "--- drain 300s ---"; sleep 300

# replicate 2 — 300 first, delay order flipped
run "${TAG}S300R2" 300 '200@1000,200@1500'
echo "--- drain 300s ---"; sleep 300
run "${TAG}S384R2" 384 '200@1500,200@1000'
echo "--- drain 300s ---"; sleep 300

# replicate 3 — 384 first again, delays 1.5 then 1.0
run "${TAG}S384R3" 384 '200@1500,200@1000'
echo "--- drain 300s ---"; sleep 300
run "${TAG}S300R3" 300 '200@1000,200@1500'

echo "=== DOE COMPLETE $(date -u +%FT%TZ) unit=$TAG ==="
