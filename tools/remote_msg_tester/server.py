#!/usr/bin/env python3
"""
Remote message latency tester (Sprint23) — local server + one HTML page.

Purpose
    Send ONE console line to a Spotter through the Sofar Command API, then
    watch that Spotter's USB console log until the Ebox prints
    "Remote message received", and report how long it took.

Inputs
    Browser form: Spotter ID, API key (optional), command line.
    Console log written by tools/spotter_serial_monitor.py:
        <log-root>/<SPOT-ID>/console_YYYYMMDD.log   (UTC day files)
    Token: env var SOFAR_API_TOKEN_BM_REEF unless a key is typed in the page.

Outputs (all under runs/remote_msg_latency/)
    latency_log.jsonl      append-only event log: sent / received / released
                           / abandoned, one JSON object per line
    latency_summary.csv    one row per test, rewritten on every change
    excerpts/<test_id>.log raw console lines from the receive line onward
    runs/sofar_command_sends.jsonl also gets the send record, so the CLI
    sender's rate-limit guard and this tool see each other's sends.

Assumptions
    - The serial monitor is already running for that Spotter. This tool never
      opens a serial port. A stale console log blocks the send, because a
      test nobody is watching cannot be timed.
    - Latency = receive line's HOST timestamp (written by the monitor, 1 s
      resolution) minus the send time. Both come from this Mac's clock.
    - Receive signature, seen on v2.16.6 and v2.16.8:
        [SYS] [INFO] Remote message received(N)! "<message>
        id:NNNNN
        "
      Sofar appends the id line. The bus-release signature of the v2.16.8
      queue feature is NOT known yet; set "released_regex" in signatures.json
      once Sprint23 Phase 2b has observed it. Until then a test ends at
      "received".

Example
    python3 tools/remote_msg_tester/server.py --log-root ~/spotter_logs
    open http://127.0.0.1:8771

    Offline check, no network and no Spotter:
    python3 tools/remote_msg_tester/server.py --log-root /tmp/fake \
        --fake-send --out-dir /tmp/fake_out

Known limitations
    - One test in flight per Spotter (keeps matching unambiguous and avoids
      the many-pending-commands state that wedged the mailbox in July).
    - Sofar allows 1 successful request/min/Spotter; enforced here too.
    - The token travels browser -> this server over plain HTTP, which is why
      the server binds 127.0.0.1 only. It is never logged or echoed back.
"""

import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import sofar_send_command as sender  # noqa: E402  (reuse, do not fork)

OUT_DIR = os.path.join(REPO_ROOT, "runs", "remote_msg_latency")
EVENT_LOG = os.path.join(OUT_DIR, "latency_log.jsonl")
SUMMARY_CSV = os.path.join(OUT_DIR, "latency_summary.csv")
EXCERPT_DIR = os.path.join(OUT_DIR, "excerpts")
SIGNATURES = os.path.join(HERE, "signatures.json")

MONITOR_STALE_S = 60      # a live Spotter prints `power |` every ~10 s
EXCERPT_LINES = 60        # console lines kept after the receive line
SPOTTER_ID_RE = re.compile(r"^SPOT-[0-9A-Za-z]+$")
LOG_LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) ?(.*)$")
FW_BANNER_RE = re.compile(r"\|_\| (v\d+\.\d+\.\d+)")
SOFAR_ID_RE = re.compile(r"^id:(\d+)\s*$")
# Lines that reboot the Spotter or cut bus power. A reboot mid-cycle is a
# hard power cut for the camera Pi (SD-corruption risk), and a queued reboot
# would re-fire on every retry. Blocked unless the operator ticks override.
REBOOT_CLASS_RE = re.compile(
    r"\b(reset|reboot|cfg\s+save|commit|dfu|bootloader|factory)\b", re.I)

CSV_FIELDS = ["test_id", "spotter_id", "message", "clear_command_queue",
              "sent_utc", "http_status", "status", "received_utc",
              "latency_s", "released_utc", "release_latency_s",
              "sofar_msg_id", "fw_version", "token_source", "fake_send",
              "matched_console_line"]


