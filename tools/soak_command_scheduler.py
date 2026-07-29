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


CATCHUP_LOOKBACK_H = 3  # a time missed less than this long ago fires NOW


def next_occurrence(hhmm, now=None):
    """Next occurrence of HH:MM — except an occurrence missed within the
    catch-up lookback returns that PAST time, so a restarted scheduler
    fires missed entries immediately instead of waiting a day."""
    now = now or datetime.now(timezone.utc)
    h, m = map(int, hhmm.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        if (now - t) <= timedelta(hours=CATCHUP_LOOKBACK_H):
            return t  # recently missed -> catch up
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

    # Targets are computed ONCE at start. A target already past at
    # execution time fires immediately in catch-up mode (bug found in
    # the 24h soak: recomputing per-entry pushed late entries to
    # TOMORROW after the Mac napped through an alarm). Catch-up sends
    # keep >=65 s spacing per Spotter (Sofar 1/min hard limit).
    schedule = sorted(((next_occurrence(e["at"]), e) for e in plan),
                      key=lambda t: t[0])
    print(f"[scheduler] {len(schedule)} entries; first at "
          f"{schedule[0][0].strftime('%H:%M')}Z", flush=True)
    last_send = {}  # spotter_id -> monotonic time of last fired send
    for target, entry in schedule:
        wait = (target - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        else:
            print(f"[scheduler] LATE by {-wait:.0f}s: "
                  f"{entry.get('note','')} — firing now", flush=True)
        spot = entry["spotter_id"]
        gap = time.monotonic() - last_send.get(spot, -1e9)
        if gap < 65:
            time.sleep(65 - gap)
        last_send[spot] = time.monotonic()
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
              f"{status} {'OK' if ok else 'FAILED'}", flush=True)
    print("[scheduler] plan complete.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
