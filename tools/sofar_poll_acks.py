#!/usr/bin/env python3
# filename: sofar_poll_acks.py
# description: Sprint10 §6/§7 / Sprint26 S4 c.3 — poll api/sensor-data for BM command acks (v9 slim acks) and <CF> config lines.
"""
Mac-side ack poller: find command answers in Sofar sensor-data.

The camera (commands v9, DESIGN_supervisor.md §6.2) answers every
remote-range command with a slim ack JSON uplink

    {"id":N,"ok":1,"h":"<cfghash8>"[,"e":code,"k":key,"s":1,"d":1,"v":min]}

(`h` config hash after the command, `e`/`k` rejection code + first
offending key, `s:1` staged until cfm, `d:1` a repeat of an earlier
answer, `v` granted hold minutes) and sends config values as

    <CF v=1 h=<hash8> [n=i/N] [err=<kind> k=<key>|reverted=<key> lim=.. ref=..] key=value[@src] ...>

(`get` answers, the change summary after a set/reset, boot config
errors, guarded reverts; src: none = YAML, @d default, @c<id> command).
Both ride the Spotter cellular queue to Sofar's backend and appear
(13-30 min lag observed on this bench — Notecard batch sync) as
hex-encoded `value` fields at:

    GET https://api.sofarocean.com/api/sensor-data

This tool decodes the window and lists acks + <CF> lines, or waits for
specific command ids (--wait-for), for bench verification and as the
reference implementation for the GUI's ack watcher.

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
  - stdout: one line per ack (UTC, id, ok, h e k s d v, node) and per
    <CF> line (UTC, h, n, err/reverted head, key=value@src pairs)
  - wait mode exits 0 only when every requested id was seen; 1 on
    timeout ("not seen yet" is NOT "not delivered" — backend lag)

Example
  python3 tools/sofar_poll_acks.py --spotter-id SPOT-33507C --hours 2
  python3 tools/sofar_poll_acks.py --spotter-id SPOT-33507C \
      --wait-for 1000101 1000102 --timeout-min 40

Known limitations: <CF> values are shown as sent (text, %XX unescaped);
a multi-part <CF> (n=i/N) is printed per part, not reassembled. A v8
ack from an unmigrated unit (with `st`) still parses; `st` is not shown.
"""

import argparse
import json
import os
import re
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
    (v9: command_wire.build_ack). Image/status traffic (<I{i}> chunks,
    START/END lines, <CF>/<HL>/<WS> lines) never parses as such an object.
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


ACK_FIELDS = ("h", "e", "k", "s", "d", "v")    # the v9 slim ack, after id/ok
# <CF> head fields (command_v9: report_errors / flush_notes); everything else
# in a <CF> is a `key=value[@src]` item (registry keys always contain a dot).
CF_HEAD = ("err", "k", "reverted", "lim", "ref")
_PCT = re.compile(r"(?:%[0-9A-Fa-f]{2})+")


def cf_unescape(text):
    """Inverse of command_wire.cf_value's %XX escaping (UTF-8 bytes)."""
    return _PCT.sub(lambda m: bytes.fromhex(m.group(0).replace("%", ""))
                    .decode("utf-8", "replace"), text)


def tag_fields(text, tag):
    """`<TAG a=1 b=2 ...>` -> {"a": "1", "b": "2"} (values raw), else None.
    Used for <HL ...> (rc_heal.build_hl_message) and <WS ...>."""
    if text is None:
        return None
    s = text.strip()
    if not (s.startswith(f"<{tag} ") and s.endswith(">")):
        return None
    out = {}
    for tok in s[len(tag) + 2:-1].split():
        name, _, value = tok.partition("=")
        out[name] = value
    return out


def extract_cf(text):
    """A decoded `<CF v=1 h=.. [n=i/N] ...>` line -> dict, else None.

    {"v", "h", "n": (i, N) or None, "err", "k", "reverted", "lim", "ref"
    (None when absent), "items": [(key, value_text, src or None)]}.
    src is the text after `@` (d = default, c<id> = command); a value
    ending in `~` was cut by the unit (too long for one part)."""
    fields = tag_fields(text, "CF")
    if fields is None:
        return None
    out = {"v": None, "h": None, "n": None, "items": []}
    out.update({name: None for name in CF_HEAD})
    for tok in text.strip()[4:-1].split():
        name, _, value = tok.partition("=")
        if name == "v":
            out["v"] = value
        elif name == "h":
            out["h"] = value
        elif name == "n":
            try:
                i, total = value.split("/")
                out["n"] = (int(i), int(total))
            except ValueError:
                out["n"] = None
        elif name in CF_HEAD:
            out[name] = cf_unescape(value)
        else:
            value, at, src = value.partition("@")
            out["items"].append((cf_unescape(name), cf_unescape(value),
                                 src if at else None))
    return out


