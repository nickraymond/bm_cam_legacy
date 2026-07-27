#!/usr/bin/env python3
# filename: soak_command_scheduler.py
# description: Sprint10 soak — execute a timed command-send plan unattended.
"""
Sprint10 24 h soak — deterministic command scheduler (SOAK_PLAN_24H.md).

Reads a JSON plan and executes each entry at its UTC time, sending
either through the operator GUI server (exercises the real GUI send
path: lifecycle store, in-flight handling, id allocation) or directly
via the Sofar API (for raw/negative tests the GUI cannot produce, and
for explicit-id tests like duplicates).

Plan entry fields:
  at        "HH:MM" UTC today/tomorrow (next occurrence)
  route     "gui" | "direct"
  spotter_id, c, v        (gui + direct)
  id        explicit command id (direct only; gui allocates its own)
  raw       raw console message (direct only; bypasses tables — for
            negative tests)
  note      free text carried into the log

Outputs: prints one line per action; appends JSONL results next to the
plan file (<plan>.results.jsonl). Exits when the plan is done.

Example:
  python3 tools/soak_command_scheduler.py --plan runs/sprint10_soak_20260727/command_plan.json
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import sofar_send_command as ssc  # noqa: E402

GUI_URL = "http://127.0.0.1:8770/api/send"


def next_occurrence(hhmm, now=None):
    now = now or datetime.now(timezone.utc)
    h, m = map(int, hhmm.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t


def send_gui(entry):
    body = {"spotter_id": entry["spotter_id"],
            "node_id": entry.get("node_id", ""),
            "c": entry["c"], "v": entry.get("v"),
            "override_in_flight": bool(entry.get("override"))}
    req = urllib.request.Request(
        GUI_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read())


def send_direct(entry, token):
    if "raw" in entry:
        message = entry["raw"]
    else:
        payload = ssc.build_command_json(entry["id"], entry["c"],
                                         entry.get("v"))
        message = ssc.build_console_line(payload)
    ssc.validate_message(message)
    status, resp = ssc.post_command(entry["spotter_id"], token,
                                    {"telemetry": ssc.TELEMETRY,
                                     "message": message})
    ssc.append_send_log(ssc.SEND_LOG, {
        "ts": time.time(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "spotter_id": entry["spotter_id"], "telemetry": ssc.TELEMETRY,
        "message": message, "clear_command_queue": False,
        "http_status": status, "response": resp, "via": "soak_scheduler",
    })
    return status, resp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--plan", required=True)
    args = ap.parse_args(argv)

    token = os.environ.get(ssc.TOKEN_ENV)
    if not token:
        print(f"[ERROR] set {ssc.TOKEN_ENV}")
        return 2
    with open(args.plan) as f:
        plan = json.load(f)["plan"]
    results_path = args.plan + ".results.jsonl"

    entries = sorted(plan, key=lambda e: next_occurrence(e["at"]))
    print(f"[scheduler] {len(entries)} entries; first at "
          f"{next_occurrence(entries[0]['at']).strftime('%H:%M')}Z")
    for entry in entries:
        target = next_occurrence(entry["at"])
        wait = (target - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        try:
            if entry.get("route") == "gui":
                status, resp = send_gui(entry)
            else:
                status, resp = send_direct(entry, token)
            ok = status in (200, 202)
        except Exception as e:  # keep the schedule alive, loudly
            status, resp, ok = None, f"scheduler exception: {e}", False
        line = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "entry": entry, "status": status, "resp": resp, "ok": ok}
        with open(results_path, "a") as f:
            f.write(json.dumps(line) + "\n")
        print(f"[scheduler] {line['utc']} {entry.get('note','')} -> "
              f"{status} {'OK' if ok else 'FAILED'}")
    print("[scheduler] plan complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
