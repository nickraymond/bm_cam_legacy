#!/bin/bash
# pool_sha_check.sh — do the pool-day files deployed on both Pis and nereus000 match a git ref?
#
# Purpose:  runbook §0B (QE #155, 2026-10-12): a stale copy (e.g. a T-DRY pool_mode.sh) would fake
#           "PRODUCTION MODE" at the restore or run old pool code. STOP if anything differs.
# Checks:   units   /home/pi/pool_mode.sh                 = tools/pool_mode.sh
#                   /home/pi/BM_Devel_Pi/pool_sequence.py = BM_Devel_Pi/pool_sequence.py
#                   /home/pi/BM_Devel_Pi/rc_preview.py    = BM_Devel_Pi/rc_preview.py
#           monitor /home/pi/pool_tools/pool_cmd.py       = tools/pool_cmd.py
#                   /home/pi/pool_tools/pool_preview.py   = tools/pool_preview.py
#           Expected sha256 = `git show <ref>:<path>` from this checkout (after `git fetch origin`
#           when the ref is origin/...). ONE ssh per host (read-only: sha256sum).
# Inputs:   --ref REF (default origin/development), --unit pi@HOST (repeatable; default pi@bmcam003
#           pi@bmcam004), --monitor pi@HOST (default pi@192.168.1.45)
# Outputs:  one line per file: MATCH | DIFF (deployed vs ref sha, 12 hex) | MISSING | UNREACHABLE;
#           a final "ALL MATCH <ref> <commit>" or "STOP: n mismatch(es)". Exit 0 only if all match.
# Example:  tools/pool_sha_check.sh
#           tools/pool_sha_check.sh --ref origin/development --monitor pi@172.20.10.3
# Limits:   compares files only (not the rest of BM_Devel_Pi, not running processes: a process
#           started before a redeploy still runs the old code; restart it after a redeploy).
# Test hook: POOL_PI_HOME replaces /home/pi in the remote paths.
set -u
REF="origin/development"; MON="pi@192.168.1.45"; UNITS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?--ref REF}"; shift 2 ;;
    --unit) UNITS+=("${2:?--unit pi@HOST}"); shift 2 ;;
    --monitor) MON="${2:?--monitor pi@HOST}"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "[sha] unknown argument $1" >&2; exit 2 ;;
  esac
done
[ ${#UNITS[@]} -gt 0 ] || UNITS=(pi@bmcam003 pi@bmcam004)
H="${POOL_PI_HOME:-/home/pi}"
UNIT_FILES="$H/pool_mode.sh=tools/pool_mode.sh $H/BM_Devel_Pi/pool_sequence.py=BM_Devel_Pi/pool_sequence.py $H/BM_Devel_Pi/rc_preview.py=BM_Devel_Pi/rc_preview.py"
MON_FILES="$H/pool_tools/pool_cmd.py=tools/pool_cmd.py $H/pool_tools/pool_preview.py=tools/pool_preview.py"

case "$REF" in origin/*) git fetch -q origin || { echo "[sha] git fetch failed" >&2; exit 2; } ;; esac
COMMIT=$(git rev-parse --short "$REF" 2>/dev/null) || { echo "[sha] unknown ref $REF" >&2; exit 2; }
echo "[sha] reference $REF = $COMMIT"
BAD=0

check_host() {   # $1 ssh target, $2 "remote=repo ..." pairs
  local host="$1" pairs="$2" remote_list="" p out
  for p in $pairs; do remote_list="$remote_list ${p%%=*}"; done
  # remote: one line per file "<sha> <path>" or "MISSING <path>" (sha256sum on the Pi; shasum fallback)
  out=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" "for f in$remote_list; do if [ -f \"\$f\" ]; then s=\$( (sha256sum \"\$f\" 2>/dev/null || shasum -a 256 \"\$f\") | cut -c1-64); echo \"\$s \$f\"; else echo \"MISSING \$f\"; fi; done" < /dev/null 2>&1)
  if [ $? -ne 0 ]; then echo "UNREACHABLE $host: $(echo "$out" | tail -1)"; BAD=$((BAD + 1)); return; fi
  for p in $pairs; do
    local rpath="${p%%=*}" repo="${p#*=}" exp got
    exp=$(git show "$REF:$repo" 2>/dev/null | shasum -a 256 | cut -c1-64)
    got=$(echo "$out" | awk -v f="$rpath" '$2 == f {print $1}' | head -1)
    if [ "$got" = "MISSING" ] || [ -z "$got" ]; then echo "MISSING  $host $rpath"; BAD=$((BAD + 1))
    elif [ "$got" = "$exp" ]; then echo "MATCH    $host $rpath ${got:0:12}"
    else echo "DIFF     $host $rpath deployed=${got:0:12} $REF=${exp:0:12}  (redeploy from $REF)"; BAD=$((BAD + 1)); fi
  done
}

for u in "${UNITS[@]}"; do check_host "$u" "$UNIT_FILES"; done
check_host "$MON" "$MON_FILES"
if [ $BAD -eq 0 ]; then echo "ALL MATCH $REF $COMMIT"; exit 0; fi
echo "STOP: $BAD mismatch(es) vs $REF $COMMIT: redeploy those files (and restart what runs them) before going on"
exit 1
