#!/usr/bin/env python3
"""S6b HIL: per-heal latency ledger + summary stats from the mirrored evidence (Mac side).

Purpose : Nick's morning report: backend auto-send heals issued vs received by the camera,
          and the latency of each hop (mean / mode / stdev / min / max / n).
Inputs  : runs/s6b_hil_20260930/h4/ (written by h4_mirror.sh)
            <SPOT>_heal-events.json      heal_request rows (t_alloc, t_202) + heal_status (<HL>)
            <SPOT>_console_evidence.txt  nereus000 console lines (timestamped on nereus000, NTP)
            media_status.jsonl           completion per media (media.timestamp_utc)
Outputs : h4/heal_ledger_auto.csv (one row per autosend heal), h4/heal_summary.json
Clocks  : t_alloc / t_202 / t_complete = Render; t_spotter_rx / t_camera_ok = the console's
          own Spotter ISO stamp when present, else nereus000 receive time; t_hl_* = Sofar row
          time of the <HL> uplink (Spotter clock). All NTP/GPS-disciplined (checked 05:32Z).
Hops    : send->spotter  = t_spotter_rx - t_202   (Remote message received(N)! … "id":ID)
          send->camera   = t_camera_ok  - t_202   ([bmcamNNN] OK id=ID rsd: …; the camera
                           dispatches commands between actions, so this includes any
                           burst that was running when the rsd landed)
          send->hl_sent  = first <HL a=sent> for the id - t_202 (the camera finished re-sending)
          send->complete = max(media.timestamp_utc over the heal's media, if all complete) - t_202
Mode    : of values rounded to whole minutes (continuous data).
Example : python3 runs/s6b_hil_20260930/h4_ledger.py
Limits  : a heal whose media is healed by a LATER heal gets its complete time attributed to
          both; `complete_via` names the last heal id that touched the media.
"""
import csv, json, re, statistics, sys
from collections import Counter
from datetime import datetime
from pathlib import Path

H4 = Path(__file__).resolve().parent / "h4"
SPOTS = {"SPOT-33507C": "BMCAM_003", "SPOT-31593C": "BMCAM_004"}
ISO = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)")


def ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def console_time(line):
    """Prefer the Spotter's own ms stamp (2nd ISO on the line), else nereus000's (1st)."""
    found = ISO.findall(line)
    return ts(found[1] if len(found) > 1 else found[0]) if found else None


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    mins = [round(v / 60) for v in vals]
    return {"n": len(vals), "min_s": round(min(vals), 1), "max_s": round(max(vals), 1),
            "mean_s": round(statistics.mean(vals), 1),
            "stdev_s": round(statistics.stdev(vals), 1) if len(vals) > 1 else None,
            "median_s": round(statistics.median(vals), 1),
            "mode_min": Counter(mins).most_common(1)[0][0]}


media = {}
mpath = H4 / "media_status.jsonl"
if mpath.exists():
    for line in mpath.read_text().splitlines():
        m = json.loads(line)
        media[m["media_id"]] = m

rows = []
for spot, dev in SPOTS.items():
    he = json.loads((H4 / f"{spot}_heal-events.json").read_text())
    con = (H4 / f"{spot}_console_evidence.txt").read_text(errors="replace").splitlines() \
        if (H4 / f"{spot}_console_evidence.txt").exists() else []
    hl = [r for r in he["rows"] if r["kind"] == "heal_status"]
    for r in he["rows"]:
        if r["kind"] != "heal_request" or r.get("requested_by") != "autosend":
            continue
        hid = r["command_id"]
        t202 = ts(r.get("sent_at"))
        rx = next((console_time(l) for l in con
                   if "Remote message received" in l and f'"id":{hid},' in l), None)
        ok = next((console_time(l) for l in con if f"OK id={hid} " in l), None)
        mine = [h for h in hl if h.get("command_id") == hid]
        sent = [ts(h["timestamp_utc"]) for h in mine if h.get("action") == "sent"]
        req = [ts(h["timestamp_utc"]) for h in mine if h.get("action") == "requested"]
        mids = [h["media_id"] for h in r.get("heals") or []]
        ms = [media.get(m) for m in mids]
        done = all(m and m.get("missing_reason") == "complete" for m in ms) and ms
        tcomp = max(ts(m["timestamp_utc"]) for m in ms) if done else None
        d = lambda t: round((t - t202).total_seconds(), 1) if (t and t202) else None
        rows.append({
            "heal_id": hid, "device": dev, "spotter": spot, "chunks": r.get("chunks"),
            "keys": ";".join(f'{h["media_key"]}:{h["ranges"]}' for h in r.get("heals") or []),
            "media_ids": ";".join(map(str, mids)),
            "send_outcome": r.get("send_outcome"), "sent_status": r.get("sent_status"),
            "t_alloc": r.get("timestamp_utc"), "t_202": r.get("sent_at"),
            "t_spotter_rx": rx and rx.isoformat(), "t_camera_ok": ok and ok.isoformat(),
            "t_hl_requested": min(req).isoformat() if req else None,
            "t_hl_sent": min(sent).isoformat() if sent else None,
            "hl_sent_actions": ",".join(f'{h.get("media_key")}:{h.get("action")}/{h.get("reason")}' for h in mine),
            "t_complete": tcomp and tcomp.isoformat(),
            "send_to_spotter_s": d(rx), "send_to_camera_ok_s": d(ok),
            "send_to_hl_sent_s": d(min(sent)) if sent else None, "send_to_complete_s": d(tcomp),
        })

rows.sort(key=lambda x: x["t_alloc"] or "")
if rows:
    with open(H4 / "heal_ledger_auto.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

summary = {}
for dev in list(SPOTS.values()) + ["ALL"]:
    rs = [r for r in rows if dev == "ALL" or r["device"] == dev]
    summary[dev] = {
        "heals_issued": len(rs),
        "sent_202": sum(1 for r in rs if r["sent_status"] == 202),
        "not_sent": sum(1 for r in rs if r["sent_status"] != 202),
        "rx_at_spotter": sum(1 for r in rs if r["t_spotter_rx"]),
        "received_by_camera": sum(1 for r in rs if r["t_camera_ok"] or r["t_hl_requested"]),
        "camera_resent": sum(1 for r in rs if r["t_hl_sent"]),
        "media_complete": sum(1 for r in rs if r["t_complete"]),
        "chunks_asked": sum(r["chunks"] or 0 for r in rs),
        "send_to_spotter": stats([r["send_to_spotter_s"] for r in rs]),
        "send_to_camera_ok": stats([r["send_to_camera_ok_s"] for r in rs]),
        "send_to_hl_sent": stats([r["send_to_hl_sent_s"] for r in rs]),
        "send_to_complete": stats([r["send_to_complete_s"] for r in rs]),
    }
(H4 / "heal_summary.json").write_text(json.dumps(summary, indent=1))
json.dump(summary, sys.stdout, indent=1)
print()