def set_out_dir(path):
    """Point every output at another folder (offline checks use a scratch
    dir so fake tests never land in the real latency log)."""
    global OUT_DIR, EVENT_LOG, SUMMARY_CSV, EXCERPT_DIR
    OUT_DIR = os.path.abspath(os.path.expanduser(path))
    EVENT_LOG = os.path.join(OUT_DIR, "latency_log.jsonl")
    SUMMARY_CSV = os.path.join(OUT_DIR, "latency_summary.csv")
    EXCERPT_DIR = os.path.join(OUT_DIR, "excerpts")


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)


def load_signatures():
    with open(SIGNATURES, "r", encoding="utf-8") as f:
        sig = json.load(f)
    received = re.compile(sig["received_regex"])
    released = re.compile(sig["released_regex"]) if sig.get(
        "released_regex") else None
    return received, released


class Store:
    """Append-only event log, folded into one record per test_id."""

    def __init__(self):
        self.lock = threading.Lock()
        self.records = {}   # test_id -> dict, insertion ordered
        os.makedirs(EXCERPT_DIR, exist_ok=True)
        if os.path.exists(EVENT_LOG):
            with open(EVENT_LOG, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        self._fold(json.loads(line))
                    except ValueError:
                        continue  # torn tail line
        print(f"[store] {len(self.records)} past tests from {EVENT_LOG}")

    def _fold(self, ev):
        rec = self.records.setdefault(ev["test_id"], {})
        rec.update({k: v for k, v in ev.items() if k != "event"})

    def append(self, ev):
        with self.lock:
            with open(EVENT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, separators=(",", ":")) + "\n")
            self._fold(ev)
            self._write_csv()

    def _write_csv(self):
        tmp = SUMMARY_CSV + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            w.writeheader()
            for rec in self.records.values():
                w.writerow(rec)
        os.replace(tmp, SUMMARY_CSV)

    def pending(self, spotter_id=None):
        with self.lock:
            return [dict(r) for r in self.records.values()
                    if r.get("status") in ("sent", "received_waiting_release")
                    and (spotter_id is None or r["spotter_id"] == spotter_id)]

    def recent(self, n=50):
        with self.lock:
            return [dict(r) for r in list(self.records.values())[-n:]][::-1]

    def last_success_ts(self, spotter_id):
        with self.lock:
            ts = [r["sent_ts"] for r in self.records.values()
                  if r["spotter_id"] == spotter_id
                  and r.get("http_status") == 202 and not r.get("fake_send")]
        return max(ts) if ts else None


class ConsoleLogs:
    """Read-only view of the serial monitor's per-Spotter day files."""

    def __init__(self, log_root):
        self.log_root = os.path.expanduser(log_root)

    def day_file(self, spotter_id, day):
        return os.path.join(self.log_root, spotter_id,
                            f"console_{day.strftime('%Y%m%d')}.log")

    def newest_file(self, spotter_id):
        files = sorted(glob.glob(
            os.path.join(self.log_root, spotter_id, "console_*.log")))
        return files[-1] if files else None

    def age_s(self, spotter_id):
        f = self.newest_file(spotter_id)
        return None if f is None else max(0.0, time.time() - os.path.getmtime(f))

    def fw_version(self, spotter_id):
        """Most recent firmware banner in the newest 3 day files, or None."""
        files = sorted(glob.glob(
            os.path.join(self.log_root, spotter_id, "console_*.log")))[-3:]
        for path in reversed(files):
            last = None
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = FW_BANNER_RE.search(line)
                    if m:
                        last = m.group(1)
            if last:
                return last
        return None


