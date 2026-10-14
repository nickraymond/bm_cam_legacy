#!/bin/bash
# Visibility LED OFF on both bench Spotters (Nick OK 2026-10-13 ~7:30 PM, TE2 chat). Spotter CLI per Sprint10 DEV_LOG:
#   cfg vle 0 + cfg save (persists through reboot; cfg save does not reboot, unlike bm cfg commit); read-back cfg vle -> 0.
# REVERT (before any field / at-sea deployment): cfg vle 1 + cfg save on each Spotter.
# Waits (read-only) until both consoles are attached on nereus000, then per Spotter: read, set, save, read back, post.
set -u
cd "$(dirname "$0")"; RUN=$PWD; cd ../..; source hil/hil.env
for i in $(seq 1 170); do n=$(ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'ls /dev/ttyACM* 2>/dev/null | wc -l' </dev/null 2>/dev/null); [ "${n:-0}" -ge 2 ] && break; sleep 30; done
[ "${n:-0}" -ge 2 ] || { echo "consoles not attached within the wait; nothing sent"; exit 3; }
echo "$(date -u +%FT%TZ) consoles attached ($n)"; sleep 10
for S in SPOT-33507C SPOT-31593C; do
  echo "===== $S"
  for c in "cfg vle" "cfg vle 0" "cfg save" "cfg vle"; do echo "-- > $c"; HIL_RUN_DIR=$RUN hil/tools/hil_console.sh $S "$c" 4 2>&1 | grep -viE ", power \||^\s*$" | grep -iE "vle|save|ok|err|value|unknown|invalid" | head -4 | cut -c1-160; done
  HIL_RUN_DIR=$RUN hil/tools/hil_console.sh $S "post" 10 2>&1 | grep -i "visibilityLED" | cut -c1-120
done
