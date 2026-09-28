#!/usr/bin/env python3
# filename: soak_command_scheduler.py
# description: Sprint10 soak / Sprint26 S4 c.3 — execute a timed command-send plan (commands v9) unattended.
"""
Soak command scheduler (Sprint10 SOAK_PLAN_24H.md; commands v9 since
Sprint26 S4 c.3, DESIGN_supervisor.md §6.1).

Reads a JSON plan and executes each entry at its UTC time, sending
either through the operator GUI server (exercises the real GUI send
path: lifecycle store, in-flight handling, id allocation) or directly
via the Sofar API (for raw/negative tests the GUI cannot produce, and
for explicit-id tests like duplicates).

Plan entry fields:
  at        "HH:MM" UTC today/tomorrow (next occurrence)
  route     "gui" | "direct"
  spotter_id, node_id (node_id: gui, for the ack's node check)
  cmd       the v9 command object, e.g. {"c":"set","kv":{"d":8}},
            {"c":"get","k":["mode"]}, {"c":"trg","v":2}, {"c":"ping"}.
            gui: WITHOUT "id" (the GUI allocates one in the remote range);
            direct: WITH "id" (or give it as the entry's "id"), validated
            by sofar_send_command.validate_command (command_wire.decode).
  raw       raw console message (direct only; bypasses every check — for
            negative tests)
  override  gui: send even while a command is pending for the target
  note      free text carried into the log

Outputs: prints one line per action; appends JSONL results next to the
plan file (<plan>.results.jsonl). Exits when the plan is done.

Example plan:
  {"plan": [{"at": "14:05", "route": "gui", "spotter_id": "SPOT-33507C",
             "node_id": "53171fa3d81a8e6f", "cmd": {"c": "get", "k": ["mode"]},
             "note": "remote get"}]}
Example:
  python3 tools/soak_command_scheduler.py --plan runs/sprint10_soak_20260727/command_plan.json

Known limitations: the plan is checked entry by entry at start (bad
entries refuse the whole plan); the GUI server must be running for gui
entries (http://127.0.0.1:8770).
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


def check_entry(entry):
    """Refuse a plan entry the scheduler cannot send (at start, loudly).
    Returns None or a one-line reason."""
    for key in ("at", "spotter_id"):
        if key not in entry:
            return f"missing {key}"
    route = entry.get("route", "direct")
    if route not in ("gui", "direct"):
        return f"route {route!r} is gui or direct"
    if "raw" in entry:
        return None if route == "direct" else "raw is direct-only"
    cmd = entry.get("cmd")
    if not isinstance(cmd, dict) or not isinstance(cmd.get("c"), str):
        return 'cmd must be a v9 object like {"c":"ping"} (v8 c/v entries are retired)'
    if route == "gui":
        if "id" in cmd or "id" in entry:
            return "gui entries carry no id (the GUI allocates one)"
        # the GUI validates with a real id; check the shape with a placeholder
        probe = dict(cmd, id=ssc.W.RANGES[2][1])   # the first remote-range id
    else:
        probe = direct_command(entry)
    try:
        ssc.validate_command(probe)
    except ValueError as exc:
        return str(exc)
    return None


def direct_command(entry):
    """The v9 object a direct entry sends: cmd with its id first."""
    cmd = dict(entry["cmd"])
    cid = cmd.pop("id", entry.get("id"))
    return {"id": cid, **cmd}


def gui_body(entry):
    """POST body for the GUI's /api/send: the v9 fields flat, no id."""
    body = {k: v for k, v in entry["cmd"].items() if k != "id"}
    body.update({"spotter_id": entry["spotter_id"],
                 "node_id": entry.get("node_id", ""),
                 "override_in_flight": bool(entry.get("override"))})
    return body


def send_gui(entry):
    body = gui_body(entry)
    req = urllib.request.Request(
        GUI_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read())


def send_direct(entry, token):
    if "raw" in entry:
        message = entry["raw"]
    else:
        payload = ssc.validate_command(direct_command(entry))
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
    bad = [(i, why) for i, e in enumerate(plan) for why in [check_entry(e)] if why]
    for i, why in bad:
        print(f"[scheduler][ERROR] entry {i} ({plan[i].get('note', '')}): {why}")
    if bad:
        print(f"[scheduler] {len(bad)} bad entr{'y' if len(bad) == 1 else 'ies'}; "
              "nothing sent.")
        return 2
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
