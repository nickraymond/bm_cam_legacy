#!/usr/bin/env python3
# filename: rc_media_key.py
# description: Sprint25 S4 — rev 5 media key (keyed chunks) + the persisted sent record for heals.
"""
Sprint25 S4 — keyed media (wire contract rev 5, section 14; RESEND_DEVICE.md §2-3).

Key
  6 lowercase base-36 chars = whole seconds since 2026-01-01T00:00:00Z, zero-padded.
  Taken ONLY from the Spotter UTC read of THIS wake (the schedule gate's `spotter`
  read, or an explicit read over the command daemon's shared port). The Pi has no
  RTC: `system`/`rtc`/`skipped` gate sources never make a key. No key -> the wake
  sends the rev 3 legacy form (no key anywhere), exactly as today.
  Monotonic per device: the last key is persisted; a computed key <= last (stale
  clock) or out of range -> legacy form + one loud log line.

Wire (built by rc_uplink_messages / rc_transmit, not here)
  START: `key=<key>` right after `length`, a core key never dropped by the budget.
  chunk: `<I{key}.{n}>` (398 B at 384 base64 chars). END: unchanged.
  W9 (rev 5a, Sprint26 S4w, DESIGN_supervisor.md §10 O11): the chunk carries its
  media's planned total, `<I{key}.{n}/{M}>` with M = the START `length` (402 B at
  384 chars, i and M <= 999), so a media whose START was lost can still be healed.
  Only when CHUNK_TOTAL is set (the supervisor on a migrated config v2 unit, next to
  W8b); the caller reads it ONCE per send and passes it to both the sent record and
  the transmit function. Legacy, v1-supervisor and unkeyed wire: unchanged.

Sent record (what a heal re-sends, S5)
  sent/<stem>.sent.json      key, fmt, filename, chunk_b64_chars, msgs, sha256, sent_utc,
                             payload (path of the exact bytes that were chunked),
                             chunk_total: true (W9 only; absent = chunks had no /M)
  sent/<stem>.sent           video only: the fitted payload (it lives in tmpfs otherwise)
  Images keep NO copy: the compressed JPEG on disk IS the wire bytes; the sidecar
  points at it. Written atomically (tmp + fsync + rename) BEFORE START.
  Prune: before START in both modes, ONLY on a wake that allocated a key, records
  whose KEY time is older than `retain_days` (hard cap 30 d) before this wake's key
  are deleted (video_ring only prunes .mp4 triples; images had no retention at all).
  Ages come from keys (= Spotter UTC), never from file mtime vs the Pi clock: the Pi
  has no RTC, and the gate steps its clock to any 2020-2035 Spotter UTC with no jump
  limit, so a fast clock used to prune every record at once. Nothing is aged across
  a gap > retain_days between records (a clock jump or a long power-off): the older
  side is kept and logged (see expired_times).

`media_key:` island (camera_schedule.yaml) — ABSENT = DISABLED = wire byte-identical:

  media_key:
    enabled: false      # true only once the backend M0+ is live (it is, 2026-09-23)
    retain_days: 14     # sent/ records kept this long = the heal window (hard cap 30)
                        # (Nick 2026-09-24: 14 d; healing 2-week-old data is valuable)
    heal_order: before  # before (default = today's wire): rsd heal chunks go BEFORE this
                        # wake's START; after: START + the new media's burst first, then
                        # the heal chunks (Nick 2026-10-08: the freshest image gets the
                        # clean part of the window). Absent = before. Read by rc_heal.

The retired Sprint10 `media_gid` island (3-char gid) is ignored with a loud
warning if a YAML still enables it (warn_retired_media_gid).
"""

import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

KEY_WIDTH = 6
KEY_RANGE = 36 ** KEY_WIDTH                     # ~69 years of seconds
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
HARD_CAP_RETAIN_DAYS = 30
SPOTTER_TIME_SOURCES = ("spotter", "spotter_explicit")

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE_PATH = os.path.join(_HERE, "bm_media_key_last.txt")
DEFAULT_SENT_DIR = os.path.join(_HERE, "sent")
HEAL_ORDERS = ("before", "after")        # media_key.heal_order (Nick 2026-10-08)
DEFAULT_HEAL_ORDER = "before"
DEFAULT_CONFIG = {"enabled": False, "retain_days": 14.0, "heal_order": DEFAULT_HEAL_ORDER,
                  "source": "defaults",
                  "sent_dir": DEFAULT_SENT_DIR, "state_path": DEFAULT_STATE_PATH}


# --- key ----------------------------------------------------------------------------------

