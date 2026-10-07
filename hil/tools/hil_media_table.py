#!/usr/bin/env python3
"""hil_media_table.py — D1 table for a gate window: every media row of the bench devices with capture time,
last-chunk time and completeness, plus the D1 verdict (complete <= --d1-hours after capture).

Runs ON the monitor host (nereus000), like hil_g4_alternator.py, because the admin token never leaves it.
Read-only: GET /devices/{d}/media (paged, newest first) only.

Inputs:  --api, --env-file (ADMIN_TOKEN=...), --devices BMCAM_003,BMCAM_004 (bench allow-list),
         --since / --until (UTC ISO, capture-time window), --d1-hours 3, --now (UTC ISO, default: now)
Outputs: CSV on stdout: device, media_id, type, captured_at, last_chunk_at, expected, received, missing,
         complete, latency_min, d1 (PASS / FAIL / OPEN = incomplete but still inside the d1 window).
Example: ssh pi@192.168.1.45 'python3 /home/pi/hil_g4/hil_media_table.py --api https://nereus-vision-staging.onrender.com
           --since 2026-10-03T04:00 --until 2026-10-03T16:00' > runs/g4_.../analysis/media.csv
Limits:  last_chunk_at = the row's timestamp_utc: the backend moves it each time the row is (re)assembled, so for
         a complete row it is the completion time (or later, if a redundant heal chunk re-assembles it: the
         latency is an upper bound). NOT received_at_utc: that is the FIRST receipt (measured 2026-10-03: a clip
         healed at 07:10Z kept received_at 05:10Z). Power-event clips (G4.10) are not marked here: the analysis tags them from gate.log.
"""

import argparse
import csv
import datetime
import json
import sys
import urllib.request

ALLOWED = {"BMCAM_003", "BMCAM_004"}
UTC = datetime.timezone.utc


def ts(s):
    if not s:
        return None
    d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def read_token(path):
    for line in open(path, encoding="utf-8"):
        k, _, v = line.strip().partition("=")
        if k == "ADMIN_TOKEN" and v:
            return v.strip().strip("'\"")
    raise SystemExit(f"ADMIN_TOKEN not found in {path}")


def get(api, token, path):
    req = urllib.request.Request(api.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", required=True)
    ap.add_argument("--env-file", default="/home/pi/.config/nereus/heal_driver.env")
    ap.add_argument("--devices", default="BMCAM_003,BMCAM_004")
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--d1-hours", type=float, default=3.0)
    ap.add_argument("--now", default="")
    a = ap.parse_args()
    devs = [d.strip() for d in a.devices.split(",") if d.strip()]
    if any(d not in ALLOWED for d in devs):
        raise SystemExit(f"[media_table] REFUSED: only {sorted(ALLOWED)}")
    since, until = ts(a.since), ts(a.until)
    now = ts(a.now) if a.now else datetime.datetime.now(UTC)
    token = read_token(a.env_file)
    w = csv.writer(sys.stdout)
    w.writerow(["device", "media_id", "type", "captured_at", "last_chunk_at", "expected", "received", "missing",
                "complete", "latency_min", "d1"])
    for dev in devs:
        page = 1
        while True:
            rows = get(a.api, token, f"/devices/{dev}/media?page={page}&page_size=72&include_hidden=true")
            if not rows:
                break
            for r in rows:
                cap = ts(r.get("captured_at_utc"))
                if cap is None or not (since <= cap < until):
                    continue
                last = ts(r.get("timestamp_utc"))
                done = bool(r.get("is_complete")) if r.get("is_complete") is not None else True
                lat = (last - cap).total_seconds() / 60 if (done and last) else None
                if done and lat is not None:
                    d1 = "PASS" if lat <= a.d1_hours * 60 else "FAIL"
                else:
                    d1 = "OPEN" if (now - cap).total_seconds() <= a.d1_hours * 3600 else "FAIL"
                w.writerow([dev, r.get("media_id"), r.get("type"), cap.isoformat(timespec="seconds"),
                            last.isoformat(timespec="seconds") if last else "", r.get("expected_chunks"),
                            r.get("received_chunks"), r.get("missing_chunk_count"), done,
                            f"{lat:.0f}" if lat is not None else "", d1])
            oldest = min((ts(r.get("timestamp_utc")) or now) for r in rows)   # list is ordered by timestamp_utc
            if oldest < since or len(rows) < 72:
                break
            page += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
