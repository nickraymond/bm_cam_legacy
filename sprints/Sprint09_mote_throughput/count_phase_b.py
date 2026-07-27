#!/usr/bin/env python3
# filename: count_phase_b.py
# description: Sprint09 Phase B — count delivered TST messages per run-id via Sofar sensor-data.
"""
Counts Phase B test messages that reached the Sofar backend, per burst id.

Uses the PROVEN sensor-data path from the nereus backend (read-only reference:
nereus-vision-dev/backend/app/services/sofar_client.py). Per Nick 2026-07-26,
sensor-data is the verified endpoint (forum t/575, Zac's reply) — not
api/raw-messages as the earlier EA-header plan assumed.

Query:  GET https://api.sofarocean.com/api/sensor-data
            ?spotterId=<id>&startDate=<iso>&endDate=<iso>&token=<token>
Payload: payload["data"] = list of entries; each entry:
            value                 hex-encoded message bytes -> ASCII
            timestamp             ISO8601
            bristlemouth_node_id  bridging mote id (bench: 53171fa3d81a8e6f)
Decode: bytes.fromhex(entry["value"]).decode("utf-8") -> "TST,<burst>,<seq>,<pad>*<crc8hex>"

Inputs:
  SOFAR_API_TOKEN_BM_REEF   env var with the API token (never passed on CLI)
  --spotter-id              e.g. SPOT-33507C
  --run-id                  base run id; B2 sweep bursts are <run>G<gap>
  --start / --end           UTC ISO window (default: last 6 hours)

Output: JSON per-burst table {burst_id: {found, expected_seqs_seen, crc_failures,
        missing}} to stdout; exit nonzero if the API call fails.

Example:
  export SOFAR_API_TOKEN_BM_REEF=...   # (Nick's shell; do not echo the token)
  python3 count_phase_b.py --spotter-id SPOT-33507C --run-id S09B2 \
      --start 2026-07-27T00:00:00Z --end 2026-07-27T02:00:00Z

Known limitations: sensor-data pagination behavior unverified for very large
windows — keep query windows tight around each run. Counts only lines matching
the TST format; START/END-style RC messages need the backend's own parsers.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

PAT = re.compile(r"TST,(?P<burst>[A-Za-z0-9]+G?\d*),(?P<seq>\d{5}),(?P<pad>[A-Z0-9]*)\*(?P<crc>[0-9A-F]{2})")


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--spotter-id", required=True)
    p.add_argument("--run-id", required=True, help="base run id; matches bursts <run>*")
    p.add_argument("--start", default=None, help="UTC ISO start (default now-6h)")
    p.add_argument("--end", default=None, help="UTC ISO end (default now)")
    args = p.parse_args()

    token = os.environ.get("SOFAR_API_TOKEN_BM_REEF")
    if not token:
        sys.exit("Set SOFAR_API_TOKEN_BM_REEF in the environment (never on the CLI).")

    now = datetime.now(timezone.utc)
    start = args.start or (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = args.end or now.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = "https://api.sofarocean.com/api/sensor-data?" + urlencode({
        "spotterId": args.spotter_id, "startDate": start, "endDate": end,
        "token": token,
    })
    print(f"# GET sensor-data spotterId={args.spotter_id} {start}..{end}", file=sys.stderr)
    with urlopen(url, timeout=45) as resp:
        payload = json.load(resp)

    rows = payload.get("data", [])
    print(f"# {len(rows)} sensor-data rows in window", file=sys.stderr)

    bursts = {}
    for entry in rows:
        raw = entry.get("value")
        if not raw:
            continue
        try:
            decoded = bytes.fromhex(str(raw).strip()).decode("utf-8", "replace")
        except ValueError:
            continue
        m = PAT.search(decoded)
        if not m or not m.group("burst").startswith(args.run_id):
            continue
        b = bursts.setdefault(m.group("burst"), {"seqs": [], "crc_failures": []})
        seq = int(m.group("seq"))
        body = f"TST,{m.group('burst')},{m.group('seq')},{m.group('pad')}"
        b["seqs"].append(seq)
        if crc8(body.encode()) != int(m.group("crc"), 16):
            b["crc_failures"].append(seq)

    out = {}
    for burst, b in sorted(bursts.items()):
        seqs = sorted(set(b["seqs"]))
        out[burst] = {
            "found": len(b["seqs"]),
            "unique": len(seqs),
            "min_seq": seqs[0] if seqs else None,
            "max_seq": seqs[-1] if seqs else None,
            "missing_within_range": [s for s in range(seqs[0], seqs[-1] + 1) if s not in seqs][:30] if seqs else [],
            "crc_failures": b["crc_failures"][:30],
        }
    print(json.dumps({"spotter_id": args.spotter_id, "window": [start, end],
                      "run_id": args.run_id, "bursts": out}, indent=2))


if __name__ == "__main__":
    main()
