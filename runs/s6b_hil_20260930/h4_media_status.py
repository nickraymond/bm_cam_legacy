#!/usr/bin/env python3
"""S6b HIL: completion status of every media named in a heal_request (runs ON nereus000).

Input : heal-events JSON files on stdin are NOT used; this script fetches heal-events itself
        for the two bench Spotters (admin token from ~/.config/nereus/heal_driver.env, never printed).
Output: one JSON line per media on stdout: media_id, device, heal ids, missing reason/count,
        received_at_utc, timestamp_utc (row last update; = assembly time once complete).
        Presigned URLs are dropped.
Example: ssh pi@192.168.1.45 'python3 -' < runs/s6b_hil_20260930/h4_media_status.py
"""
import json, os, urllib.request

API = "https://nereus-vision-staging.onrender.com"
env = {}
for line in open(os.path.expanduser("~/.config/nereus/heal_driver.env")):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v.strip('"').strip("'")
TOK = env["ADMIN_TOKEN"]


def get(path):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + TOK})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"http_error": e.code}


media = {}
for spot in ("SPOT-33507C", "SPOT-31593C"):
    for row in get(f"/systems/{spot}/heal-events?hours=30").get("rows", []):
        if row.get("kind") != "heal_request" or row.get("requested_by") != "autosend":
            continue
        for h in row.get("heals") or []:
            m = media.setdefault(h["media_id"], {"media_id": h["media_id"], "device": row["device_id"],
                                                 "media_key": h["media_key"], "heal_ids": []})
            m["heal_ids"].append(row["command_id"])
for mid, m in sorted(media.items()):
    miss = get(f"/admin/ingest/media/{mid}/missing")
    d = miss.get("detail") if isinstance(miss.get("detail"), dict) else miss
    m["missing_reason"] = d.get("reason")
    m["missing_count"] = d.get("missing_count", 0 if d.get("reason") == "complete" else None)
    row = get(f"/media/{mid}").get("media", {})
    for k in ("captured_at_utc", "received_at_utc", "timestamp_utc", "format"):
        m[k] = row.get(k)
    print(json.dumps(m, sort_keys=True))
