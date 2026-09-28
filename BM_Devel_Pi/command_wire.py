#!/usr/bin/env python3
# filename: command_wire.py
# description: Sprint26 S4 a.2 — commands v9 wire: strict parse, verb shapes, id ranges, HMAC service signatures, slim ack, <CF>, console lines.
"""
The commands v9 wire (DESIGN_supervisor.md §6.1–6.3; PLAN_S4.md G5, G8, G9).

Pure: stdlib only, no file, clock, UART or subprocess. The daemon (S4b) calls
it on the MAIN thread at a decision point, never on the burst path.

Inputs:  one inbound `bm pub` payload (bytes or str).
Outputs:
  decode(payload)      -> Command, or raises Unackable (bad JSON / no usable id:
                          console line only, no ack) or Rejected (ackable: the
                          command's id is known; ack e:<code>, k:<key>)
  id_range(id)         -> "console" | "heal" | "remote" | "service" | "conductor" | None
  canonical_for_sig(d) / sign(d, key) / verify_sig(d, key)
  build_ack(...)       -> slim ack JSON  {"id","ok","h"[,"e","k","s","d","v"]}
  build_cf(h, items)   -> ["<CF v=1 h=.. k=v ..>", ...] (charset-safe, chunked)
  console_line(...)    -> one printable-ASCII console line

Rules (§6.2 strict JSON; REVIEW K10):
  - the whole JSON, `sig` included, is at most 248 bytes (the 270 B console
    line = 22 B of `bm pub bmcam/cmd ` + ` 1 1` + newline + this JSON);
  - ASCII only; NaN / Infinity / 1e999 refused; duplicate keys refused;
  - no bool where an int is meant (id, v, ref, all);
  - strings [A-Za-z0-9_:./+-]{1,48} (keys too); lists 1..4 items, all numbers
    or all strings; objects only at the top and as `kv` (depth <= 2);
  - `rsd` keeps its own structure (SPEC_resend_heal §5) under the same strict
    decode: command_messages.parse_rsd validates it, unchanged.

Error codes (G8): id cmd key val xk lock auth old cas big ref err. The wire
module raises id / cmd / key / val / auth-shape; the rest belong to the
dispatcher (S4b).

Example:
  python3 -c "import command_wire as w; print(w.decode(b'{\\"id\\":7,\\"c\\":\\"ping\\"}'))"

Known limitations: registry types and ranges are NOT checked here (the
whole-config validator does that, config_validate.py); short names are
resolved by the dispatcher (they depend on the media in play).
"""

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, field

MAX_JSON_BYTES = 248
CONSOLE_OVERHEAD_BYTES = 22          # "bm pub bmcam/cmd " + " 1 1" + "\n"
MAX_CONSOLE_LINE_BYTES = MAX_JSON_BYTES + CONSOLE_OVERHEAD_BYTES   # 270, Sofar limit
MAX_ID = 2**32 - 1
MAX_ABS_INT = 2**53                  # anti-DoS bound; registry ranges are tighter
MAX_LIST = 4
MAX_KV = 16
MAX_CONSOLE_CHARS = 240              # one Spotter console line
MAX_CF_BYTES = 280                   # same ceiling as <WS> (compact_kv_message)
SIG_HEX = 16                         # HMAC-SHA256 truncated to 64 bits

RE_STR = re.compile(r"[A-Za-z0-9_:./+-]{1,48}\Z")
RE_HASH8 = re.compile(r"[0-9a-f]{8}\Z")
RE_SIG = re.compile(r"[0-9a-f]{16}\Z")

# §6.2 command-id ranges per sender: (name, lo, hi, high_water, cellular reply)
RANGES = (
    ("console",   1,             99_999,        False, False),
    ("heal",      100_000,       999_999,       False, False),   # <HL> is the cellular answer
    ("remote",    1_000_000,     99_999_999,    True,  True),
    ("service",   100_000_000,   199_999_999,   True,  True),
    ("conductor", 2_000_000_000, MAX_ID,        False, False),
)
HIGH_WATER_RANGES = tuple(r[0] for r in RANGES if r[3])
CELLULAR_RANGES = tuple(r[0] for r in RANGES if r[4])

