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

Delivery-robustness features (2026-07-31, after the mailbox-wedge
diagnosis — runs/remote_cmd_diagnosis_20260731/REPORT.md):
  - "Send on next wake" (default): the command is armed locally and
    fires the moment a fresh uplink row appears at api/sensor-data for
    the target (unit awake and transmitting) so the post-burst mailbox
    drain lands while the bus is on and the daemon is in its listen
    tail. Mailbox drains at other syncs execute onto a dead bus and the
    command is consumed unheard — measured live: ids 1016/1017 delivered
    to a 0.15 V bus.
  - Retry-until-ack: no ack after retry_after_s (default 40 min ≈ one
    duty cycle + margin) -> re-send the SAME id (daemon dedupe makes
    this idempotent), up to max_attempts, then retry_exhausted loudly.
    One command in flight per spotter, always — stacking pending
    commands in the Sofar FIFO is the wedge risk state.
  - Un-wedge button: clear_command_queue + fresh ping probe in one call
    (the recovery this diagnosis proved). NOTE: an already-dispatched
    head command can still arrive after a clear (observed: id 1016).

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
                 send_log=ssc.SEND_LOG, poll_s=120,
                 retry_after_s=2400, max_attempts=4, wake_poll_s=90):
        self.lock = threading.Lock()
        self.store = lc.CommandLifecycle(gui_log)
        self.send_log = send_log
        self.poll_s = poll_s
        self.retry_after_s = retry_after_s
        self.max_attempts = max_attempts
        self.wake_poll_s = wake_poll_s
        self.last_poll = {"utc": None, "acks_seen": 0, "error": None}
        # spotter -> newest sensor-data row utc seen by the wake watcher;
        # a scheduled command fires when this moves past its arm time.
        self.wake_rows = {}
        self.last_wake_check = {"utc": None, "error": None}
        with open(targets_path, "r", encoding="utf-8") as f:
            self.targets = json.load(f)["targets"]

    def _send_log_ids(self):
        """Command ids already used by ANY sender (CLI included), parsed
        from the shared send log, so the GUI never re-mints one."""
        import re
        ids = []
        try:
            with open(self.send_log, "r", encoding="ascii") as f:
                for line in f:
                    m = re.search(r'\\"id\\":(\d+)|"id":(\d+)', line)
                    if m:
                        ids.append(int(m.group(1) or m.group(2)))
        except FileNotFoundError:
            pass
        return ids

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
            "retry_after_s": self.retry_after_s,
            "max_attempts": self.max_attempts,
            "wake_poll_s": self.wake_poll_s,
        }

    # -- sending ----------------------------------------------------------

    def send(self, spotter_id, node_id, c, v, override_in_flight=False,
             mode="wake"):
        """Validate, build, rate-limit, POST (or arm for the next wake),
        record. Returns a dict for the page; never raises for
        operator-level errors. mode: "now" | "wake" (default — fires
        when a fresh uplink row shows the unit awake)."""
        token = os.environ.get(ssc.TOKEN_ENV)
        if not token:
            return {"error": f"server started without {ssc.TOKEN_ENV} set"}
        if mode not in ("now", "wake"):
            return {"error": f"unknown send mode {mode!r}"}
        with self.lock:
            pending = self.store.pending(spotter_id)
            if pending and not override_in_flight:
                return {"error": "in_flight",
                        "detail": [p["cmd_id"] for p in pending]}
            try:
                cmd_id = self.store.next_command_id(
                    extra_used=self._send_log_ids())
                payload = ssc.build_command_json(
                    cmd_id, c, None if c == "ping" else int(v))
                message = ssc.build_console_line(payload)
                ssc.validate_message(message)
            except (ValueError, TypeError) as e:
                return {"error": f"invalid command: {e}"}
            if mode == "wake":
                self.store.record_scheduled(cmd_id, spotter_id, node_id, c,
                                            None if c == "ping" else int(v),
                                            message)
                # Baseline: rows at/before arm time don't count as a wake.
                self.wake_rows.setdefault(spotter_id, None)
                return {"cmd_id": cmd_id, "state": lc.SCHEDULED}
            last = ssc.load_last_success_ts(self.send_log, spotter_id)
            now = time.time()
            if last is not None and now - last < ssc.RATE_LIMIT_S:
                return {"error": "rate_limited",
                        "retry_in_s": int(ssc.RATE_LIMIT_S - (now - last)) + 1}
        out = self._post_and_record(spotter_id, node_id, c, v, cmd_id,
                                    message, token, attempt=1)
        return out

    def _post_and_record(self, spotter_id, node_id, c, v, cmd_id, message,
                         token, attempt, clear_queue=False):
        """POST one command (network I/O outside the lock) and record it
        in both logs. Shared by direct sends, wake fires and retries."""
        body = {"telemetry": ssc.TELEMETRY, "message": message}
        if clear_queue:
            body["clear_command_queue"] = True
        now = time.time()
        status, resp = ssc.post_command(spotter_id, token, body)
        with self.lock:
            ssc.append_send_log(self.send_log, {
                "ts": now, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime(now)),
                "spotter_id": spotter_id, "telemetry": ssc.TELEMETRY,
                "message": message, "clear_command_queue": clear_queue,
                "http_status": status, "response": resp, "via": "gui",
                **({"attempt": attempt} if attempt > 1 else {}),
            })
            cur = self.store.get(cmd_id)
            if attempt > 1 and cur is not None and \
                    cur["state"] not in lc.IN_FLIGHT_STATES:
                # ack (or terminal verdict) landed while this retry's POST
                # was on the wire — keep it; the duplicate send is
                # harmless (daemon dedupe) and stays in the send log.
                return {"cmd_id": cmd_id, "state": cur["state"],
                        "http_status": status, "response": resp}
            self.store.record_sent(cmd_id, spotter_id, node_id, c,
                                   None if c == "ping" else
                                   (int(v) if v is not None else None),
                                   message, status, resp, attempt=attempt,
                                   cleared_queue=clear_queue)
            return {"cmd_id": cmd_id, "state": self.store.get(cmd_id)["state"],
                    "http_status": status, "response": resp}

    def unwedge(self, spotter_id, node_id):
        """Recovery for a wedged cloud mailbox: clear_command_queue plus a
        fresh ping probe in ONE call (proven 2026-07-31). The probe's ack
        (or its console echo) confirms delivery resumed."""
        token = os.environ.get(ssc.TOKEN_ENV)
        if not token:
            return {"error": f"server started without {ssc.TOKEN_ENV} set"}
        with self.lock:
            last = ssc.load_last_success_ts(self.send_log, spotter_id)
            now = time.time()
            if last is not None and now - last < ssc.RATE_LIMIT_S:
                return {"error": "rate_limited",
                        "retry_in_s": int(ssc.RATE_LIMIT_S - (now - last)) + 1}
            cmd_id = self.store.next_command_id(
                extra_used=self._send_log_ids())
            payload = ssc.build_command_json(cmd_id, "ping", None)
            message = ssc.build_console_line(payload)
        return self._post_and_record(spotter_id, node_id, "ping", None,
                                     cmd_id, message, token, attempt=1,
                                     clear_queue=True)

    def cancel(self, cmd_id):
        """Cancel a wake-scheduled command before it fires."""
        with self.lock:
            cmd = self.store.get(cmd_id)
            if cmd is None:
                return {"error": f"unknown command id {cmd_id}"}
            if cmd["state"] != lc.SCHEDULED:
                return {"error": f"command {cmd_id} is {cmd['state']}, "
                                 "only scheduled sends can be cancelled"}
            self.store.record_cancelled(cmd_id)
            return {"cmd_id": cmd_id, "state": lc.CANCELLED}

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
                for _ts, ack, node_id in acks:
                    seen += 1
                    cid = ack["id"]
                    cmd = self.store.get(cid)
                    if cmd is not None and cmd["state"] in lc.IN_FLIGHT_STATES:
                        self.store.record_ack(cid, ack, node_id)
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

    # -- wake watcher + retry engine --------------------------------------

    @staticmethod
    def _norm_utc(iso):
        """Truncate an ISO timestamp to whole-second 'YYYY-MM-DDTHH:MM:SSZ'
        so sensor-data rows (with .mmm) compare lexically against ours."""
        if not iso:
            return None
        return iso[:19] + "Z"

    def check_wakes(self):
        """Fire wake-scheduled commands whose target has a sensor-data row
        NEWER than the arm time (unit awake and transmitting — the
        post-burst mailbox drain then lands in the daemon's listen tail)."""
        token = os.environ.get(ssc.TOKEN_ENV)
        with self.lock:
            spotters = sorted({c["spotter_id"]
                               for c in self.store.scheduled()})
        if not spotters or not token:
            return
        err = None
        for spotter in spotters:
            try:
                latest = spa.fetch_latest_row_utc(spotter, token, hours=3.0)
            except OSError as e:
                err = str(e)
                continue
            norm = self._norm_utc(latest)
            with self.lock:
                self.wake_rows[spotter] = norm
                due = [dict(c) for c in self.store.scheduled(spotter)
                       if norm and norm > self._norm_utc(c["scheduled_utc"])]
                last = ssc.load_last_success_ts(self.send_log, spotter)
            if not due:
                continue
            if last is not None and time.time() - last < ssc.RATE_LIMIT_S:
                continue  # rate-limited; next tick retries the fire
            cmd = due[0]  # one per tick per spotter (Sofar: 1 send/min)
            self._post_and_record(spotter, cmd.get("node_id"), cmd.get("c"),
                                  cmd.get("v"), cmd["cmd_id"],
                                  cmd["message"], token, attempt=1)
        with self.lock:
            self.last_wake_check = {
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "error": err,
            }

    def check_retries(self):
        """Re-send unacked commands (same id — daemon dedupe makes this
        idempotent) after retry_after_s; give up loudly at max_attempts."""
        token = os.environ.get(ssc.TOKEN_ENV)
        if not token:
            return
        with self.lock:
            overdue = [dict(c) for c in self.store.in_flight()
                       if c.get("last_attempt_ts") is not None
                       and time.time() - c["last_attempt_ts"]
                       >= self.retry_after_s]
        for cmd in overdue:
            attempts = cmd.get("attempt", 1)
            with self.lock:
                cur = self.store.get(cmd["cmd_id"])
                if cur is None or cur["state"] not in lc.IN_FLIGHT_STATES:
                    continue  # acked/failed while we looked
                if attempts >= self.max_attempts:
                    self.store.record_retry_exhausted(cmd["cmd_id"], attempts)
                    continue
                last = ssc.load_last_success_ts(self.send_log,
                                                cmd["spotter_id"])
            if last is not None and time.time() - last < ssc.RATE_LIMIT_S:
                continue  # rate-limited; next tick
            self._post_and_record(cmd["spotter_id"], cmd.get("node_id"),
                                  cmd.get("c"), cmd.get("v"), cmd["cmd_id"],
                                  cmd["message"], token, attempt=attempts + 1)

    def worker_loop(self):
        last_wake_poll = 0.0
        while True:
            time.sleep(20)
            try:
                if time.time() - last_wake_poll >= self.wake_poll_s:
                    last_wake_poll = time.time()
                    self.check_wakes()
                self.check_retries()
            except Exception as e:  # keep the worker alive, loudly
                with self.lock:
                    self.last_wake_check = {"utc": None,
                                            "error": f"worker crashed: {e}"}

    def status(self):
        with self.lock:
            cmds = []
            for c in self.store.all_commands():
                out = {k: v for k, v in c.items() if k != "history"}
                if c["state"] in lc.IN_FLIGHT_STATES and \
                        c.get("last_attempt_ts") is not None:
                    out["next_retry_in_s"] = max(
                        0, int(c["last_attempt_ts"] + self.retry_after_s
                               - time.time()))
                cmds.append(out)
            return {
                "commands": cmds,
                "in_flight": [c["cmd_id"] for c in self.store.in_flight()],
                "scheduled": [c["cmd_id"] for c in self.store.scheduled()],
                "last_poll": self.last_poll,
                "last_wake_check": self.last_wake_check,
                "wake_rows": self.wake_rows,
                "retry_after_s": self.retry_after_s,
                "max_attempts": self.max_attempts,
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
                                 bool(req.get("override_in_flight")),
                                 req.get("mode", "wake"))
                self._json(out, 200 if "error" not in out else 409)
            elif self.path == "/api/unwedge":
                if "spotter_id" not in req:
                    return self._json({"error": "missing spotter_id"}, 400)
                out = state.unwedge(req["spotter_id"], req.get("node_id"))
                self._json(out, 200 if "error" not in out else 409)
            elif self.path == "/api/cancel":
                if "cmd_id" not in req:
                    return self._json({"error": "missing cmd_id"}, 400)
                out = state.cancel(int(req["cmd_id"]))
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
    ap.add_argument("--retry-after-min", type=float, default=40.0,
                    help="re-send an unacked command after this long "
                         "(default 40 min ≈ one duty cycle + margin)")
    ap.add_argument("--max-attempts", type=int, default=4,
                    help="give up (retry_exhausted) after this many sends")
    ap.add_argument("--wake-poll-s", type=int, default=90,
                    help="how often the wake watcher checks sensor-data "
                         "for fresh uplink rows")
    ap.add_argument("--send-log", default=ssc.SEND_LOG,
                    help=argparse.SUPPRESS)  # bench: share another
    ap.add_argument("--gui-log", default=GUI_LOG,
                    help=argparse.SUPPRESS)  # checkout's audit logs
    args = ap.parse_args(argv)

    if not os.environ.get(ssc.TOKEN_ENV):
        print(f"[ERROR] set {ssc.TOKEN_ENV} before starting the GUI.")
        return 2

    state = GuiState(args.targets, gui_log=args.gui_log,
                     send_log=args.send_log, poll_s=args.poll_s,
                     retry_after_s=int(args.retry_after_min * 60),
                     max_attempts=args.max_attempts,
                     wake_poll_s=args.wake_poll_s)
    threading.Thread(target=state.poll_loop, daemon=True).start()
    threading.Thread(target=state.worker_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(state))
    print(f"[gui] serving http://127.0.0.1:{args.port}  "
          f"(targets: {len(state.targets)}, tables v{ct.TABLES_VERSION}, "
          f"ack poll {args.poll_s}s, wake poll {args.wake_poll_s}s, "
          f"retry after {args.retry_after_min:g} min × "
          f"{args.max_attempts} attempts)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[gui] stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
