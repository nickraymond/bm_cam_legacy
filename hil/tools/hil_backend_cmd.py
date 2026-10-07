#!/usr/bin/env python3
"""hil_backend_cmd.py — send ONE read-only command (get / ping) to a bench device through the backend product path
(POST /admin/devices/{d}/commands → /send, Sofar lane), retrying the same id on 409 rate_limited.

Runs ON the monitor host (nereus000; the admin token never leaves it). Used by R1G's deliberate ack-loss stress
(EM GO 2026-10-04): a third command in the same :20 slot as the hourly set + trg.
Inputs:  --api, --env-file, --device (BMCAM_003 | BMCAM_004), --json '{"c":"get","k":["mode.media"]}' (verbs get /
         ping only), --log (JSON lines)
Outputs: prints "CID <id> send <status>"; appends request/answer lines to --log; exit 0 on a 200/202 send.
Example: python3 /home/pi/hil_r1/hil_backend_cmd.py --api https://nereus-vision-staging.onrender.com
           --device BMCAM_003 --json '{"c":"get","k":["mode.media"]}'
         --refresh: instead of --json, POST /devices/{d}/remote-config/refresh (lane sofar; the catalog's
         refresh_gets, all read-only `get`s) and send each in id order (65 s rate limit handled by the retry).
Limits:  read-only verbs only by default (refuses set/reset/cfm/hld: those go through the remote-config path).
         `--allow-trg` additionally permits ONE `trg` (one-shot capture, e.g. Sprint28 R1 `{"c":"trg","v":2,"kv":{"med":"still"}}`).
"""

import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.request

ALLOWED = {"BMCAM_003", "BMCAM_004"}
VERBS = {"get", "ping"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", required=True)
    ap.add_argument("--env-file", default="/home/pi/.config/nereus/heal_driver.env")
    ap.add_argument("--device", required=True)
    ap.add_argument("--json")
    ap.add_argument("--refresh", action="store_true", help="record + send the backend refresh gets (Sofar lane)")
    ap.add_argument("--log", default="/home/pi/hil_r1/backend_cmd.jsonl")
    ap.add_argument("--allow-trg", action="store_true", help="also allow a one-shot trg command")
    a = ap.parse_args()
    if a.device not in ALLOWED:
        raise SystemExit(f"REFUSED: {a.device} not in {sorted(ALLOWED)}")
    if bool(a.json) == a.refresh:
        raise SystemExit("give exactly one of --json / --refresh")
    body = json.loads(a.json) if a.json else {"c": "get"}
    if body.get("c") not in VERBS | ({"trg"} if a.allow_trg else set()):
        raise SystemExit(f"REFUSED: verb {body.get('c')!r} (read-only verbs only: {sorted(VERBS)})")
    body["lane"] = "sofar"
    tok = next(l.split("=", 1)[1].strip().strip("'\"") for l in open(a.env_file) if l.startswith("ADMIN_TOKEN="))
    log = open(a.log, "a")

    def call(method, path, b=None):
        req = urllib.request.Request(a.api.rstrip("/") + path, method=method,
                                     data=json.dumps(b).encode() if b is not None else None,
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                st, out = r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            t = e.read().decode("utf-8", "replace")
            try:
                st, out = e.code, json.loads(t)
            except ValueError:
                st, out = e.code, t
        log.write(json.dumps({"t": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                              "call": f"{method} {path}", "body": b, "status": st, "answer": out}) + "\n"); log.flush()
        return st, out

    def send(cid):
        for _ in range(5):
            st2, out2 = call("POST", f"/admin/devices/{a.device}/commands/{cid}/send")
            if st2 == 409 and isinstance(out2, dict) and (out2.get("detail") or {}).get("reason") == "rate_limited":
                time.sleep(int((out2.get("detail") or {}).get("retry_after_s") or 65) + 2); continue
            break
        print(f"CID {cid} send {st2}", flush=True)
        return st2 in (200, 202)

    if a.refresh:
        st, out = call("POST", f"/devices/{a.device}/remote-config/refresh", {"lane": "sofar"})
        cmds = out.get("commands") if isinstance(out, dict) else None
        if not cmds:
            print(f"REFUSED {st}: {str(out)[:200]}"); return 3
        print(f"[refresh] {a.device}: {len(cmds)} gets recorded "
              f"({', '.join(str(c['command_id']) for c in cmds)})", flush=True)
        ok = [send(c["command_id"]) for c in sorted(cmds, key=lambda c: c["command_id"])]
        print(f"[refresh] {a.device}: {sum(ok)}/{len(ok)} sent")
        return 0 if all(ok) else 1

    st, out = call("POST", f"/admin/devices/{a.device}/commands", body)
    cid = out.get("command_id") if isinstance(out, dict) else None
    if not cid:
        print(f"REFUSED {st}: {str(out)[:200]}"); return 3
    return 0 if send(cid) else 1


if __name__ == "__main__":
    sys.exit(main())
