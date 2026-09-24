#!/usr/bin/env python3
# filename: bm_heal_driver.py
# description: Sprint25 S5 ladder 3 — operator-side heal loop on the console-monitor Pi (nereus000).
"""
Heal driver — the operator's half of self-healing media, automated for a bench soak.

Runs next to spotter_serial_monitor.py (same Pi, same log root). For every rig
(Spotter -> backend device), on every camera wake seen on the Spotter console
(`Bridge bus power: 1`):

  1. check the outstanding heal command (if any): every media it names complete
     at the backend (GET /admin/ingest/media/{id}/missing -> 409 complete|nothing_missing)
     -> log `healed` and clear it; published on HEAL_WAKES wakes without finishing
     -> log `expired` and clear it (the unit drops it after 3 wakes too); every
     unfinished media answers 409 `still_arriving` (chunks landing at the backend)
     -> log `waiting`, publish nothing, do not count the wake (at most MAX_WAIT_WAKES)
  2. no outstanding command -> GET heal-candidates; if any, POST heal-commands
     (backend allocates the id + packs <= 40 chunks) -> new outstanding command
  3. publish the outstanding command's console line (`bm pub bmcam/cmd {...} 1 1`)
     through the monitor's cmd.txt at bus-on + PUBLISH_OFFSETS_S. The unit plans
     its heals ~+50 s after bus-on, so the early publishes heal THIS wake; the
     late one lands in the listen tail and heals the next wake. Repeats are safe:
     the unit dedupes by command id and acks the duplicate.

Nothing here talks to Sofar (console path only, Nick 2026-09-24: "console until
thoroughly vetted"). Everything is appended to <log-root>/heal_driver/events.jsonl.

Inputs
  --log-root   the monitor's log root (default /home/pi/spotter_logs)
  --rig        SPOT-ID=DEVICE_ID, repeatable (e.g. SPOT-31593C=BMCAM_004)
  --env-file   file holding ADMIN_TOKEN=... (mode 600; never logged)
  --api        backend base URL (default staging)

Example (nereus000):
  python3 bm_heal_driver.py --rig SPOT-31593C=BMCAM_004 --rig SPOT-33507C=BMCAM_003

Known limitations: wake detection is the console's bus-power line only; a Spotter
reset that re-powers the bus also counts as a wake. Backend candidates lag a wake
by ~20 min (Notecard sync + ingest), so a clip is typically healed 2 wakes later.
"""

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request

WAKE_MARKER = "Bridge bus power: 1"
PUBLISH_OFFSETS_S = (30, 40, 50, 260)
HEAL_WAKES = 3
DEFAULT_API = "https://nereus-vision-staging.onrender.com"
DONE_REASONS = ("complete", "nothing_missing")
# Backend says chunks for the media are still landing (receive-time rule, nvd BUGS.md B19):
# the heal we published may have just arrived, or the original tail is arriving late from
# Sofar. Publishing again now would only re-send bytes that are already in flight, so the
# wake is not counted against HEAL_WAKES and nothing is published. Bounded: after
# MAX_WAIT_WAKES consecutive waits the command is treated as a normal wake again.
STILL_ARRIVING = "still_arriving"
MAX_WAIT_WAKES = 2


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_token(env_file):
    with open(env_file, "r", encoding="utf-8") as fh:
        for line in fh:
            key, _, value = line.strip().partition("=")
            if key == "ADMIN_TOKEN" and value:
                return value.strip().strip("'\"")
    raise SystemExit(f"ADMIN_TOKEN not found in {env_file}")