class Watcher(threading.Thread):
    """Tails the console log for each pending test. Never touches serial."""

    def __init__(self, store, logs):
        super().__init__(daemon=True)
        self.store, self.logs = store, logs
        self.cursor = {}    # test_id -> [path, offset]
        self.excerpt = {}   # test_id -> lines still to copy after receive

    def run(self):
        while True:
            try:
                for rec in self.store.pending():
                    self._advance(rec)
                self._drain_excerpts()
            except Exception as e:  # keep the watcher alive, but loudly
                print(f"[watcher] ERROR {type(e).__name__}: {e}")
            time.sleep(1)

    def _read_new_lines(self, test_id, rec):
        path, offset = self.cursor.setdefault(
            test_id, [rec["log_file"], rec["log_offset"]])
        lines = []
        if os.path.exists(path):
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read()
            end = chunk.rfind(b"\n") + 1   # only whole lines
            lines = chunk[:end].decode("utf-8", "replace").splitlines()
            self.cursor[test_id][1] = offset + end
        # UTC day rollover: move on once a newer day file exists
        newest = self.logs.newest_file(rec["spotter_id"])
        if newest and newest > path and not lines:
            self.cursor[test_id] = [newest, 0]
        return lines

    def _advance(self, rec):
        received_re, released_re = load_signatures()
        test_id = rec["test_id"]
        want = rec["message"].split("\n")[0].strip()
        for line in self._read_new_lines(test_id, rec):
            m = LOG_LINE_RE.match(line)
            host_ts, text = (m.group(1), m.group(2)) if m else (None, line)
            if rec["status"] == "sent":
                hit = received_re.search(text)
                if not (hit and host_ts and hit.group("msg").strip() == want):
                    continue
                latency = (parse_iso(host_ts) -
                           parse_iso(rec["sent_utc"])).total_seconds()
                expects_release = bool(released_re) and want.startswith("bm ")
                rec["status"] = ("received_waiting_release"
                                 if expects_release else "received")
                self.store.append({
                    "event": "received", "test_id": test_id,
                    "status": rec["status"], "received_utc": host_ts,
                    "latency_s": latency, "matched_console_line": text,
                    "received_bytes": int(hit.group("n"))})
                self.excerpt[test_id] = EXCERPT_LINES
                self._excerpt_write(test_id, line)
                print(f"[watcher] {test_id} RECEIVED after {latency:.0f} s")
            else:
                self._excerpt_line(test_id, rec, line, text)
                if (rec["status"] == "received_waiting_release"
                        and released_re and released_re.search(text)):
                    rel = (parse_iso(host_ts) -
                           parse_iso(rec["sent_utc"])).total_seconds()
                    rec["status"] = "released"
                    self.store.append({
                        "event": "released", "test_id": test_id,
                        "status": "released", "released_utc": host_ts,
                        "release_latency_s": rel,
                        "released_console_line": text})
                    print(f"[watcher] {test_id} RELEASED after {rel:.0f} s")

    def _excerpt_write(self, test_id, line):
        with open(os.path.join(EXCERPT_DIR, f"{test_id}.log"), "a",
                  encoding="utf-8") as f:
            f.write(line + "\n")

    def _excerpt_line(self, test_id, rec, line, text):
        if self.excerpt.get(test_id, 0) <= 0:
            return
        self.excerpt[test_id] -= 1
        self._excerpt_write(test_id, line)
        m = SOFAR_ID_RE.match(text)
        if m and not rec.get("sofar_msg_id"):
            rec["sofar_msg_id"] = m.group(1)
            self.store.append({"event": "sofar_id", "test_id": test_id,
                               "sofar_msg_id": m.group(1)})

    def _drain_excerpts(self):
        """Finished tests still owe their post-receive console excerpt."""
        for test_id, left in list(self.excerpt.items()):
            if left <= 0:
                self.excerpt.pop(test_id)
                continue
            rec = self.store.records.get(test_id)
            if rec and rec.get("status") in ("received", "released"):
                for line in self._read_new_lines(test_id, rec):
                    m = LOG_LINE_RE.match(line)
                    self._excerpt_line(test_id, rec, line,
                                       m.group(2) if m else line)


