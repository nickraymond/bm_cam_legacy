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
# Limits: the guard is as good as hil.env; never add a field unit to HIL_RIG_*. The monitor host
# (HIL_MONITOR) is not a rig and is not guarded here.

HIL_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HIL_DIR="$(dirname "$HIL_TOOLS_DIR")"
if [ -f "$HIL_DIR/hil.env" ]; then . "$HIL_DIR/hil.env"; else . "$HIL_DIR/hil.env.example"; fi
if [ -z "${HIL_RUN_DIR:-}" ]; then
  if [ -s "$HIL_DIR/.current_run" ]; then HIL_RUN_DIR="$(cat "$HIL_DIR/.current_run")"; else HIL_RUN_DIR="."; fi
fi
export HIL_RUN_DIR

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
