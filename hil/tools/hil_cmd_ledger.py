#!/usr/bin/env python3
"""hil_cmd_ledger.py — per-command delivery ledger for a bench device: backend status vs what the Spotter
console saw (command received, unit OK/ERR), the wake it arrived in, the lag in wakes, and the receive time
against the Spotter's hourly report (the hub.sync hypothesis: remote commands are only fetched at the report).

Runs ON the monitor host (nereus000): the admin token never leaves it, and the console logs live there.
Read-only: GET /admin/devices/{d}/commands + the spotter-monitor console logs.

Inputs:  --api, --env-file (ADMIN_TOKEN=...), --device BMCAM_003 | BMCAM_004 (bench allow-list), --spot SPOT-…
         (must be the device's Spotter), --since UTC ISO (commands created at/after), --min-id 1000000 (remote ids),
         --log-root /home/pi/spotter_logs
Outputs: CSV on stdout, one row per command: command_id, verb, what, created, sent, backend_status, backend_ack,
         spotter_rx (console "Remote message received" with that id), report_before_rx (the last LEGACY len-50
         report queued ≤ 15 min before rx), rx_after_report_s, unit_reply (OK/ERR + time), reply_text, arrival_wake
         (hh:00 of the reply), lag_wakes (wake boundaries between send and reply).
Example: ssh pi@192.168.1.45 'python3 /home/pi/hil_g4/hil_cmd_ledger.py --api https://nereus-vision-staging.onrender.com
           --device BMCAM_003 --spot SPOT-33507C --since 2026-10-03T18:00' > runs/<run>/analysis/cmd_ledger.csv
Limits:  console-only evidence: a reply lost between the unit and the console is not distinguishable from no reply.
         A duplicate delivery (mote replay) keeps the FIRST rx/reply. Lines are matched by id text, so ids must be unique
         per Spotter (they are: remote ids >= 1e6 per device).
"""

import argparse
import csv
import datetime
import glob
import json
import os
import re
import sys
import urllib.request

ALLOWED = {"BMCAM_003": "SPOT-33507C", "BMCAM_004": "SPOT-31593C"}
UTC = datetime.timezone.utc
RX = re.compile(r'Remote message received.*?"id":(\d+)')
REPLY = re.compile(r'\] (OK|ERR) id=(\d+)\s*(.*?)(?: cfg=[0-9a-f]+)?\s*$')
REPORT = re.compile(r'Added message\(id: \d+ len: 50\) to queue MS_Q_LEGACY')


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


def console_events(log_root, spot, since):
    """First rx / reply per id, and every report time, from the console logs on/after `since`'s day."""
    rx, reply, reports = {}, {}, []
    day0 = since.date()
    for path in sorted(glob.glob(os.path.join(log_root, spot, "console_*.log"))):
        m = re.search(r"console_(\d{8})\.log$", path)
        if not m or datetime.datetime.strptime(m.group(1), "%Y%m%d").date() < day0:
            continue
        for line in open(path, encoding="utf-8", errors="replace"):
            t = ts(line[:20]) if len(line) > 20 and line[19] == "Z" else None
            if t is None or t < since:
                continue
            if REPORT.search(line):
                reports.append(t)
            r = RX.search(line)
            if r:
                rx.setdefault(int(r.group(1)), t)
            p = REPLY.search(line)
            if p:
                reply.setdefault(int(p.group(2)), (p.group(1), t, p.group(3)[:80]))
    return rx, reply, sorted(reports)


def wakes_between(a, b):
    """Number of hh:00 boundaries in (a, b]: the wakes the command waited through, inclusive of the reply wake."""
    if not (a and b) or b < a:
        return ""
    h = lambda t: int(t.timestamp() // 3600)
    return h(b) - h(a)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", required=True)
    ap.add_argument("--env-file", default="/home/pi/.config/nereus/heal_driver.env")
    ap.add_argument("--device", required=True)
    ap.add_argument("--spot", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--min-id", type=int, default=1000000)
    ap.add_argument("--log-root", default="/home/pi/spotter_logs")
    a = ap.parse_args()
    if ALLOWED.get(a.device) != a.spot:
        raise SystemExit(f"[cmd_ledger] REFUSED: {a.device}/{a.spot} is not a bench pair {ALLOWED}")
    since = ts(a.since)
    req = urllib.request.Request(a.api.rstrip("/") + f"/admin/devices/{a.device}/commands?limit=500",
                                 headers={"Authorization": f"Bearer {read_token(a.env_file)}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        cmds = json.loads(r.read())["commands"]
    rx, reply, reports = console_events(a.log_root, a.spot, since - datetime.timedelta(hours=1))
    w = csv.writer(sys.stdout)
    w.writerow(["command_id", "verb", "what", "created", "sent", "backend_status", "backend_ack", "spotter_rx",
                "report_before_rx", "rx_after_report_s", "unit_reply", "reply_at", "reply_text", "arrival_wake",
                "lag_wakes"])
    for c in sorted(cmds, key=lambda c: c["command_id"]):
        cid = int(c["command_id"])
        created = ts(c.get("created_at"))
        if cid < a.min_id or created is None or created < since:
            continue
        sent = ts(c.get("sent_at"))
        t_rx = rx.get(cid)
        rep = [t for t in reports if t_rx and t <= t_rx and (t_rx - t).total_seconds() <= 900]
        rep_t = rep[-1] if rep else None
        kind, t_reply, text = reply.get(cid, ("", None, ""))
        what = json.dumps(c.get("kv") or {}, separators=(",", ":")) if c.get("verb") in ("set", "reset") else ""
        w.writerow([cid, c.get("verb"), what, created.isoformat(timespec="seconds"),
                    sent.isoformat(timespec="seconds") if sent else "", c.get("status"),
                    bool(c.get("ack")), t_rx.isoformat(timespec="seconds") if t_rx else "",
                    rep_t.isoformat(timespec="seconds") if rep_t else "",
                    int((t_rx - rep_t).total_seconds()) if rep_t else "", kind,
                    t_reply.isoformat(timespec="seconds") if t_reply else "", text,
                    t_reply.strftime("%H:00") if t_reply else "", wakes_between(sent or created, t_reply)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
