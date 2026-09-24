#!/usr/bin/env python3
# filename: command_messages.py
# description: Sprint10 — inbound command parser + outbound ack builder.
"""
Sprint10 command daemon — the wire-payload contract, both directions.

Inbound:  parse_command(payload) validates one command payload
          `{"id": 417, "c": "roi", "v": 2}` against command_tables.
Outbound: build_ack(...) renders the full-state ack
          `{"id": 417, "ok": 1, "st": {...}}` (SPEC "Ack contract").

Pure functions: no serial, no camera, no filesystem. Framing (COBS/CRC,
topic) is the daemon's job (§2); this module sees only the payload bytes
between frame decode and camera apply.

Validation rules (DESIGN D3 — reject anything not exactly right):
  - payload must be UTF-8 JSON object; anything else -> error "json"
  - "id" must be a non-bool int in [0, 2**32) -> else error "id".
    An invalid id means NO ack is possible (nothing to correlate); the
    caller logs and drops. Every other error still acks (ok=0 + code).
  - "c"  must be a known command name -> else error "cmd". An OLD receiver
    getting a NEWER sender's command (e.g. v2 tables receiving "hlt")
    rejects it with this code — the version-mismatch path is a normal
    ack, never a crash.
  - "v"  must be a valid table index for "c" -> else error "val".
    ping and the query commands (help/cfg) may omit "v" (defaults to 0);
    settings commands must carry it, and so must trg (arming a trigger
    with no value would be ambiguous).
  - unknown extra keys are TOLERATED (forward compatibility: a newer
    sender may add fields; ignoring them cannot mis-apply a setting)

Ack shape: {"id":N,"ok":1,"st":{roi,...,hlt,twn}} — plus "e":"<code>"
when ok=0. `st` always carries every SETTINGS_COMMANDS key (missing input
keys are filled from factory defaults) so any single ack tells the
operator the complete truth (DESIGN D4). A trg ack means ARMED, not
captured — execution proof is the image/wake status on the next boot.
Compact separators; a full ack is ~80 bytes, far under the 384-char
uplink chunk (Sprint09 locked values).

Example:
  >>> from command_messages import parse_command, build_ack
  >>> parse_command(b'{"id": 417, "c": "roi", "v": 2}')
  {'ok': True, 'id': 417, 'cmd': 'roi', 'value': 2, 'error': None}
  >>> build_ack(417, True, {"roi": 2, "foc": 0, "awb": 0, "exp": 0, "win": 0})
  '{"id":417,"ok":1,"st":{"roi":2,"foc":0,"awb":0,"exp":0,"win":0}}'
"""

import json
import re

from command_tables import (
    DEFAULT_SETTINGS,
    HEAL_COMMANDS,
    QUERY_COMMANDS,
    SETTINGS_COMMANDS,
    is_command,
    valid_value,
)

# Compact error codes (ride in the ack "e" field; keep them short).
ERR_JSON = "json"  # not UTF-8 / not JSON / not an object
ERR_ID = "id"      # id missing/invalid -> command is UNACKABLE (drop + log)
ERR_CMD = "cmd"    # unknown command name
ERR_VAL = "val"    # value index not in the command's table

# id must fit uint32: satellite-side senders stay small, and the dedupe
# store never grows unbounded entries.
MAX_COMMAND_ID = 2**32 - 1

# rsd (Sprint25 S5, tables v8; SPEC_resend_heal.md §5). Limits are per command.
RSD_MAX_HEALS = 8        # 270 B Sofar cap holds ~6-8 heals (spec §0c S2b record)
RSD_MAX_CHUNKS = 40      # per command AND per wake to start (spec §0b Q1; ceiling 60)
RE_MEDIA_KEY = re.compile(r"^[0-9a-z]{6}$")
RE_RANGES = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")


def _result(ok, command_id=None, cmd=None, value=None, error=None):
    return {"ok": ok, "id": command_id, "cmd": cmd, "value": value, "error": error}


def _valid_id(command_id):
    if isinstance(command_id, bool) or not isinstance(command_id, int):
        return False
    return 0 <= command_id <= MAX_COMMAND_ID