VERBS = ("ping", "help", "get", "set", "reset", "cfm", "trg", "hld", "rsd", "wap")
FIELDS = {
    "ping":  {"to"},
    "help":  {"to"},
    "get":   {"k", "to"},
    "set":   {"kv", "b", "sig"},
    "reset": {"k", "all", "b", "sig"},
    "cfm":   {"ref"},
    "trg":   {"v", "kv"},
    "hld":   {"v"},
    "rsd":   {"h", "x"},
    "wap":   {"v"},
}
TRG_VALUES = (0, 1, 2, 3, 4)   # 0 cancel · 1 capture+save · 2 capture+output per mode · 3/4 reference image
HLD_MAX_WIRE_MIN = 1440        # the registry cap (commands.hold_max_min) is applied by the dispatcher


class Unackable(ValueError):
    """No usable id (bad JSON, not an object, bad id): console line only."""


@dataclass(eq=False)
class Rejected(Exception):
    """Refused with a known id: ack {"id", "ok":0, "e": code[, "k": key]}."""
    id: int
    code: str
    key: str = None
    why: str = ""

    def __post_init__(self):
        super().__init__(self.id, self.code, self.key, self.why)
        # `k` rides the ack and the console: only a charset-clean name, else none.
        if not (isinstance(self.key, str) and RE_STR.match(self.key)):
            self.key = None

    def __str__(self):
        return f"id={self.id} e={self.code}" + (f" k={self.key}" if self.key else "") + \
            (f": {self.why}" if self.why else "")


@dataclass
class Command:
    id: int
    verb: str
    range: str
    fields: dict = field(default_factory=dict)   # everything except id and c
    raw: dict = field(default_factory=dict)      # the decoded object (for sig checks)
    rsd: dict = None                             # parse_rsd() result for rsd


# ---------------------------------------------------------------------------
# strict JSON
# ---------------------------------------------------------------------------

def _no_constant(name):
    raise ValueError(f"{name} is not allowed")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} is not finite")
    return value


def _bounded_int(text):
    value = int(text)
    if abs(value) > MAX_ABS_INT:
        raise ValueError(f"{text} is too large")
    return value


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def strict_loads(payload):
    """bytes/str -> JSON object, or raise Unackable. ASCII only."""
    if isinstance(payload, str):
        try:
            payload = payload.encode("ascii")
        except UnicodeEncodeError:
            raise Unackable("not ASCII")
    if not isinstance(payload, (bytes, bytearray)):
        raise Unackable("not bytes")
    try:
        text = bytes(payload).decode("ascii")
    except UnicodeDecodeError:
        raise Unackable("not ASCII")
    try:
        data = json.loads(text, parse_constant=_no_constant, parse_float=_finite_float,
                          parse_int=_bounded_int, object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError) as exc:
        raise Unackable(f"not strict JSON: {exc}")
    if not isinstance(data, dict):
        raise Unackable("not a JSON object")
    return data


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def id_range(command_id):
    for name, lo, hi, _hw, _cell in RANGES:
        if lo <= command_id <= hi:
            return name
    return None


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------

def _check_str(cid, text, key):
    if not isinstance(text, str) or not RE_STR.match(text):
        raise Rejected(cid, "val", key, "strings are [A-Za-z0-9_:./+-]{1,48}")


def _check_scalar_or_list(cid, value, key):
    """kv values: null, bool, finite number, charset string, or a 1..4 list
    of all numbers or all strings. Never an object (depth <= 2)."""
    if value is None or isinstance(value, bool) or _is_num(value):
        return
    if isinstance(value, str):
        _check_str(cid, value, key)
        return
    if isinstance(value, list):
        if not 1 <= len(value) <= MAX_LIST:
            raise Rejected(cid, "val", key, f"lists hold 1..{MAX_LIST} items")
        if all(_is_num(v) for v in value):
            return
        if all(isinstance(v, str) for v in value):
            for v in value:
                _check_str(cid, v, key)
            return
        raise Rejected(cid, "val", key, "a list is all numbers or all strings")
    raise Rejected(cid, "val", key, "nested objects are not allowed")


def _key_list(cid, value, name):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_LIST:
        raise Rejected(cid, "val", name, f"{name} is a list of 1..{MAX_LIST} names")
    for item in value:
        _check_str(cid, item, name)


def _int_field(cid, fields, name, lo, hi, required=True):
    if name not in fields:
        if required:
            raise Rejected(cid, "val", name, f"{name} is required")
        return
    v = fields[name]
    if not _is_int(v) or not lo <= v <= hi:
        raise Rejected(cid, "val", name, f"{name} must be an integer {lo}..{hi}")