class App:
    def __init__(self, args):
        self.args = args
        self.logs = ConsoleLogs(args.log_root)
        self.store = Store()
        self.send_lock = threading.Lock()
        load_signatures()  # fail at startup, not mid-test
        Watcher(self.store, self.logs).start()

    def cooldown_s(self, spotter_id):
        """Seconds until Sofar will accept another request for this Spotter.
        Looks at this tool's log AND the CLI sender's log."""
        last = [t for t in (
            self.store.last_success_ts(spotter_id),
            sender.load_last_success_ts(sender.SEND_LOG, spotter_id)) if t]
        if not last:
            return 0
        return max(0, int(sender.RATE_LIMIT_S - (time.time() - max(last)) + 1))

    def status(self, spotter_id):
        ok_id = bool(SPOTTER_ID_RE.match(spotter_id or ""))
        age = self.logs.age_s(spotter_id) if ok_id else None
        _, released_re = load_signatures()
        return {
            "now_utc": iso(utc_now()),
            "monitor_log_age_s": age,
            "monitor_ok": age is not None and age < MONITOR_STALE_S,
            "cooldown_s": self.cooldown_s(spotter_id) if ok_id else 0,
            "token_env_name": sender.TOKEN_ENV,
            "token_env_set": bool(os.environ.get(sender.TOKEN_ENV)),
            "release_signature_known": released_re is not None,
            "fake_send": self.args.fake_send,
            "records": self.store.recent(),
        }

    def send(self, req):
        spotter_id = (req.get("spotter_id") or "").strip().upper()
        message = (req.get("message") or "").strip()
        clear_queue = bool(req.get("clear_command_queue"))
        api_key = (req.get("api_key") or "").strip()
        if not SPOTTER_ID_RE.match(spotter_id):
            return 400, "Spotter ID must look like SPOT-31593C"
        if not message:
            return 400, "command is empty"
        if "\n" in message:
            return 400, "one console line per test (no newlines)"
        try:
            sender.validate_message(message)
        except ValueError as e:
            return 400, str(e)
        if REBOOT_CLASS_RE.search(message) and not req.get("allow_reboot_class"):
            return 400, ("blocked: this looks like a reboot/commit-class "
                         "command. Tick the override box only if you mean it.")
        token = api_key or os.environ.get(sender.TOKEN_ENV, "")
        if not token and not self.args.fake_send:
            return 400, (f"no API key: type one, or set {sender.TOKEN_ENV} "
                         "in the environment that starts this server")
        with self.send_lock:
            if self.store.pending(spotter_id):
                return 409, ("a test is already in flight for this Spotter. "
                             "Wait for it, or abandon it first.")
            wait = self.cooldown_s(spotter_id)
            if wait and not self.args.fake_send:
                return 429, f"Sofar rate limit: wait {wait} s"
            age = self.logs.age_s(spotter_id)
            if age is None or age >= MONITOR_STALE_S:
                return 409, ("console log for this Spotter is missing or "
                             f"stale (age {age}). Start the serial monitor: "
                             "python3 tools/spotter_serial_monitor.py "
                             f"--log-root {self.args.log_root} "
                             f"--only {spotter_id}")
            # Cursor BEFORE the POST, so a fast delivery cannot be missed.
            log_file = self.logs.newest_file(spotter_id)
            log_offset = os.path.getsize(log_file)
            body = {"telemetry": sender.TELEMETRY, "message": message}
            if clear_queue:
                body["clear_command_queue"] = True
            sent = utc_now()
            if self.args.fake_send:
                status, resp = 202, {"status": "fake", "message": "no network"}
            else:
                status, resp = sender.post_command(spotter_id, token, body)
            resp_txt = json.dumps(resp) if not isinstance(resp, str) else resp
            if token:
                resp_txt = resp_txt.replace(token, "<token>")
            test_id = f"{sent.strftime('%Y%m%dT%H%M%SZ')}_{spotter_id}"
            ev = {
                "event": "sent", "test_id": test_id, "spotter_id": spotter_id,
                "message": message, "clear_command_queue": clear_queue,
                "sent_utc": iso(sent), "sent_ts": sent.timestamp(),
                "http_status": status, "api_response": resp_txt,
                "status": "sent" if status == 202 else "send_failed",
                "token_source": "ui" if api_key else "env",
                "fake_send": self.args.fake_send,
                "fw_version": self.logs.fw_version(spotter_id),
                "log_file": log_file, "log_offset": log_offset,
            }
            self.store.append(ev)
            if not self.args.fake_send:
                sender.append_send_log(sender.SEND_LOG, {
                    "ts": sent.timestamp(), "utc": iso(sent),
                    "spotter_id": spotter_id, "telemetry": sender.TELEMETRY,
                    "message": message, "clear_command_queue": clear_queue,
                    "http_status": status, "response": resp_txt,
                    "via": "remote_msg_tester"})
        print(f"[send] {test_id} HTTP {status} {message!r}")
        if status != 202:
            return 502, f"Sofar said HTTP {status}: {resp_txt}"
        return 200, test_id

    def abandon(self, req):
        test_id = req.get("test_id", "")
        rec = self.store.records.get(test_id)
        if not rec or rec.get("status") not in ("sent",
                                                 "received_waiting_release"):
            return 400, "no such pending test"
        self.store.append({"event": "abandoned", "test_id": test_id,
                           "status": "abandoned",
                           "abandoned_utc": iso(utc_now())})
        return 200, ("abandoned locally. The command may STILL be queued at "
                     "Sofar; send with 'clear queue' ticked to drop it.")


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):  # quiet; request bodies hold the key
            pass

        def _reply(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/":
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._reply(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/status":
                sid = ""
                for part in query.split("&"):
                    if part.startswith("spotter_id="):
                        sid = part.split("=", 1)[1].strip().upper()
                return self._reply(200, json.dumps(app.status(sid)))
            if path == "/latency_summary.csv" and os.path.exists(SUMMARY_CSV):
                with open(SUMMARY_CSV, "rb") as f:
                    return self._reply(200, f.read(), "text/csv")
            self._reply(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            # Same-origin only: a web page elsewhere must not be able to
            # drive sends through the operator's browser.
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{app.args.port}",
                                         f"http://localhost:{app.args.port}"):
                return self._reply(403, json.dumps({"error": "bad origin"}))
            try:
                n = int(self.headers.get("Content-Length", "0"))
                req = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._reply(400, json.dumps({"error": "bad JSON"}))
            if self.path == "/api/send":
                code, msg = app.send(req)
            elif self.path == "/api/abandon":
                code, msg = app.abandon(req)
            else:
                code, msg = 404, "not found"
            key = "result" if code == 200 else "error"
            self._reply(code, json.dumps({key: msg}))
    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log-root", default="~/spotter_logs",
                    help="serial monitor log root (default ~/spotter_logs)")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--fake-send", action="store_true",
                    help="no network: pretend Sofar returned 202. For offline "
                         "checks against a replayed console log.")
    ap.add_argument("--out-dir", default=OUT_DIR,
                    help="where the latency log/CSV/excerpts go")
    args = ap.parse_args()
    if args.fake_send and os.path.abspath(
            os.path.expanduser(args.out_dir)) == OUT_DIR:
        raise SystemExit("--fake-send needs its own --out-dir, so fake tests "
                         "never mix into the real latency log")
    set_out_dir(args.out_dir)
    app = App(args)
    print(f"[tester] log root : {os.path.expanduser(args.log_root)}")
    print(f"[tester] outputs  : {OUT_DIR}")
    print(f"[tester] token env: {sender.TOKEN_ENV} "
          f"{'SET' if os.environ.get(sender.TOKEN_ENV) else 'NOT SET'}")
    if args.fake_send:
        print("[tester] *** FAKE SEND MODE — nothing reaches Sofar ***")
    print(f"[tester] open http://127.0.0.1:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