def parse_command(payload):
    """Validate one inbound command payload (bytes or str).

    Returns a dict {ok, id, cmd, value, error}:
      ok=True          -> id/cmd/value are safe to apply
      ok=False, id set -> reject, but ack with error code (error field)
      ok=False, id None-> unackable (bad JSON or bad id); caller drops
    Never raises on hostile input.
    """
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = bytes(payload).decode("utf-8")
        except UnicodeDecodeError:
            return _result(False, error=ERR_JSON)

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _result(False, error=ERR_JSON)

    if not isinstance(data, dict):
        return _result(False, error=ERR_JSON)

    command_id = data.get("id")
    if not _valid_id(command_id):
        return _result(False, error=ERR_ID)

    cmd = data.get("c")
    if not isinstance(cmd, str) or not is_command(cmd):
        return _result(False, command_id=command_id, error=ERR_CMD)

    value = data.get("v")
    if value is None and (cmd == "ping" or cmd in QUERY_COMMANDS):
        value = 0  # ping/help/cfg carry no value; normalize to index 0

    if cmd in HEAL_COMMANDS:
        heal = parse_rsd(data)
        if heal is None:
            return _result(False, command_id=command_id, cmd=cmd, error=ERR_VAL)
        return _result(True, command_id=command_id, cmd=cmd, value=heal)

    if not valid_value(cmd, value):
        return _result(False, command_id=command_id, cmd=cmd, error=ERR_VAL)

    return _result(True, command_id=command_id, cmd=cmd, value=value)


def expand_ranges(text, max_chunks=RSD_MAX_CHUNKS):
    """"17,40-42" -> [17, 40, 41, 42], or None if malformed, reversed ("42-40"),
    duplicated ("3,1-4"), or more than max_chunks indices. Counts BEFORE expanding,
    so a hostile "0-99999999" costs nothing."""
    if not isinstance(text, str) or not RE_RANGES.match(text):
        return None
    spans, total = [], 0
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        lo = int(lo)
        hi = int(hi) if hi else lo
        if hi < lo:
            return None
        total += hi - lo + 1
        if total > max_chunks:
            return None
        spans.append((lo, hi))
    out = [n for lo, hi in spans for n in range(lo, hi + 1)]
    if len(set(out)) != len(out):
        return None
    return sorted(out)


def parse_rsd(data):
    """Validate an rsd payload (already a JSON object). Returns
    {"x": 1} (cancel all pending heals) or {"h": [[key, [n, ...]], ...]}, or None.

    Rules (SPEC_resend_heal.md §5): exactly one of "x" / "h"; "x" must be 1;
    "h" is 1..8 [key, ranges] pairs, key ^[0-9a-z]{6}$ and unique within the
    command, ranges per expand_ranges; <= 40 chunks across the whole command.
    Whether each key has a sent record and every n < its msgs is the daemon's
    check (heal_validate_fn) — it needs the filesystem.
    """
    has_x, has_h = "x" in data, "h" in data
    if has_x == has_h:
        return None
    if has_x:
        x = data["x"]
        return {"x": 1} if (x == 1 and not isinstance(x, bool)) else None
    heals = data["h"]
    if not isinstance(heals, list) or not 1 <= len(heals) <= RSD_MAX_HEALS:
        return None
    out, keys, total = [], set(), 0
    for item in heals:
        if not isinstance(item, list) or len(item) != 2:
            return None
        key, ranges = item
        if not isinstance(key, str) or not RE_MEDIA_KEY.match(key) or key in keys:
            return None
        ns = expand_ranges(ranges, RSD_MAX_CHUNKS - total)
        if ns is None:
            return None
        keys.add(key)
        total += len(ns)
        out.append([key, ns])
    return {"h": out}


def build_ack(command_id, ok, settings, error=None):
    """Render the full-state ack JSON string (SPEC "Ack contract").

    settings: current applied settings dict; any missing key is filled
    from factory defaults so `st` is always complete. error: compact
    code, only rendered when ok is falsy.
    """
    st = {key: int(settings.get(key, DEFAULT_SETTINGS[key])) for key in SETTINGS_COMMANDS}
    ack = {"id": int(command_id), "ok": 1 if ok else 0}
    if not ok:
        ack["e"] = str(error) if error else "err"
    ack["st"] = st
    return json.dumps(ack, separators=(",", ":"))
