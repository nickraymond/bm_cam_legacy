#!/usr/bin/env bash
# filename: poll_manual_cycle.sh
# description: Poll Sofar for the manual-cycle image until complete or deadline.
# Curl for TLS (macOS framework Python has no CA store), cci.analyze for the
# per-image verdict. Emits one status line per poll; exits 0 on COMPLETE,
# 2 on deadline (partial/missing).
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPOT="${1:?SPOT id}"; FILE="${2:?filename fragment}"
START="${3:?start iso}"; END_BY="${4:-3600}"   # seconds to keep polling
S="${TMPDIR:-/tmp}"
deadline=$(( $(date +%s) + END_BY ))
while :; do
  end_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  curl -s "https://api.sofarocean.com/api/sensor-data?spotterId=$SPOT&startDate=$START&endDate=$end_iso&token=$SOFAR_API_TOKEN_BM_REEF&limit=5000" \
    -o "$S/poll_$SPOT.json" || true
  python3 - "$S/poll_$SPOT.json" "$FILE" <<'PY'
import json, sys
sys.path.insert(0, "tools"); sys.path.insert(0, "BM_Devel_Pi")
import count_complete_images as cci
try:
    entries = json.load(open(sys.argv[1]))["data"]
except Exception as exc:
    print(f"POLL fetch-error: {exc}"); sys.exit(1)
images, _ = cci.analyze(entries)
img = next((i for i in images if sys.argv[2] in i.filename), None)
if img is None:
    print(f"POLL no-START-yet ({len(entries)} rows in window)"); sys.exit(1)
r = img.report()
print(f"POLL {r['filename']}: recv={r['received']}/{r['planned']} "
      f"end={r['end_seen']} gap@{r['first_gap_index']} "
      f"complete={r['complete']}")
sys.exit(0 if r["complete"] else 1)
PY
  rc=$?
  [ $rc -eq 0 ] && { echo "VERDICT: COMPLETE at $(date -u +%FT%TZ)"; exit 0; }
  [ "$(date +%s)" -ge "$deadline" ] && { echo "VERDICT: DEADLINE — not complete"; exit 2; }
  sleep 300
done
