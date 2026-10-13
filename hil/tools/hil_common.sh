#!/bin/bash
# hil_common.sh — sourced (never run) by every hil/tools wrapper. Two jobs:
#
# 1. Configuration without inline env prefixes (Nick's standing allow rules match commands from
#    their start, so calls must be plain `hil/tools/hil_x.sh ...`):
#      - sources hil/hil.env (gitignored; copy of hil.env.example) if present, else hil.env.example
#      - HIL_RUN_DIR: env if set, else the path stored in hil/.current_run (written by
#        hil_new_run.sh / hil_use_run.sh), else "."
# 2. The rig allow-list guard: the ONLY Spotters / devices / hosts a HIL tool may reach are the
#    ones in HIL_RIG_A / HIL_RIG_B ("SPOT=DEVICE=HOST=BRIDGE"). Anything else (e.g. the field
#    Spotter SPOT-33361C, bmcam001/002) exits 5 BEFORE any ssh or API call.
#
#      hil_require_spot SPOT-ID     hil_require_device BMCAM_00x     hil_require_host bmcam00x
#
# 3. The pairing check: HIL_RIG_* must agree with tools/rig_map.json (else exit 5, see below).
# Limits: the guard is as good as hil.env; never add a field unit to HIL_RIG_*. The monitor host
# (HIL_MONITOR) is not a rig and is not guarded here.

HIL_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HIL_DIR="$(dirname "$HIL_TOOLS_DIR")"
if [ -f "$HIL_DIR/hil.env" ]; then . "$HIL_DIR/hil.env"; else . "$HIL_DIR/hil.env.example"; fi
if [ -z "${HIL_RUN_DIR:-}" ]; then
  if [ -s "$HIL_DIR/.current_run" ]; then HIL_RUN_DIR="$(cat "$HIL_DIR/.current_run")"; else HIL_RUN_DIR="."; fi
fi
export HIL_RUN_DIR

# 3. Pairing check (QE #169, after Nick swapped the cameras between the Spotters on Tue 2026-10-13):
#    every HIL_RIG_* SPOT=DEVICE=HOST must agree with tools/rig_map.json (the pool_cmd map), both
#    ways (host -> its Spotter, Spotter -> its host). A stale hil/hil.env would otherwise send a
#    bridge commit to the Spotter of a RUNNING Pi (hard cut). Refuses (exit 5) before any network
#    call. No rig_map.json in this checkout (an older branch) -> nothing to compare.
#    HIL_RIG_MAP overrides the map path (tests).
HIL_RIG_MAP="${HIL_RIG_MAP:-$(dirname "$HIL_DIR")/tools/rig_map.json}"
if [ -f "$HIL_RIG_MAP" ]; then
  _hil_map_bad=$(python3 - "$HIL_RIG_MAP" "${HIL_RIG_A:-}" "${HIL_RIG_B:-}" 2>&1 <<'PY'
import json, sys
raw = json.load(open(sys.argv[1]))
m = {u: (v.get("spot") if isinstance(v, dict) else v) for u, v in raw.items() if not u.startswith("_")}
by_spot = {s: u for u, s in m.items()}
for rig in sys.argv[2:]:
    p = rig.split("=")
    if len(p) < 3:
        continue
    spot, host = p[0], p[2]
    if host in m and m[host] != spot:
        print(f"{host} is on {spot} in hil.env but on {m[host]} in rig_map.json;")
    elif spot in by_spot and by_spot[spot] != host:
        print(f"{spot} carries {host} in hil.env but {by_spot[spot]} in rig_map.json;")
PY
  ) || _hil_map_bad="cannot read $HIL_RIG_MAP (${_hil_map_bad})"
  if [ -n "$_hil_map_bad" ]; then
    echo "[hil-guard] REFUSED: hil/hil.env disagrees with $HIL_RIG_MAP: $(echo $_hil_map_bad) Fix hil/hil.env" \
         "(cameras swapped Tue 2026-10-13: see hil/hil.env.example); nothing sent" >&2
    exit 5
  fi
fi

_hil_rigs() { printf '%s\n' "${HIL_RIG_A:-}" "${HIL_RIG_B:-}" | grep -E '^[^=]+=[^=]+=[^=]+'; }
_hil_refuse() { echo "[hil-guard] REFUSED: $1 is not a bench rig (HIL_RIG_A / HIL_RIG_B); nothing sent" >&2; exit 5; }

hil_require_spot() {
  local s="${1:-}"; [ -n "$s" ] || _hil_refuse "<empty SPOT-ID>"
  _hil_rigs | cut -d= -f1 | grep -qx -- "$s" || _hil_refuse "Spotter '$s'"
}
hil_require_device() {
  local d="${1:-}"; [ -n "$d" ] || _hil_refuse "<empty device>"
  _hil_rigs | cut -d= -f2 | grep -qx -- "$d" || _hil_refuse "device '$d'"
}
hil_require_host() {
  local h="${1:-}"; [ -n "$h" ] || _hil_refuse "<empty host>"
  _hil_rigs | cut -d= -f3 | grep -qx -- "$h" || _hil_refuse "host '$h'"
}
# the Spotter a device is wired to (for wrappers that take both: they must agree)
hil_spot_of_device() { _hil_rigs | awk -F= -v d="$1" '$2 == d {print $1}'; }
