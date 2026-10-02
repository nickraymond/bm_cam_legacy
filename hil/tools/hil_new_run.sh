#!/bin/bash
# hil_new_run.sh — create a HIL run folder (hil/README.md §3) with a started run_manifest.json.
#
# Inputs:   $1 run name (e.g. g3_hardmode), $2 gate/test id (e.g. G3), $3.. units (hosts)
#           env HIL_RUNS (default runs), HIL_OPERATOR (session name, default "Test Engineer")
# Outputs:  hil/.current_run (the run the other wrappers log to); runs/<name>_<YYYYMMDD PDT>/ with RESULTS.md (from the template), run_manifest.json,
#           commands.log, gate.log, snapshots/ console/ api/ pulled/ analysis/. Prints the path.
#           Refuses to overwrite an existing RESULTS.md / run_manifest.json.
# Example:  hil/tools/hil_new_run.sh g3_hardmode G3 bmcam003 bmcam004
# Limits:   the date is the PDT date (the gates are scheduled in PDT). Run from the repo root.
set -eu
NAME="${1:?run name}"; GATE="${2:?gate id}"; shift 2
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
HERE="$(cd "$(dirname "$0")" && pwd)"; HIL="$(dirname "$HERE")"
D="${HIL_RUNS:-runs}/${NAME}_$(TZ=America/Los_Angeles date +%Y%m%d)"
mkdir -p "$D"/{snapshots,console,api,pulled,analysis}
touch "$D/commands.log" "$D/gate.log"
[ -e "$D/RESULTS.md" ] || sed -e "s/{{GATE}}/$GATE/g" -e "s#{{RUN}}#$D#g" "$HIL/templates/RESULTS.md" > "$D/RESULTS.md"
if [ ! -e "$D/run_manifest.json" ]; then
  python3 - "$D" "$GATE" "$@" <<'PY'
import json, os, subprocess, sys, datetime, hashlib, glob
d, gate, units = sys.argv[1], sys.argv[2], sys.argv[3:]
def git(*a):
    try: return subprocess.check_output(["git", *a], text=True).strip()
    except Exception: return None
now = datetime.datetime.now(datetime.timezone.utc)
tools = {}
for p in sorted(glob.glob("hil/tools/*")):
    tools[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
m = {
  "schema": "hil_run_manifest/1",
  "gate": gate, "run_dir": d,
  "start_utc": now.isoformat(timespec="seconds"),
  "start_pdt": now.astimezone(datetime.timezone(datetime.timedelta(hours=-7))).isoformat(timespec="seconds"),
  "end_utc": None, "end_pdt": None,
  "operator": os.environ.get("HIL_OPERATOR", "Test Engineer"),
  "repo": {"branch": git("rev-parse", "--abbrev-ref", "HEAD"), "sha": git("rev-parse", "HEAD"),
           "dirty": bool(git("status", "--porcelain"))},
  "units": [{"host": u, "before": {"runtime_sha": None, "cfg_hash": None, "cron_armed": None},
             "after": {"runtime_sha": None, "cfg_hash": None, "cron_armed": None}} for u in units],
  "env": {k: v for k, v in os.environ.items() if k.startswith("HIL_") and "TOKEN" not in k},
  "tools_sha256_16": tools,
  "media": [], "verdict": None, "notes": [],
}
json.dump(m, open(os.path.join(d, "run_manifest.json"), "w"), indent=2)
PY
fi
echo "$(date -u +%FT%TZ) run created for $GATE units: $*" >> "$D/gate.log"
echo "$D" > "$HIL_DIR/.current_run"   # later wrappers log here (hil_use_run.sh switches)
echo "$D"
