#!/bin/bash
# hil_use_run.sh — point every hil/tools wrapper at an existing run folder (writes hil/.current_run),
# so calls stay plain `hil/tools/hil_x.sh ...` (no HIL_RUN_DIR=... prefix).
# Inputs:   $1 run folder (e.g. runs/g3_hardmode_20261001); must exist
# Outputs:  hil/.current_run (gitignored); prints the folder
# Example:  hil/tools/hil_use_run.sh runs/g3_hardmode_20261001
set -eu
D="${1:?run folder}"; [ -d "$D" ] || { echo "[hil_use_run] no such folder: $D" >&2; exit 2; }
HIL_DIR="$(dirname "$(cd "$(dirname "$0")" && pwd)")"
echo "$D" > "$HIL_DIR/.current_run"; echo "$D"
