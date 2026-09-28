#!/bin/bash
# S4a overnight soak: delivery at the backend for both rigs (staging, read via nereus000
# so the admin token never leaves it). One line per media: capture ts, received/expected.
# Usage: ./delivery.sh [N=8]
N=${1:-8}
ssh -o ConnectTimeout=8 -o BatchMode=yes pi@192.168.1.45 "set -a; . ~/.config/nereus/heal_driver.env; set +a
for d in BMCAM_003 BMCAM_004; do
  curl -s -m 40 -H \"Authorization: Bearer \$ADMIN_TOKEN\" \"https://nereus-vision-staging.onrender.com/devices/\$d/media?page_size=$N\" |
  python3 -c '
import json, sys
d = sys.argv[1]
rows = json.load(sys.stdin)
for m in rows:
    e, r = m.get(\"expected_chunks\"), m.get(\"received_chunks\")
    flag = \"COMPLETE\" if e and e == r else \"PARTIAL\"
    print(d, m[\"timestamp_utc\"][:19], \"recv\", (m.get(\"received_at_utc\") or \"\")[:19], f\"{r}/{e}\", flag)
' \$d
done" < /dev/null
