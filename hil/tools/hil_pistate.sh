#!/bin/bash
# hil_pistate.sh — sha256 of the unit's command state, config YAML and journal (+ line
# count), and the overlay / guarded / high-water keys, appended to the run's steps.log.
#
# Purpose:  cheap before/after evidence around each command step ("nothing changed" is a hash).
# Origin:   adapted copy of runs/s5_console_20260928/pistate.sh (original left in place).
# Inputs:   $1 TAG, $2 host (default $HIL_HOST); env HIL_RUN_DIR, HIL_UNIT_USER, HIL_UNIT_APP
# Outputs:  stdout + $HIL_RUN_DIR/steps.log
# Example:  hil/tools/hil_pistate.sh N1.before bmcam003
# Limits:   read-only; note the result_cache changes the state file sha on every command by design.
set -u
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
TAG="${1:?TAG}"; H="${2:-${HIL_HOST:?host or HIL_HOST}}"
hil_require_host "$H"
U="${HIL_UNIT_USER:-pi}"; APP="${HIL_UNIT_APP:-/home/pi/BM_Devel_Pi}"
RUN="${HIL_RUN_DIR:-.}"; mkdir -p "$RUN"
{
  echo "----- $(date -u +%FT%TZ) STATE $TAG $H"
  ssh -o BatchMode=yes -o ConnectTimeout=6 "$U@$H" "cd $APP
for f in bm_command_state_v2.json camera_config.yaml config_journal.jsonl; do
  [ -f \$f ] && echo \"sha256 \$(sha256sum \$f | cut -c1-16) \$f \$(wc -l < \$f)L\" || echo \"missing \$f\"; done
python3 -c \"
import json; s = json.load(open('bm_command_state_v2.json'))
print('overlay', json.dumps(s.get('overlay'), sort_keys=True))
print('guarded', json.dumps(s.get('guarded'), sort_keys=True))
print('boot', s.get('boot_counter'), 'hw', json.dumps(s.get('high_water'), sort_keys=True), 'trg', json.dumps(s.get('pending_trigger_v9')), 'cache', len(s.get('result_cache') or {}))
\" 2>&1 | cut -c1-300" < /dev/null 2>&1
} | tee -a "$RUN/steps.log"
