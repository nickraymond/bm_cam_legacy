#!/usr/bin/env python3
# filename: sofar_poll_acks.py
# description: Sprint10 §6/§7 — poll api/sensor-data for BM command acks.
"""
Sprint10 — Mac-side ack poller: find command acks in Sofar sensor-data.

The camera daemon acks every processed command with a compact JSON
uplink message ({"id":N,"ok":0|1,...,"st":{...}}). Those ride the
Spotter cellular queue to Sofar's backend and appear (13-30 min lag
observed on this bench — Notecard batch sync) as hex-encoded `value`
fields at:

    GET https://api.sofarocean.com/api/sensor-data

This tool decodes the window, extracts ack JSONs, and either lists them
or waits for specific command ids (--wait-for), for Phase C/D
verification and as the reference implementation for the GUI's ack
watcher (SPEC "Operator GUI" item 4).

Inputs
  --spotter-id SPOT-XXXXX      target Spotter (required)
  --hours N                    lookback window (default 3)
  --wait-for ID [ID ...]       poll until acks for ALL these command ids
                               are seen (or --timeout-min expires)
  --poll-s N                   poll interval in wait mode (default 120;
                               remote API — keep polite)
  --timeout-min N              wait-mode give-up (default 45; backend lag
                               alone is 13-30 min)
  env SOFAR_API_TOKEN_BM_REEF  API token (never on CLI, never printed)

Outputs
  - stdout: one line per ack found (UTC, id, ok, error code, st)
  - wait mode exits 0 only when every requested id was seen; 1 on
    timeout ("not seen yet" is NOT "not delivered" — backend lag)

Example
  python3 tools/sofar_poll_acks.py --spotter-id SPOT-33507C --hours 2
  python3 tools/sofar_poll_acks.py --spotter-id SPOT-33507C \
      --wait-for 801 802 --timeout-min 40
"""

import argparse
import json
import os
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

API = "https://api.sofarocean.com/api/sensor-data"
TOKEN_ENV = "SOFAR_API_TOKEN_BM_REEF"


def _ssl_context():
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
    return ctx


def decode_value(raw):
    """Hex sensor-data value -> text, or None (per Sprint09 DEV_LOG Q2)."""
    try:
        return bytes.fromhex(str(raw).strip()).decode("utf-8", "replace")
    except ValueError:
        return None


def extract_ack(text):
    """Parse an ack JSON out of a decoded uplink message, else None.

    Acks are bare compact JSON objects with integer `id` and `ok` keys
    (command_messages.build_ack). Image/status traffic (<I{i}> chunks,
    START/END lines) never parses as such an object.
    """
    if text is None:
        return None
    s = text.strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    if not isinstance(obj.get("id"), int) or "ok" not in obj:
        return None
    return obj


def normalize_node_id(raw):
    """sensor-data `bristlemouth_node_id` ('0x53171fa3d81a8e6f') ->
    bare lowercase hex, or None. Verified field name/format 2026-07-27
    (Phase C acks 801/802)."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    return s[2:] if s.startswith("0x") else s


def fetch_acks(spotter_id, token, start_iso, end_iso, timeout_s=45):
    """One sensor-data sweep -> list of (utc_timestamp, ack_dict, node_id).
    node_id is the publishing BM node (bare lowercase hex) or None."""
    url = API + "?" + urlencode({
        "spotterId": spotter_id, "startDate": start_iso, "endDate": end_iso,
        "token": token,
    })
    with urlopen(url, timeout=timeout_s, context=_ssl_context()) as resp:
        payload = json.load(resp)
    acks = []
    for entry in payload.get("data", []):
        ack = extract_ack(decode_value(entry.get("value")))
        if ack is not None:
            acks.append((entry.get("timestamp", "?"), ack,
                         normalize_node_id(entry.get("bristlemouth_node_id"))))
    return acks


def fetch_latest_row_utc(spotter_id, token, hours=3.0, timeout_s=45):
    """Newest sensor-data row timestamp (ISO string) in the window, or None.

    Any row counts, not just acks — a fresh row means the unit is (or was
    moments ago) awake and transmitting, which is the GUI's wake-detection
    signal for aiming command sends at the bus-on window.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = API + "?" + urlencode({
        "spotterId": spotter_id, "startDate": start, "endDate": end,
        "token": token,
    })
    with urlopen(url, timeout=timeout_s, context=_ssl_context()) as resp:
        payload = json.load(resp)
    latest = None
    for entry in payload.get("data", []):
        ts = entry.get("timestamp")
        if ts and (latest is None or ts > latest):
            latest = ts
    return latest


def fmt(ts, ack, node_id=None):
    err = f" e={ack['e']}" if "e" in ack else ""
    node = f" node={node_id}" if node_id else ""
    return (f"{ts}  id={ack['id']} ok={ack['ok']}{err}{node} "
            f"st={json.dumps(ack.get('st', {}), separators=(',', ':'))}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--spotter-id", required=True)
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--wait-for", type=int, nargs="+", default=None)
    ap.add_argument("--poll-s", type=float, default=120.0)
    ap.add_argument("--timeout-min", type=float, default=45.0)
    args = ap.parse_args(argv)

    token = os.environ.get(TOKEN_ENV)
    if not token:
        print(f"[ERROR] set {TOKEN_ENV} in the environment (never on the CLI).")
        return 2

    def window():
        now = datetime.now(timezone.utc)
        return ((now - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                now.strftime("%Y-%m-%dT%H:%M:%SZ"))

    if args.wait_for is None:
        start, end = window()
        acks = fetch_acks(args.spotter_id, token, start, end)
        print(f"# {len(acks)} acks in {start}..{end}")
        for ts, ack, node_id in acks:
            print(fmt(ts, ack, node_id))
        return 0

    want = set(args.wait_for)
    deadline = time.time() + args.timeout_min * 60
    seen = {}
    while True:
        start, end = window()
        try:
            acks = fetch_acks(args.spotter_id, token, start, end)
        except OSError as e:
            print(f"[WARN] sweep failed ({e}); retrying")
            acks = []
        for ts, ack, node_id in acks:
            if ack["id"] in want and ack["id"] not in seen:
                seen[ack["id"]] = ack
                print(f"[ACK] {fmt(ts, ack, node_id)}")
        missing = want - set(seen)
        if not missing:
            print(f"[OK] all {len(want)} ack(s) observed at the backend.")
            return 0
        if time.time() >= deadline:
            print(f"[TIMEOUT] not seen after {args.timeout_min:.0f} min: "
                  f"{sorted(missing)} — backend lag is 13-30 min; not proof "
                  f"of non-delivery.")
            return 1
        print(f"[wait] missing {sorted(missing)}; next poll in "
              f"{args.poll_s:.0f} s")
        time.sleep(args.poll_s)


if __name__ == "__main__":
    sys.exit(main())