class Backend:
    def __init__(self, api, token):
        self.api = api.rstrip("/")
        self.token = token

    def call(self, method, path, body=None):
        """(status, json-or-text). Never raises on HTTP errors."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(text)
            except ValueError:
                return exc.code, text
        except Exception as exc:
            return 0, f"{type(exc).__name__}: {exc}"


class Driver:
    def __init__(self, log_root, rigs, backend):
        self.log_root = log_root
        self.rigs = rigs                      # {spotter_id: device_id}
        self.backend = backend
        self.dir = os.path.join(log_root, "heal_driver")
        os.makedirs(self.dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        self.lock = threading.Lock()
        self.state = self._load()

    # --- persistence / logging ------------------------------------------------------
    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1)
        os.replace(tmp, self.state_path)

    def event(self, spotter, kind, **fields):
        rec = {"utc": utc_now(), "spotter": spotter, "device": self.rigs.get(spotter), "event": kind, **fields}
        line = json.dumps(rec, default=str)
        print(line, flush=True)
        with self.lock:
            with open(os.path.join(self.dir, "events.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # --- one wake ---------------------------------------------------------------------
    def on_wake(self, spotter, bus_on_mono):
        device = self.rigs[spotter]
        self.event(spotter, "wake")
        cmd = self.state.get(device)
        if cmd:
            status = self._media_status(cmd["media_ids"])
            if all(r in DONE_REASONS for r in status.values()):
                self.event(spotter, "healed", command_id=cmd["command_id"], media=status,
                           wakes_published=cmd["wakes"])
                cmd = None
            elif cmd["wakes"] >= HEAL_WAKES:
                self.event(spotter, "expired", command_id=cmd["command_id"], media=status)
                cmd = None
            elif (all(r in DONE_REASONS or r == STILL_ARRIVING for r in status.values())
                  and cmd.get("waits", 0) < MAX_WAIT_WAKES):
                # Every media not yet done is still receiving chunks at the backend:
                # not a used wake, nothing published; re-check next wake.
                cmd["waits"] = cmd.get("waits", 0) + 1
                self.event(spotter, "waiting", command_id=cmd["command_id"], media=status,
                           waits=cmd["waits"], wakes_published=cmd["wakes"])
                with self.lock:
                    self._save()
                return
        if cmd is None:
            cmd = self._new_command(spotter, device)
        with self.lock:
            self.state[device] = cmd
            self._save()
        if cmd is None:
            return
        cmd["wakes"] += 1
        with self.lock:
            self._save()
        for offset in PUBLISH_OFFSETS_S:
            delay = bus_on_mono + offset - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.publish(spotter, cmd, offset)

    def _media_status(self, media_ids):
        out = {}
        for mid in media_ids:
            code, body = self.backend.call("GET", f"/admin/ingest/media/{mid}/missing")
            if code == 200:
                out[mid] = f"missing {body.get('ranges')}"
            elif code == 409 and isinstance(body, dict):
                out[mid] = (body.get("detail") or {}).get("reason", "409")
            else:
                out[mid] = f"http {code}"
        return out

    def _new_command(self, spotter, device):
        code, body = self.backend.call("GET", f"/admin/ingest/devices/{device}/heal-candidates?hours=24")
        if code != 200:
            self.event(spotter, "backend_error", call="heal-candidates", status=code, body=str(body)[:200])
            return None
        cands = body.get("candidates") or []
        self.event(spotter, "candidates", n=len(cands), skipped=len(body.get("skipped") or []),
                   items=[{"media_id": c["media_id"], "key": c["media_key"], "missing": c["ranges"]} for c in cands])
        if not cands:
            return None
        code, body = self.backend.call("POST", f"/admin/ingest/devices/{device}/heal-commands", {})
        if code != 200:
            self.event(spotter, "backend_error", call="heal-commands", status=code, body=str(body)[:200])
            return None
        self.event(spotter, "command", command_id=body["command_id"], heals=body["heals"],
                   chunks=body["chunks"], console_bytes=body["console_bytes"], left_out=body.get("left_out"))
        return {"command_id": body["command_id"], "console_line": body["console_line"],
                "media_ids": body["media_ids"], "wakes": 0, "created_utc": utc_now()}

    def publish(self, spotter, cmd, offset):
        path = os.path.join(self.log_root, spotter, "cmd.txt")
        try:
            with open(path, "w", encoding="ascii") as fh:
                fh.write(cmd["console_line"] + "\n")
            self.event(spotter, "publish", command_id=cmd["command_id"], offset_s=offset, wake_n=cmd["wakes"])
        except OSError as exc:
            self.event(spotter, "publish_error", error=str(exc))

    # --- console follower ---------------------------------------------------------------
    def follow(self, spotter):
        """Tail today's console log (UTC day rollover aware); a wake runs in its own thread."""
        day, fh = None, None
        while True:
            today = time.strftime("%Y%m%d", time.gmtime())
            if today != day:
                path = os.path.join(self.log_root, spotter, f"console_{today}.log")
                try:
                    new = open(path, "r", encoding="utf-8", errors="replace")
                except OSError:
                    time.sleep(2)
                    continue
                if fh is None:
                    new.seek(0, os.SEEK_END)        # start: only wakes from now on
                if fh:
                    fh.close()
                day, fh = today, new
            line = fh.readline()
            if not line:
                time.sleep(0.5)
                continue
            if WAKE_MARKER in line:
                threading.Thread(target=self._safe_wake, args=(spotter, time.monotonic()),
                                 name=f"wake-{spotter}", daemon=True).start()

    def _safe_wake(self, spotter, bus_on_mono):
        try:
            self.on_wake(spotter, bus_on_mono)
        except Exception as exc:
            self.event(spotter, "driver_error", error=f"{type(exc).__name__}: {exc}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-root", default="/home/pi/spotter_logs")
    ap.add_argument("--rig", action="append", required=True, help="SPOT-ID=DEVICE_ID (repeatable)")
    ap.add_argument("--env-file", default=os.path.expanduser("~/.config/nereus/heal_driver.env"))
    ap.add_argument("--api", default=DEFAULT_API)
    args = ap.parse_args()
    rigs = dict(r.split("=", 1) for r in args.rig)
    driver = Driver(args.log_root, rigs, Backend(args.api, read_token(args.env_file)))
    for spotter in rigs:
        driver.event(spotter, "driver_start", api=args.api, offsets=PUBLISH_OFFSETS_S)
        threading.Thread(target=driver.follow, args=(spotter,), name=f"follow-{spotter}", daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