def decode(payload, parse_rsd=None):
    """One inbound payload -> Command. Raises Unackable or Rejected.

    parse_rsd: command_messages.parse_rsd (injected so this module stays free
    of the v8 tables that command_messages imports)."""
    raw_len = len(payload.encode("ascii", "replace") if isinstance(payload, str) else payload)
    data = strict_loads(payload)
    cid = data.get("id")
    if not _is_int(cid) or not 0 <= cid <= MAX_ID:
        raise Unackable("id must be an integer 0..2^32-1")
    if raw_len > MAX_JSON_BYTES:
        raise Rejected(cid, "val", None, f"{raw_len} B > {MAX_JSON_BYTES} B (sig included)")
    rng = id_range(cid)
    if rng is None:
        raise Rejected(cid, "id", None, "id is outside every sender range (§6.2)")
    verb = data.get("c")
    if not isinstance(verb, str) or verb not in VERBS:
        raise Rejected(cid, "cmd", None, f"unknown command {verb!r}")
    fields = {k: v for k, v in data.items() if k not in ("id", "c")}
    extra = sorted(set(fields) - FIELDS[verb])
    if extra:
        raise Rejected(cid, "key", extra[0], f"{verb} takes no {extra[0][:48]!r}")

    cmd = Command(id=cid, verb=verb, range=rng, fields=fields, raw=data)
    if verb == "rsd":
        heal = parse_rsd(data) if parse_rsd else None
        if heal is None:
            raise Rejected(cid, "val", None, "rsd shape (SPEC_resend_heal §5)")
        cmd.rsd = heal
        return cmd

    if "to" in fields and fields["to"] != "con":
        raise Rejected(cid, "val", "to", 'to is "con" (console only) or absent')
    if "b" in fields and not (isinstance(fields["b"], str) and RE_HASH8.match(fields["b"])):
        raise Rejected(cid, "val", "b", "b is an 8-hex config hash")
    if "sig" in fields and not (isinstance(fields["sig"], str) and RE_SIG.match(fields["sig"])):
        raise Rejected(cid, "auth", "sig", "sig is 16 lowercase hex")
    if "kv" in fields:
        kv = fields["kv"]
        if not isinstance(kv, dict) or not 1 <= len(kv) <= MAX_KV:
            raise Rejected(cid, "val", "kv", f"kv is an object of 1..{MAX_KV} keys")
        for key, value in kv.items():
            _check_str(cid, key, key)
            _check_scalar_or_list(cid, value, key)

    if verb == "get":
        if "k" not in fields:
            raise Rejected(cid, "val", "k", "get needs k")
        _key_list(cid, fields["k"], "k")
    elif verb == "set":
        if "kv" not in fields:
            raise Rejected(cid, "val", "kv", "set needs kv")
    elif verb == "reset":
        if ("k" in fields) == ("all" in fields):
            raise Rejected(cid, "val", "k", "reset takes exactly one of k / all")
        if "k" in fields:
            _key_list(cid, fields["k"], "k")
        elif not (_is_int(fields["all"]) and fields["all"] == 1):
            raise Rejected(cid, "val", "all", "all must be 1")
    elif verb == "cfm":
        _int_field(cid, fields, "ref", 1, MAX_ID)
    elif verb == "trg":
        _int_field(cid, fields, "v", 0, max(TRG_VALUES))
        if fields["v"] == 0 and "kv" in fields:
            raise Rejected(cid, "val", "kv", "trg 0 (cancel) takes no kv")
    elif verb == "hld":
        _int_field(cid, fields, "v", 0, HLD_MAX_WIRE_MIN)
    elif verb == "wap":
        _int_field(cid, fields, "v", 0, 99)
    return cmd


# ---------------------------------------------------------------------------
# service signatures (§6.3, O6)
# ---------------------------------------------------------------------------

SERVICE_KEY_PATH = "/home/pi/.config/nereus/service.key"   # locked path (§6.3); mode 600


def parse_service_key(text):
    """Key file text (64 hex = 32 bytes, as deploy_rc_runtime.sh
    --create-service-key writes it) -> bytes, or None if malformed."""
    if isinstance(text, bytes):
        text = text.decode("ascii", "replace")
    text = (text or "").strip()
    if not re.match(r"[0-9a-fA-F]{64}\Z", text):
        return None
    return bytes.fromhex(text)