def encode_key(seconds):
    """int seconds since EPOCH -> 6-char base36, or None when out of range."""
    s = int(seconds)
    if s < 0 or s >= KEY_RANGE:
        return None
    out = []
    for _ in range(KEY_WIDTH):
        s, r = divmod(s, 36)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def decode_key(key):
    """6-char base36 -> int seconds since EPOCH (ValueError on a malformed key)."""
    if not isinstance(key, str) or len(key) != KEY_WIDTH or any(c not in _ALPHABET for c in key):
        raise ValueError(f"malformed media key {key!r}")
    n = 0
    for c in key:
        n = n * 36 + _ALPHABET.index(c)
    return n


def key_for_utc(utc_dt):
    """Spotter UTC (aware datetime) -> key, or None if out of range."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return encode_key((utc_dt - EPOCH).total_seconds() // 1)


def spotter_utc_for_wake(gate_info=None, daemon=None, timeout_s=20.0):
    """The Spotter UTC this wake read, or (None, reason).

    Order: the schedule gate's read when its source is the Spotter (free: the cycle
    already paid for it); else an explicit read over the daemon's shared port. Never
    the Pi clock. Returns (datetime|None, source|reason).
    """
    if gate_info and gate_info.get("source_time") == "spotter" and gate_info.get("utc_time"):
        try:
            dt = datetime.fromisoformat(gate_info["utc_time"])
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)), "spotter"
        except (TypeError, ValueError):
            pass
    if daemon is not None:
        try:
            dt = daemon.wait_for_spotter_utc(timeout_s)
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)), "spotter_explicit"
        except Exception as exc:   # TimeoutError on a silent bus
            return None, f"spotter_read_failed: {exc}"
    source = (gate_info or {}).get("source_time") or "no_gate_read"
    return None, f"no_spotter_time (gate source={source})"


def _read_last(state_path):
    try:
        with open(state_path, "r", encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _atomic_write(path, data, mode="w"):
    tmp = path + ".tmp"
    with open(tmp, mode) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def allocate_key(utc_dt, source, state_path=DEFAULT_STATE_PATH):
    """Key for this wake, persisted, or (None, reason) -> the wake sends legacy.

    Monotonic: a key <= the last persisted key means a stale/rewound clock; reusing
    it would merge two media at the backend, so the wake falls back to legacy.
    """
    if utc_dt is None:
        return None, source
    key = key_for_utc(utc_dt)
    if key is None:
        return None, f"out_of_range ({utc_dt.isoformat()})"
    last = _read_last(state_path)
    if last is not None:
        try:
            if decode_key(key) <= decode_key(last):
                return None, f"stale_clock (key {key} <= last {last})"
        except ValueError:
            pass  # corrupt state: the new key overwrites it
    _atomic_write(state_path, key)
    return key, source


# --- config -------------------------------------------------------------------------------

# W9 (Sprint26 S4w): set by the supervisor on a migrated unit (rc_supervisor
# _install_wire_extras, cleared by finish(), the same lifetime as W8b's
# START_EXTRA_FN). False = every chunk keeps the rev 5 prefix, byte-identical.
CHUNK_TOTAL = False


def chunk_prefix(i, key=None, total=None):
    """Wire prefix for chunk i: legacy `<I7>`, keyed `<I{key}.{i}>` (rev 5), or keyed
    with the media's planned total `<I{key}.{i}/{total}>` (W9, rev 5a). `total` is
    ignored without a key: the backend rejects an unkeyed `<I{i}/{M}>`."""
    if key is None:
        return f"<I{i}>"
    if total is None:
        return f"<I{key}.{i}>"
    return f"<I{key}.{i}/{int(total)}>"


def warn_retired_media_gid(config_path):
    """True, with one loud line, if the YAML still enables the retired Sprint10
    `media_gid:` island. The unit ignores it and runs the key/legacy wire: a stale
    island must never fail a boot (DESIGN_supervisor.md §8.3 S1 step 3). Deploy and migrate
    refuse such a file (S2)."""
    enabled = False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            in_island = False
            for raw in f:
                line = raw.split("#", 1)[0].rstrip()
                if not line.strip():
                    continue
                if not line.startswith(" "):
                    in_island = line.strip() == "media_gid:"
                    continue
                if in_island and ":" in line:
                    k, v = (x.strip() for x in line.split(":", 1))
                    if k == "enabled" and v.strip("'\"").lower() == "true":
                        enabled = True
    except OSError:
        return False
    if enabled:
        print("[KEY][WARN] media_gid.enabled is true in the YAML, but media_gid was retired "
              "by wire rev 5 and is IGNORED. Remove the media_gid island.")
    return enabled


def load_media_key_config(config_path):
    """Read the `media_key:` island (tolerant flat-island line parser). Absent =
    disabled. Raises ValueError on a bad value, naming the key."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            in_island = False
            for raw in f:
                line = raw.split("#", 1)[0].rstrip()
                if not line.strip():
                    continue
                if not line.startswith(" "):
                    in_island = line.strip() == "media_key:"
                    if in_island:
                        cfg["source"] = "yaml"
                    continue
                if not in_island or ":" not in line:
                    continue
                k, v = (x.strip() for x in line.split(":", 1))
                if k == "enabled":
                    if v.lower() not in ("true", "false"):
                        raise ValueError(f"media_key.enabled must be true|false, got {v!r}")
                    cfg["enabled"] = v.lower() == "true"
                elif k == "retain_days":
                    try:
                        cfg["retain_days"] = float(v)
                    except ValueError:
                        raise ValueError(f"media_key.retain_days must be a number, got {v!r}")
                    if not 0 < cfg["retain_days"] <= HARD_CAP_RETAIN_DAYS:
                        raise ValueError(f"media_key.retain_days must be in (0, {HARD_CAP_RETAIN_DAYS}]")
                elif k == "heal_order":
                    v = v.strip("'\"").lower()
                    if v not in HEAL_ORDERS:
                        raise ValueError("media_key.heal_order must be "
                                         f"{'|'.join(HEAL_ORDERS)}, got {v!r}")
                    cfg["heal_order"] = v
    except OSError:
        pass
    return cfg


