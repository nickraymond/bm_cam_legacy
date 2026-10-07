#!/usr/bin/env python3
"""hil_g4_alternator.py — G4 (Nick 2026-10-02): alternate still <-> video hourly on the bench rigs through
the R1 PRODUCT path (backend remote-config, Sofar lane), and send the D3 `trg` commands, unattended.

Runs ON nereus000 (the admin token never leaves it) from a systemd timer at :20 every hour, i.e. while
the units are halted (bus windows :00–:10), so the command waits for the next wake.

Each run, per device:
  1. target media for the NEXT wake (UTC hour h+1): `still` when h+1 is even, `video` when odd.
  2. POST /devices/{d}/remote-config/changes {"set":{"mode.media":target},"lane":"sofar","supersede":true}
     -> POST /admin/devices/{d}/commands/{cid}/send. 409 rate_limited -> wait retry_after_s, retry the
     SAME id (max 5). Every request and answer is one JSON line in --log.
  3. if this hour is a D3 hour (--trg-hours), POST /admin/devices/{d}/commands {"c":"trg","v":2,
     "lane":"sofar"} -> /send, with the same retry rule. Sent >= 70 s after the media change (one send
     per 65 s per Spotter, shared with heals).
Safety: ONLY BMCAM_003 / BMCAM_004 (hard allow-list, the bench rigs). Plan + read + these two verbs only.
--dry-run calls /remote-config/plan instead of /changes and sends nothing.

Inputs:  --api, --env-file (ADMIN_TOKEN=...), --devices BMCAM_003,BMCAM_004, --trg-hours 6,10,14 (UTC
         hours whose :20 run also sends a trg), --log /home/pi/hil_g4/alternator.jsonl, --dry-run,
         --until 2026-10-03T16:00 (no sends at/after this UTC time: the G4 end)
Outputs: the JSON-lines log; exit 0 (all sends accepted or dry run), 1 (any send failed).
Example: python3 hil_g4_alternator.py --api https://nereus-vision-staging.onrender.com --dry-run
Limits:  "commanded" ≠ "in effect": the effect is measured from the START type of later wakes (analysis);
         this script does not read the units. Stateless: the target comes from the clock.
"""

import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.request

ALLOWED = {"BMCAM_003", "BMCAM_004"}


def read_token(path):
    for line in open(path, encoding="utf-8"):
        k, _, v = line.strip().partition("=")
        if k == "ADMIN_TOKEN" and v:
            return v.strip().strip("'\"")
    raise SystemExit(f"ADMIN_TOKEN not found in {path}")


class Api:
    def __init__(self, base, token, log):
        self.base, self.token, self.log = base.rstrip("/"), token, log

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                st, out = r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            t = e.read().decode("utf-8", "replace")
            try:
                st, out = e.code, json.loads(t)
            except ValueError:
                st, out = e.code, t
        except Exception as e:
            st, out = 0, f"{type(e).__name__}: {e}"
        self.log({"call": f"{method} {path}", "body": body, "status": st, "answer": out})
        return st, out


def send_with_retry(api, dev, cid, tries=5):
    for _ in range(tries):
        st, out = api.call("POST", f"/admin/devices/{dev}/commands/{cid}/send")
        if st == 409 and isinstance(out, dict) and (out.get("detail") or {}).get("reason") == "rate_limited":
            time.sleep(int((out.get("detail") or {}).get("retry_after_s") or 65) + 2)
            continue
        return st, out
    return st, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", required=True)
    ap.add_argument("--env-file", default="/home/pi/.config/nereus/heal_driver.env")
    ap.add_argument("--devices", default="BMCAM_003,BMCAM_004")
    ap.add_argument("--trg-hours", default="")
    ap.add_argument("--log", default="/home/pi/hil_g4/alternator.jsonl")
    ap.add_argument("--until", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    devs = [d.strip() for d in a.devices.split(",") if d.strip()]
    bad = [d for d in devs if d not in ALLOWED]
    if bad:
        raise SystemExit(f"[alternator] REFUSED: {bad} not in the bench allow-list {sorted(ALLOWED)}")
    now = datetime.datetime.now(datetime.timezone.utc)
    if a.until and now >= datetime.datetime.fromisoformat(a.until).replace(tzinfo=datetime.timezone.utc):
        print(f"[alternator] past --until {a.until}: nothing sent"); return 0
    run_id = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    fh = open(a.log, "a")

    def log(rec):
        rec = dict(rec, run=run_id, t=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   dry_run=a.dry_run)
        fh.write(json.dumps(rec) + "\n"); fh.flush()

    api = Api(a.api, read_token(a.env_file), log)
    next_hour = (now + datetime.timedelta(hours=1)).hour
    target = "still" if next_hour % 2 == 0 else "video"
    trg_now = str(now.hour) in [h.strip() for h in a.trg_hours.split(",") if h.strip()]
    failed = False
    for dev in devs:
        body = {"set": {"mode.media": target}, "lane": "sofar", "supersede": True}
        if a.dry_run:
            st, out = api.call("POST", f"/devices/{dev}/remote-config/plan", body)
            print(f"[alternator] DRY {dev} -> {target} for wake {next_hour:02d}:00Z: plan {st} "
                  f"ok={out.get('ok') if isinstance(out, dict) else out}")
            continue
        st, out = api.call("POST", f"/devices/{dev}/remote-config/changes", body)
        cid = out.get("command_id") if isinstance(out, dict) else None
        if not cid:
            print(f"[alternator] {dev} change REFUSED {st}: {str(out)[:200]}"); failed = True; continue
        st2, out2 = send_with_retry(api, dev, cid)
        log({"event": "media_command", "device": dev, "target": target, "for_wake_utc_hour": next_hour,
             "command_id": cid, "send_status": st2})
        print(f"[alternator] {dev} mode.media={target} for {next_hour:02d}:00Z: cid {cid} send {st2}")
        failed |= st2 not in (200, 202)
    if trg_now:
        time.sleep(0 if a.dry_run else 70)
        for dev in devs:
            if a.dry_run:
                print(f"[alternator] DRY {dev} would send trg now"); continue
            st, out = api.call("POST", f"/admin/devices/{dev}/commands", {"c": "trg", "v": 2, "lane": "sofar"})
            cid = out.get("command_id") if isinstance(out, dict) else None
            if not cid:
                print(f"[alternator] {dev} trg REFUSED {st}: {str(out)[:200]}"); failed = True; continue
            st2, out2 = send_with_retry(api, dev, cid)
            log({"event": "trg_command", "device": dev, "command_id": cid, "send_status": st2})
            print(f"[alternator] {dev} trg cid {cid} send {st2}")
            failed |= st2 not in (200, 202)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