def normalize_node_id(raw):
    """sensor-data `bristlemouth_node_id` ('0x53171fa3d81a8e6f') ->
    bare lowercase hex, or None. Verified field name/format 2026-07-27
    (Phase C acks 801/802)."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    return s[2:] if s.startswith("0x") else s


def _sweep(spotter_id, token, start_iso, end_iso, timeout_s=45):
    url = API + "?" + urlencode({
        "spotterId": spotter_id, "startDate": start_iso, "endDate": end_iso,
        "token": token,
    })
    with urlopen(url, timeout=timeout_s, context=_ssl_context()) as resp:
        return json.load(resp).get("data", [])


def classify_reply(text):
    """Decoded uplink text -> ("ack", dict) | ("cf", dict) | (None, None)."""
    ack = extract_ack(text)
    if ack is not None:
        return "ack", ack
    cf = extract_cf(text)
    if cf is not None:
        return "cf", cf
    return None, None


def replies_from_rows(rows):
    """sensor-data rows -> [(utc, kind, obj, node_id)] for acks and <CF>."""
    out = []
    for entry in rows:
        kind, obj = classify_reply(decode_value(entry.get("value")))
        if kind is not None:
            out.append((entry.get("timestamp", "?"), kind, obj,
                        normalize_node_id(entry.get("bristlemouth_node_id"))))
    return out


def fetch_replies(spotter_id, token, start_iso, end_iso, timeout_s=45):
    """One sensor-data sweep -> [(utc, "ack"|"cf", obj, node_id)]."""
    return replies_from_rows(_sweep(spotter_id, token, start_iso, end_iso,
                                    timeout_s))


def fetch_acks(spotter_id, token, start_iso, end_iso, timeout_s=45):
    """One sensor-data sweep -> list of (utc_timestamp, ack_dict, node_id).
    node_id is the publishing BM node (bare lowercase hex) or None."""
    return [(ts, obj, node) for ts, kind, obj, node in
            fetch_replies(spotter_id, token, start_iso, end_iso, timeout_s)
            if kind == "ack"]


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
    """One ack line: id, ok, then the v9 fields present (h e k s d v)."""
    extra = "".join(f" {name}={ack[name]}" for name in ACK_FIELDS
                    if ack.get(name) is not None)
    node = f" node={node_id}" if node_id else ""
    return f"{ts}  id={ack['id']} ok={ack['ok']}{extra}{node}"


def fmt_cf(ts, cf, node_id=None):
    """One <CF> line: hash, part, head (err/k or reverted/lim/ref), items."""
    parts = [f"{ts}  <CF> h={cf['h']}"]
    if cf["n"]:
        parts.append(f"n={cf['n'][0]}/{cf['n'][1]}")
    parts += [f"{name}={cf[name]}" for name in CF_HEAD if cf.get(name) is not None]
    parts += [f"{k}={v}" + (f"@{src}" if src else "") for k, v, src in cf["items"]]
    if node_id:
        parts.append(f"node={node_id}")
    return " ".join(parts)


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
        replies = fetch_replies(args.spotter_id, token, start, end)
        n_ack = sum(1 for r in replies if r[1] == "ack")
        print(f"# {n_ack} acks, {len(replies) - n_ack} <CF> lines in {start}..{end}")
        for ts, kind, obj, node_id in replies:
            print(fmt(ts, obj, node_id) if kind == "ack" else fmt_cf(ts, obj, node_id))
        return 0

    want = set(args.wait_for)
    deadline = time.time() + args.timeout_min * 60
    seen = {}
    seen_cf = set()
    while True:
        start, end = window()
        try:
            replies = fetch_replies(args.spotter_id, token, start, end)
        except OSError as e:
            print(f"[WARN] sweep failed ({e}); retrying")
            replies = []
        for ts, kind, obj, node_id in replies:
            if kind == "ack" and obj["id"] in want and obj["id"] not in seen:
                seen[obj["id"]] = obj
                print(f"[ACK] {fmt(ts, obj, node_id)}")
            elif kind == "cf" and (ts, obj["h"]) not in seen_cf:
                seen_cf.add((ts, obj["h"]))
                print(f"[CF]  {fmt_cf(ts, obj, node_id)}")
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
