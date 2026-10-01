#!/bin/bash
# pistate.sh TAG [host] — S5 ladder evidence: sha256 of the command state, the config YAML
# and the journal (+ line count), and the overlay keys, appended to ladder.log.
# Read-only on the unit.
TAG="$1"; H="${2:-bmcam003}"
HERE="$(cd "$(dirname "$0")" && pwd)"
{
  echo "----- $(date -u +%FT%TZ) STATE $TAG $H"
  ssh -o BatchMode=yes -o ConnectTimeout=6 pi@$H 'cd /home/pi/BM_Devel_Pi
for f in bm_command_state_v2.json camera_config.yaml config_journal.jsonl; do
  [ -f $f ] && echo "sha256 $(sha256sum $f | cut -c1-16) $f $(wc -l < $f)L" || echo "missing $f"; done
python3 -c "
import json; s = json.load(open(\"bm_command_state_v2.json\"))
print(\"overlay\", json.dumps(s.get(\"overlay\"), sort_keys=True))
print(\"guarded\", json.dumps(s.get(\"guarded\"), sort_keys=True))
print(\"boot\", s.get(\"boot_counter\"), \"hw\", json.dumps(s.get(\"high_water\"), sort_keys=True), \"trg\", json.dumps(s.get(\"pending_trigger_v9\")), \"cache\", len(s.get(\"result_cache\") or {}))
" 2>&1 | cut -c1-300' < /dev/null 2>&1
} | tee -a "$HERE/ladder.log"
