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
  - "c"  must be one of the six v1 commands -> else error "cmd"
  - "v"  must be a valid table index for "c" -> else error "val".
    ping may omit "v" (defaults to 0); settings commands must carry it.
  - unknown extra keys are TOLERATED (forward compatibility: a newer
    sender may add fields; ignoring them cannot mis-apply a setting)

Ack shape: {"id":N,"ok":1,"st":{roi,foc,awb,exp,win}} — plus "e":"<code>"
when ok=0. `st` always carries all five settings keys (missing input keys
are filled from factory defaults) so any single ack tells the operator
the complete truth (DESIGN D4). Compact separators; a full ack is ~60
bytes, far under the 384-char uplink chunk (Sprint09 locked values).

Example:
  >>> from command_messages import parse_command, build_ack
  >>> parse_command(b'{"id": 417, "c": "roi", "v": 2}')
  {'ok': True, 'id': 417, 'cmd': 'roi', 'value': 2, 'error': None}
  >>> build_ack(417, True, {"roi": 2, "foc": 0, "awb": 0, "exp": 0, "win": 0})
  '{"id":417,"ok":1,"st":{"roi":2,"foc":0,"awb":0,"exp":0,"win":0}}'
"""

import json

from command_tables import DEFAULT_SETTINGS, SETTINGS_COMMANDS, is_command, valid_value

# Compact error codes (ride in the ack "e" field; keep them short).
ERR_JSON = "json"  # not UTF-8 / not JSON / not an object
ERR_ID = "id"      # id missing/invalid -> command is UNACKABLE (drop + log)
ERR_CMD = "cmd"    # unknown command name
ERR_VAL = "val"    # value index not in the command's table

# id must fit uint32: satellite-side senders stay small, and the dedupe
# store never grows unbounded entries.
MAX_COMMAND_ID = 2**32 - 1


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
    if value is None and cmd == "ping":
        value = 0  # ping carries no value (SPEC); normalize to index 0

    if not valid_value(cmd, value):
        return _result(False, command_id=command_id, cmd=cmd, error=ERR_VAL)

    return _result(True, command_id=command_id, cmd=cmd, value=value)


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
