#!/usr/bin/env python3
# filename: server.py
# description: Sprint10 §7 — local operator GUI server (Mac, stdlib only).
"""
Sprint10 operator GUI — the human sending surface for BM camera commands
(SPEC "Operator GUI", DESIGN D9/D10). Local Mac tool, NOT the customer
website. Zero dependencies beyond the repo (stdlib http.server).

    export SOFAR_API_TOKEN_BM_REEF=...     # never on the CLI
    python3 tools/bm_command_gui/server.py [--port 8770]
    open http://127.0.0.1:8770

What it does
  - Target selection from targets.json (registered SPOT-ID + expected
    BM node id) — GUI item 1.
  - Preset dropdowns GENERATED from command_tables.py — the GUI cannot
    offer a value the daemon can't apply — GUI item 2 / D9.
  - Sends via the Sofar Command API (same code path as
    tools/sofar_send_command.py) and shows the cloud-accept result +
    in-flight state per command id; refuses to stack sends while one is
    pending for the target (override checkbox) and enforces Sofar's
    1/min/Spotter rate limit client-side — GUI item 3 / D10.
  - Polls api/sensor-data for acks (auto every poll_s, observed backend
    lag 13-30 min), verifies them, displays acked/mismatch loudly —
    GUI item 4.

State: lifecycle events append to runs/gui_commands.jsonl (replayed on
restart); sends also append to the shared runs/sofar_command_sends.jsonl
so the CLI and GUI share one rate-limit view.

Bind: 127.0.0.1 only. One operator at a time is the design point.
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
sys.path.insert(0, HERE)

import command_tables as ct            # noqa: E402
import lifecycle as lc                 # noqa: E402
import sofar_poll_acks as spa          # noqa: E402
import sofar_send_command as ssc       # noqa: E402

DEFAULT_PORT = 8770
GUI_LOG = os.path.join(REPO_ROOT, "runs", "gui_commands.jsonl")


class GuiState:
    """All mutable state behind one lock (ThreadingHTTPServer)."""

    def __init__(self, targets_path, gui_log=GUI_LOG,
                 send_log=ssc.SEND_LOG, poll_s=120):
        self.lock = threading.Lock()
        self.store = lc.CommandLifecycle(gui_log)
        self.send_log = send_log
        self.poll_s = poll_s
        self.last_poll = {"utc": None, "acks_seen": 0, "error": None}
        with open(targets_path, "r", encoding="utf-8") as f:
            self.targets = json.load(f)["targets"]

    # -- config for the page ---------------------------------------------

    def config(self):
        commands = {}
        for cmd in ct.COMMANDS:
            if cmd == "ping":
                commands[cmd] = [{"v": 0, "label": "ping (liveness test)"}]
                continue
            commands[cmd] = [
                {"v": v, "label": f"{v}: {ct.entry_for(cmd, v)['label']}"}
                for v in sorted(ct.table_for(cmd))
            ]
        return {
            "targets": self.targets,
            "commands": commands,
            "tables_version": ct.TABLES_VERSION,
            "poll_s": self.poll_s,
            "rate_limit_s": ssc.RATE_LIMIT_S,
        }

    # -- sending ----------------------------------------------------------

    def send(self, spotter_id, node_id, c, v, override_in_flight=False):
        """Validate, build, rate-limit, POST, record. Returns a dict for
        the page; never raises for operator-level errors."""
        token = os.environ.get(ssc.TOKEN_ENV)
        if not token:
            return {"error": f"server started without {ssc.TOKEN_ENV} set"}
        with self.lock:
            pending = self.store.in_flight(spotter_id)
            if pending and not override_in_flight:
                return {"error": "in_flight",
                        "detail": [p["cmd_id"] for p in pending]}
            last = ssc.load_last_success_ts(self.send_log, spotter_id)
            now = time.time()
            if last is not None and now - last < ssc.RATE_LIMIT_S:
                return {"error": "rate_limited",
                        "retry_in_s": int(ssc.RATE_LIMIT_S - (now - last)) + 1}
            try:
                cmd_id = self.store.next_command_id()
                payload = ssc.build_command_json(
                    cmd_id, c, None if c == "ping" else int(v))
                message = ssc.build_console_line(payload)
                ssc.validate_message(message)
            except (ValueError, TypeError) as e:
                return {"error": f"invalid command: {e}"}
        # network I/O outside the lock
        status, resp = ssc.post_command(spotter_id, token,
                                        {"telemetry": ssc.TELEMETRY,
                                         "message": message})
        with self.lock:
            ssc.append_send_log(self.send_log, {
                "ts": now, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime(now)),
                "spotter_id": spotter_id, "telemetry": ssc.TELEMETRY,
                "message": message, "clear_command_queue": False,
                "http_status": status, "response": resp, "via": "gui",
            })
            self.store.record_sent(cmd_id, spotter_id, node_id, c,
                                   None if c == "ping" else int(v),
                                   message, status, resp)
            return {"cmd_id": cmd_id, "state": self.store.get(cmd_id)["state"],
                    "http_status": status, "response": resp}

    # -- ack polling -------------------------------------------------------

    def poll_acks_once(self, hours=6):
        token = os.environ.get(ssc.TOKEN_ENV)
        if not token:
            return
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.lock:
            awaiting = {c["cmd_id"]: c for c in self.store.all_commands()
                        if c["state"] in lc.IN_FLIGHT_STATES}
            spotters = sorted({c["spotter_id"] for c in awaiting.values()})
        seen = 0
        err = None
        for spotter in spotters:
            try:
                acks = spa.fetch_acks(spotter, token, start, end)
            except OSError as e:
                err = str(e)
                continue
            with self.lock:
                for _ts, ack in acks:
                    seen += 1
                    cid = ack["id"]
                    cmd = self.store.get(cid)
                    if cmd is not None and cmd["state"] in lc.IN_FLIGHT_STATES:
                        self.store.record_ack(cid, ack)
        with self.lock:
            self.last_poll = {
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "acks_seen": seen, "error": err,
                "spotters_polled": spotters,
            }

    def poll_loop(self):
        while True:
            time.sleep(self.poll_s)
            try:
                self.poll_acks_once()
            except Exception as e:  # keep the poller alive, loudly
                with self.lock:
                    self.last_poll = {"utc": None, "acks_seen": 0,
                                      "error": f"poller crashed: {e}"}

    def status(self):
        with self.lock:
            return {
                "commands": [
                    {k: v for k, v in c.items() if k != "history"}
                    for c in self.store.all_commands()
                ],
                "in_flight": [c["cmd_id"] for c in self.store.in_flight()],
                "last_poll": self.last_poll,
            }


def make_handler(state):
    index_path = os.path.join(HERE, "index.html")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(index_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/config":
                self._json(state.config())
            elif self.path == "/api/status":
                self._json(state.status())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            if self.path == "/api/send":
                for key in ("spotter_id", "node_id", "c"):
                    if key not in req:
                        return self._json({"error": f"missing {key}"}, 400)
                out = state.send(req["spotter_id"], req["node_id"],
                                 req["c"], req.get("v"),
                                 bool(req.get("override_in_flight")))
                self._json(out, 200 if "error" not in out else 409)
            elif self.path == "/api/poll_now":
                state.poll_acks_once()
                self._json(state.status())
            else:
                self._json({"error": "not found"}, 404)

        def log_message(self, fmt, *args):  # quiet: only errors matter
            if "40" in (args[1] if len(args) > 1 else ""):
                sys.stderr.write(f"[gui] {fmt % args}\n")

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--targets", default=os.path.join(HERE, "targets.json"))
    ap.add_argument("--poll-s", type=int, default=120)
    args = ap.parse_args(argv)

    if not os.environ.get(ssc.TOKEN_ENV):
        print(f"[ERROR] set {ssc.TOKEN_ENV} before starting the GUI.")
        return 2

    state = GuiState(args.targets, poll_s=args.poll_s)
    threading.Thread(target=state.poll_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(state))
    print(f"[gui] serving http://127.0.0.1:{args.port}  "
          f"(targets: {len(state.targets)}, tables v{ct.TABLES_VERSION}, "
          f"ack poll every {args.poll_s}s)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[gui] stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
