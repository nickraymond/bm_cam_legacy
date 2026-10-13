#!/bin/bash
# test_hil_guards.sh — prove the rig allow-list guard (hil_common.sh) in every wrapper that reaches a
# Spotter, a device or a unit: the field Spotter SPOT-33361C, a field device and a field host must be
# refused (exit 5) BEFORE any network call.
#
# How: ssh / scp / curl are replaced on PATH by a shim that records every call and exits 99, so a
# refused call leaves the record empty. A positive control (a real rig) must reach the shim, which
# proves the test is not vacuous. Runs offline, sends nothing.
# Inputs:   none (uses hil/hil.env or hil.env.example for the rig list)
# Outputs:  one PASS/FAIL line per case; exit 0 only if every case passes.
# Example:  hil/tools/test_hil_guards.sh
set -u
T="$(cd "$(dirname "$0")" && pwd)"
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
mkdir -p "$W/bin" "$W/run"
for c in ssh scp curl; do printf '#!/bin/sh\necho "%s $*" >> "%s/calls"\nexit 99\n' "$c" "$W" > "$W/bin/$c"; chmod +x "$W/bin/$c"; done
export PATH="$W/bin:$PATH" HIL_RUN_DIR="$W/run"
# The env in use (hil/hil.env, else the example) and two rig maps built FROM it: a consistent one for
# every case below (so the suite tests the guards in any checkout) and a swapped one for the
# pairing-check case (hil_common.sh part 3; cameras swapped Tue 2026-10-13).
ENVF="$T/../hil.env"; [ -f "$ENVF" ] || ENVF="$T/../hil.env.example"
RIG_A=$(sed -n 's/^export HIL_RIG_A="\(.*\)"/\1/p' "$ENVF"); RIG_B=$(sed -n 's/^export HIL_RIG_B="\(.*\)"/\1/p' "$ENVF")
python3 - "$RIG_A" "$RIG_B" "$W" <<'PY'
import json, sys
a, b, w = sys.argv[1].split("="), sys.argv[2].split("="), sys.argv[3]
json.dump({a[2]: a[0], b[2]: b[0]}, open(f"{w}/rig_map_consistent.json", "w"))
json.dump({a[2]: b[0], b[2]: a[0]}, open(f"{w}/rig_map_swapped.json", "w"))
PY
export HIL_RIG_MAP="$W/rig_map_consistent.json"
fails=0
expect() {  # $1 name, $2 expected exit, $3 want_calls (0|1), rest = command
  local name="$1" want="$2" wantcalls="$3"; shift 3
  : > "$W/calls"
  "$@" > "$W/out" 2>&1; local rc=$?
  local n; n=$(grep -c . "$W/calls")
  local ok=1
  [ "$rc" = "$want" ] || ok=0
  if [ "$wantcalls" = 0 ]; then [ "$n" = 0 ] || ok=0; else [ "$n" -gt 0 ] || ok=0; fi
  if [ $ok = 1 ]; then echo "PASS $name (exit $rc, network calls $n)"
  else echo "FAIL $name (exit $rc want $want, network calls $n) :: $(head -c 200 "$W/out")"; fails=$((fails+1)); fi
}
F=SPOT-33361C
expect "console refuses $F"        5 0 "$T/hil_console.sh" $F post 1
expect "cmd refuses $F"            5 0 "$T/hil_cmd.sh" X '{"id":1,"c":"ping"}' 1 $F
expect "step refuses $F"           5 0 "$T/hil_step.sh" X bmcam004 $F - -
expect "step refuses host bmcam001" 5 0 "$T/hil_step.sh" X bmcam001 SPOT-31593C - -
expect "change refuses $F"         5 0 "$T/hil_change.sh" X BMCAM_004 $F '{"set":{}}'
expect "change refuses BMCAM_001"  5 0 "$T/hil_change.sh" X BMCAM_001 SPOT-31593C '{"set":{}}'
# rig mismatch = rig A's device on rig B's Spotter, read from the env in use (pairing-agnostic:
# Nick swapped the cameras between the Spotters on Tue 2026-10-13)
expect "change refuses rig mismatch" 5 0 "$T/hil_change.sh" X "$(echo "$RIG_A" | cut -d= -f2)" "$(echo "$RIG_B" | cut -d= -f1)" '{"set":{}}'
expect "sofar refuses BMCAM_001"   5 0 "$T/hil_sofar_change.sh" X BMCAM_001 '{"set":{}}'
expect "sofar refuses empty"       1 0 "$T/hil_sofar_change.sh" X "" '{"set":{}}'
expect "pistate refuses bmcam001"  5 0 "$T/hil_pistate.sh" X bmcam001
expect "lowgain refuses bmcam001"  5 0 "$T/hil_s28_lowgain_sunrise.sh" bmcam001 "$W/run" run
expect "lowgain refuses empty"     1 0 "$T/hil_s28_lowgain_sunrise.sh" "" "$W/run" run
expect "snapshot refuses bmcam002" 5 0 "$T/hil_unit_snapshot.sh" bmcam002 x
expect "ip probe refuses bmcam002"  5 0 "$T/hil_ip_range_probe.sh" bmcam002 "$W/ipp"
expect "deploy refuses bmcam001"   5 0 "$T/hil_deploy_unit.sh" bmcam001 x
expect "refresh refuses SPOT-33361C" 5 0 "$T/hil_refresh.sh" BMCAM_003 SPOT-33361C
expect "restore_schedule refuses SPOT-33361C" 5 0 "$T/hil_restore_schedule.sh" SPOT-33361C
expect "bridge_phase refuses SPOT-33361C" 5 0 "$T/hil_bridge_phase.sh" SPOT-33361C utc
expect "bus_always_on refuses SPOT-33361C" 5 0 "$T/hil_bus_always_on.sh" SPOT-33361C
expect "wake_report refuses SPOT-33361C" 5 0 "$T/hil_wake_report.sh" SPOT-33361C 2026-10-02T22:00
expect "p0 refuses bmcam001"       5 0 "$T/hil_p0_probe.sh" bmcam001 "$W/p0"
expect "console refuses a stale pairing (hil.env vs rig_map.json)" 5 0 \
       env HIL_RIG_MAP="$W/rig_map_swapped.json" "$T/hil_console.sh" "$(echo "$RIG_A" | cut -d= -f1)" post 1
expect "bus_always_on refuses a stale pairing" 5 0 \
       env HIL_RIG_MAP="$W/rig_map_swapped.json" "$T/hil_bus_always_on.sh" "$(echo "$RIG_A" | cut -d= -f1)"
# positive controls: a real rig passes the guard and reaches the (fake) network
expect "control: console rig SPOT-31593C reaches ssh" 4 1 "$T/hil_console.sh" SPOT-31593C post 1
expect "control: sofar rig BMCAM_004 reaches ssh"     3 1 "$T/hil_sofar_change.sh" X BMCAM_004 '{"set":{}}'
echo "guard tests: $fails failure(s)"
exit $((fails > 0))