def print_media_key_settings(cfg):
    order = cfg.get("heal_order", DEFAULT_HEAL_ORDER)
    tag = "" if order == DEFAULT_HEAL_ORDER else f" heal_order={order}"   # default: line unchanged
    print(f"[KEY] media_key: enabled={cfg['enabled']} retain_days={cfg['retain_days']:g} "
          f"source={cfg['source']}{tag}")


def key_for_this_wake(cfg, gate_info=None, daemon=None, state_path=DEFAULT_STATE_PATH):
    """One call per wake: the key (or None) plus a loud log line either way."""
    if not cfg.get("enabled"):
        return None
    utc_dt, source = spotter_utc_for_wake(gate_info, daemon)
    key, why = allocate_key(utc_dt, source, state_path=state_path)
    if key is None:
        print(f"[KEY][WARN] no media key this wake ({why}) -> legacy <I{{n}}> wire")
    else:
        print(f"[KEY] media key {key} from Spotter UTC {utc_dt.isoformat()} ({why})")
    return key


# --- sent record --------------------------------------------------------------------------

def write_sent_record(sent_dir, stem, *, key, fmt, filename, chunk_b64_chars, msgs, sha256,
                      payload_bytes=None, payload_path=None, now=None, chunk_total=False):
    """Persist what is about to be chunked, BEFORE START. Video passes payload_bytes (kept
    as <stem>.sent); images pass payload_path (the JPEG on disk IS the wire bytes).
    chunk_total (W9): the chunks go out as `<I{key}.{n}/{msgs}>`; recorded so a heal
    re-sends them byte-identical. Absent from the sidecar when False (pre-W9 bytes).
    Returns the sidecar path."""
    os.makedirs(sent_dir, exist_ok=True)
    if payload_bytes is not None:
        payload_path = os.path.join(sent_dir, f"{stem}.sent")
        _atomic_write(payload_path, payload_bytes, mode="wb")
    record = {
        "key": key, "fmt": fmt, "filename": filename,
        "chunk_b64_chars": int(chunk_b64_chars), "msgs": int(msgs), "sha256": sha256,
        "payload": payload_path,
        "sent_utc": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if chunk_total:
        record["chunk_total"] = True
    sidecar = os.path.join(sent_dir, f"{stem}.sent.json")
    _atomic_write(sidecar, json.dumps(record, indent=1) + "\n")
    return sidecar


def retain_seconds(retain_days):
    """retain_days -> seconds, with the 30 d hard cap."""
    return min(float(retain_days), HARD_CAP_RETAIN_DAYS) * 86400.0


def record_key_s(sidecar_path):
    """Key time of a sent record (s since EPOCH = the Spotter UTC of the wake that sent
    it), or None when the sidecar is unreadable or its key malformed (undatable)."""
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            return decode_key((json.load(f) or {}).get("key"))
    except (OSError, ValueError, AttributeError):
        return None


def expired_times(times, retain_s, now_s=None):
    """Which record times have aged out -> (expired set, n_behind_gap).

    The reference is now_s (this wake's key time) when given, else the newest record:
    never the Pi clock. Walking back from the reference, a time older than reference -
    retain_s expires, but a gap > retain_s between two neighbouring times stops the
    walk: everything older than that gap is kept (n_behind_gap). A gap that long is a
    clock jump or a long power-off, and the device cannot tell which; keeping costs a
    few MB, deleting could cost every heal record. With now_s, a newest record more
    than retain_s before now_s is such a gap, so nothing expires (the jump guard)."""
    points = sorted(set(times), reverse=True)
    if now_s is not None:
        points = sorted(set(points) | {now_s}, reverse=True)
    if not points:
        return set(), 0
    cutoff = points[0] - float(retain_s)
    expired = set()
    for i in range(1, len(points)):
        if points[i - 1] - points[i] > retain_s:
            behind = set(points[i:]) - ({now_s} if now_s is not None else set())
            return expired, len(behind)
        if points[i] < cutoff:
            expired.add(points[i])
    return expired, 0


def prune_sent(sent_dir, retain_days, now_key):
    """Delete sent records (<stem>.sent.json + its <stem>.sent payload) whose key time
    is older than retain_days (hard cap 30 d) before now_key, this wake's freshly
    allocated key. now_key None (no trusted Spotter UTC this wake) -> nothing is
    pruned. Undatable records and records behind a gap > retain_days are kept (loud).
    Never raises; returns the number of files removed."""
    if now_key is None:
        return 0
    retain = retain_seconds(retain_days)
    try:
        now_s = decode_key(now_key)
        names = os.listdir(sent_dir)
    except (OSError, ValueError):
        return 0
    stems = {}
    for name in names:
        if name.endswith(".sent.json"):
            t = record_key_s(os.path.join(sent_dir, name))
            if t is not None:
                stems[name[: -len(".sent.json")]] = t
    expired, behind = expired_times(stems.values(), retain, now_s=now_s)
    if behind:
        print(f"[KEY][WARN] {behind} sent record(s) behind a gap > {retain / 86400:g} d kept "
              f"(clock jump or long power-off; never aged across it)")
    removed = 0
    for stem, t in sorted(stems.items()):
        if t not in expired:
            continue
        for path in (os.path.join(sent_dir, stem + ".sent.json"),
                     os.path.join(sent_dir, stem + ".sent")):
            try:
                os.remove(path)
                removed += 1
            except OSError:          # absent (images keep no .sent) or vanished
                continue
    if removed:
        print(f"[KEY] pruned {removed} sent file(s) older than {retain / 86400:g} d "
              f"(by key time) from {sent_dir}")
    return removed


def find_sent_record(sent_dir, key):
    """The sidecar dict for `key`, or None (S5 heal lookup)."""
    try:
        names = sorted(os.listdir(sent_dir))
    except OSError:
        return None
    for name in names:
        if name.endswith(".sent.json"):
            try:
                with open(os.path.join(sent_dir, name), "r", encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            if rec.get("key") == key:
                return rec
    return None


def prepare_keyed_send(settings, *, gate_info, daemon, stem, fmt, filename, payload,
                       chunk_b64_chars, payload_path=None, sent_dir=None, state_path=None,
                       chunk_total=False):
    """Once per wake, right before START (both cycles): allocate this wake's key from
    the Spotter UTC (None -> legacy wire), then prune old sent records against that key
    (no key -> no prune: never on the Pi clock), then persist what is about to be
    chunked. chunk_total (W9): the caller's once-per-send decision,
    the same value it passes to the transmit function. Never raises: a failure here
    costs the key (legacy wire), never the capture or the send."""
    cfg = settings.get("media_key_cfg") or {}
    if not cfg.get("enabled"):
        return None
    sent_dir = sent_dir or cfg.get("sent_dir") or DEFAULT_SENT_DIR
    try:
        key = key_for_this_wake(cfg, gate_info=gate_info, daemon=daemon,
                                state_path=state_path or cfg.get("state_path") or DEFAULT_STATE_PATH)
        if key is None:
            return None
        prune_sent(sent_dir, cfg["retain_days"], key)
        msgs = -(-len(base64.b64encode(payload)) // int(chunk_b64_chars))
        sidecar = write_sent_record(
            sent_dir, stem, key=key, fmt=fmt, filename=filename,
            chunk_b64_chars=chunk_b64_chars, msgs=msgs,
            sha256=hashlib.sha256(payload).hexdigest(),
            payload_bytes=None if payload_path else payload, payload_path=payload_path,
            chunk_total=chunk_total)
        print(f"[KEY] sent record: {sidecar} ({msgs} msgs"
              + (f", chunks <I{key}.n/{msgs}>" if chunk_total else "") + ")")
        return key
    except Exception as exc:
        print(f"[KEY][WARN] keyed send setup failed ({exc}) -> legacy <I{{n}}> wire")
        return None


def key_time(key):
    """UTC datetime a key encodes (for logs)."""
    return EPOCH + timedelta(seconds=decode_key(key))