def canonical_for_sig(data):
    """Canonical JSON of the command without `sig` (the HMAC input)."""
    body = {k: v for k, v in data.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def sign(data, key):
    """16 lowercase hex = HMAC-SHA256(key, canonical_for_sig(data))[:64 bits]."""
    if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
        raise ValueError("service key must be at least 16 bytes")
    mac = hmac.new(bytes(key), canonical_for_sig(data).encode("ascii"), hashlib.sha256)
    return mac.hexdigest()[:SIG_HEX]


def verify_sig(data, key):
    """True only for a well-formed sig matching `key` (constant-time compare)."""
    sig = data.get("sig")
    if not isinstance(sig, str) or not RE_SIG.match(sig) or not key:
        return False
    try:
        want = sign(data, key)
    except ValueError:
        return False
    return hmac.compare_digest(want, sig)


def encode_command(data):
    """The compact JSON a sender puts after `bm pub <topic> ` (tools)."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


# ---------------------------------------------------------------------------
# replies
# ---------------------------------------------------------------------------

def build_ack(command_id, ok, h=None, e=None, k=None, s=None, d=None, v=None):
    """Slim ack (§6.2): {"id","ok","h"} plus e/k on error, s:1 staged, d:1
    duplicate, v granted hold minutes. Field order is fixed (goldens)."""
    ack = {"id": int(command_id), "ok": 1 if ok else 0}
    if h:
        ack["h"] = str(h)
    if not ok:
        ack["e"] = str(e or "err")
        if k:
            ack["k"] = str(k)
    if s:
        ack["s"] = 1
    if d:
        ack["d"] = 1
    if v is not None:
        ack["v"] = int(v)
    return json.dumps(ack, separators=(",", ":"))


_CF_SAFE = re.compile(r"[A-Za-z0-9_:./+,-]")


def cf_value(value):
    """A value as <CF> text: lists comma-joined, bools 1/0, None `null`; any
    character outside [A-Za-z0-9_:./+,-] is %XX-escaped (so `%` itself too)."""
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "1" if value else "0"
    elif isinstance(value, list):
        text = ",".join(cf_value(v) for v in value)
        return text
    elif isinstance(value, float):
        text = repr(value)
    else:
        text = str(value)
    # UTF-8 bytes, each unsafe byte as %XX (review S4a #10: no two characters
    # share an escape).
    return "".join(chr(b) if _CF_SAFE.match(chr(b)) else "%{:02X}".format(b)
                   for b in text.encode("utf-8"))


def cf_source(source):
    """`yaml` -> "" (the common case costs nothing), `default` -> "@d",
    ("cmd", id) / "c<id>" -> "@c<id>"."""
    if not source or source == "yaml":
        return ""
    if source == "default":
        return "@d"
    if isinstance(source, tuple):
        return f"@c{int(source[1])}"
    return "@" + cf_value(str(source))


def build_cf(h, items, head=(), max_bytes=MAX_CF_BYTES):
    """<CF v=1 h=<hash8> [n=i/N] [head k=v] k=v[@src] ...> messages.

    items: [(key, value[, source])]; head: [(name, value)] fields repeated
    in every part (e.g. reverted=, err=). One item never splits across parts;
    an item longer than a whole part is cut and marked `~`."""
    def fmt(item):
        key, value = item[0], item[1]
        src = cf_source(item[2]) if len(item) > 2 else ""
        return f"{cf_value(key)}={cf_value(value)}{src}"

    head_txt = "".join(f" {cf_value(n)}={cf_value(v)}" for n, v in head)
    base = f"<CF v=1 h={cf_value(h)}"
    room = max_bytes - len(base) - len(head_txt) - len(" n=999/999") - 1   # 1 = ">"
    parts, cur = [], []
    for item in items:
        text = fmt(item)
        if len(text) + 1 > room:
            cut = room - 2
            pct = text.rfind("%", max(0, cut - 2), cut)
            text = text[:pct if pct != -1 else cut] + "~"      # never split a %XX
        if cur and sum(len(t) + 1 for t in cur) + len(text) + 1 > room:
            parts.append(cur)
            cur = []
        cur.append(text)
    if cur or not parts:
        parts.append(cur)
    n = len(parts)
    out = []
    for i, part in enumerate(parts, 1):
        tag = f" n={i}/{n}" if n > 1 else ""
        out.append(base + tag + head_txt + "".join(" " + t for t in part) + ">")
    return out


def console_line(host, text):
    """`[host] text`, printable ASCII only (spotter_print sends the character
    count as the length, bm_serial.py:229), cut to MAX_CONSOLE_CHARS."""
    line = f"[{host}] {text}"
    line = "".join(c if 32 <= ord(c) < 127 else "?" for c in line)
    if len(line) > MAX_CONSOLE_CHARS:
        line = line[:MAX_CONSOLE_CHARS - 3] + "..."
    return line
