#!/usr/bin/env python3
"""S6b HIL R5 cut snapshot (runs ON nereus000, read-only against staging).

Purpose : at the G1 cut (22:30 PDT 2026-10-01, no drain, Nick), classify every conductor
          trigger per rig from BACKEND truth: complete / partial (in heal) / no_row.
Input   : the conductor run dir's state.json (trigger ledger), admin token from
          ~/.config/nereus/heal_driver.env (never printed).
Output  : JSON on stdout: {"cut_utc", "rigs": {device: {"counts", "clips": [...]}}}.
          Per clip: trigger_id, trigger_utc, media_id, expected, missing_count, status.
Example : ssh pi@192.168.1.45 'python3 - /home/pi/spotter_logs/conductor/20261001T053033Z' \
            < runs/s6b_hil_20260930/r5_snapshot.py > runs/s6b_hil_20260930/r5_snapshot.json
"""
import json, os, sys, urllib.error, urllib.request
from datetime import datetime, timezone

API = "https://nereus-vision-staging.onrender.com"
run_dir = sys.argv[1]
env = {}
for line in open(os.path.expanduser("~/.config/nereus/heal_driver.env")):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v.strip('"').strip("'")


def get(path):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"http_error": e.code}


ledger = json.load(open(os.path.join(run_dir, "state.json")))["ledger"]
out = {"cut_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rigs": {}}
for dev, recs in ledger.items():
    clips, counts = [], {}
    for r in recs:
        mid = r.get("media_id")
        c = {k: r.get(k) for k in ("trigger_id", "trigger_utc", "acked", "media_id", "expected")}
        if not mid:
            c["status"] = "no_row"
        else:
            d = get(f"/admin/ingest/media/{mid}/missing")
            d = d.get("detail") if isinstance(d.get("detail"), dict) else d
            if d.get("reason") == "complete":
                c["status"], c["missing_count"] = "complete", 0
            else:
                c["status"] = "partial"
                c["missing_count"] = d.get("missing_count", d.get("missing_total"))
                c["missing_reason"] = d.get("reason")
        counts[c["status"]] = counts.get(c["status"], 0) + 1
        clips.append(c)
    out["rigs"][dev] = {"triggers": len(recs), "counts": counts, "clips": clips}
print(json.dumps(out, indent=1))
