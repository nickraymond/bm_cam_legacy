#!/usr/bin/env python3
# filename: command_state.py
# description: Sprint10 — persisted command settings + last-N dedupe store.
"""
Sprint10 command daemon — the one file that survives power cycles.

Holds the applied settings (roi/foc/awb/exp/win indices) and the last-N
applied command ids (dedupe, DESIGN D4) in a single JSON state file.
One file = one atomic write path: every change rewrites tmp + os.replace
+ fsync, so the Spotter cutting power mid-write can never leave a
half-written state file (Q10: every active window is a fresh process
that must reload this file at start).

File shape (schema versioned for future migration):
  {"schema": "bm_command_state_v1", "tables_version": 1,
   "settings": {"roi": 2, "foc": 0, "awb": 0, "exp": 0, "win": 0},
   "touched": ["roi"],
   "applied_ids": [415, 416, 417],
   "pending_trigger": {"id": 418, "value": 2},
   "pending_heals": [{"key": "0dhnso", "n": [17, 40], "id": 100003,
                      "wakes_left": 3}]}

`pending_heals` (Sprint25 S5, tables v8) is the rsd heal list: chunks of an
already-sent keyed media to re-send before the next START (rc_heal). Newest
first, one entry per key (a newer command for the same key replaces it), at
most PENDING_HEALS_MAX. Each entry lives `wakes_left` wakes, then rc_heal
drops it with `<HL a=dropped>`. Absent in older files (loads as []); older
code ignores the extra key.

`pending_trigger` (Sprint12) is the armed one-shot `trg` action, or
null/absent. It is NOT a setting: the next boot consume_trigger()s it —
cleared and persisted BEFORE the cycle acts on it, so a crash during the
triggered cycle can never re-fire it every boot. `trg 0` cancels. A v2
state file simply lacks the key (loads as None); a v3 file read by v2
code carries an ignored extra key — compatible both directions.

`touched` records which settings keys were EVER commanded. The overlay
(command_bindings.py) only overrides keys in `touched`, so a unit whose
YAML sets manual focus keeps it until focus is explicitly commanded —
index 0 means "commanded back to default/auto", absence from `touched`
means "never commanded, YAML wins". Ack `st` still reports all five
index values (0 for never-commanded keys).

Load is tolerant and loud (CLAUDE.md "fail loudly"): a missing file is
normal first boot (factory defaults); a corrupt file or out-of-table
value falls back to defaults per-key with a printed warning — a bad
state file must never brick the capture loop (D3 blast-radius rule).

Default path is the deployed runtime dir (not the git checkout), same
convention as bm_serial.py; override with BM_COMMAND_STATE_PATH or the
constructor for tests.

Example:
  >>> state = CommandState(path="/tmp/state.json")
  >>> state.is_duplicate(417)
  False
  >>> state.record(417, "roi", 2)   # persists before returning
  >>> state.settings["roi"]
  2

Known limitations: not safe for concurrent writers (fine — Q10: one
per-wake process, and D2 gives the daemon a single apply point).
"""

import json
import os

import re

from command_tables import (
    ACTION_COMMANDS,
    DEFAULT_SETTINGS,
    HEAL_COMMANDS,
    SETTINGS_COMMANDS,
    TABLES_VERSION,
    valid_value,
)

STATE_SCHEMA = "bm_command_state_v1"
# Sprint26 S2 (PLAN_S2.md G1): on a config-v2 unit the same v8 body lives in
# the `v8` section of bm_command_state_v2.json. Every other top-level field of
# that file (boot counter, v2 overlay, result cache, high-water marks, ...) is
# kept byte-for-byte on save: this class owns only the v8 section until S4.
STATE_SCHEMA_V2 = "bm_command_state_v2"
V2_SKELETON = {"boot_counter": 0, "overlay": {}, "guarded": {}, "result_cache": {},
               "high_water": {}}

DEFAULT_STATE_PATH = os.environ.get(
    "BM_COMMAND_STATE_PATH",
    "/home/pi/BM_Devel_Pi/bm_command_state.json",
)

# Dedupe depth. Sofar's cloud queue is shallow (Spotter holds 2 slots;
# bursts on wake are a handful of queued commands) — 32 ids is far more
# history than one duty cycle can deliver, at ~6 bytes/id in the file.
DEDUPE_KEEP = 32

# Sprint25 S5 heal list (SPEC_resend_heal.md §5).
PENDING_HEALS_MAX = 8
HEAL_WAKES = 3
_RE_KEY = re.compile(r"^[0-9a-z]{6}$")


def _valid_heal(item):
    """A persisted pending heal, or None if anything is off (tolerant load)."""
    if not isinstance(item, dict):
        return None
    key, ns, hid, wakes = (item.get(k) for k in ("key", "n", "id", "wakes_left"))
    if not isinstance(key, str) or not _RE_KEY.match(key):
        return None
    if (not isinstance(ns, list) or not ns
            or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in ns)):
        return None
    if isinstance(hid, bool) or not isinstance(hid, int):
        return None
    if isinstance(wakes, bool) or not isinstance(wakes, int) or not 1 <= wakes <= HEAL_WAKES:
        return None
    return {"key": key, "n": sorted(set(ns)), "id": hid, "wakes_left": wakes}


class CommandState:
    """Applied settings + dedupe ids, persisted atomically on change."""

    def __init__(self, path=None):
        self.path = path or DEFAULT_STATE_PATH
        # v2 file: by name for a new file, by schema for an existing one.
        self.is_v2 = os.path.basename(self.path).endswith("_v2.json")
        self.v2_fields = dict(V2_SKELETON)
        self.settings = dict(DEFAULT_SETTINGS)
        self.touched = set()
        self.applied_ids = []
        self.pending_trigger = None  # Sprint12: armed one-shot trg, or None
        self.pending_heals = []      # Sprint25 S5: rsd heal list, newest first
        # Load provenance for the daemon's startup log line.
        self.load_info = {"source": "defaults", "reset_keys": [], "error": None}
        self._load()

    # ------------------------------------------------------------------
    # Load (boot path)
    # ------------------------------------------------------------------

    def _load(self):
        if not os.path.exists(self.path):
            return  # first boot: factory defaults, nothing to report

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("state file is not a JSON object")
        except Exception as exc:
            self.load_info["error"] = str(exc)
            print(f"[CMD][WARN] state file unreadable ({exc}); using factory defaults")
            return

        self.load_info["source"] = "file"

        if data.get("schema") == STATE_SCHEMA_V2:
            self.is_v2 = True
            self.v2_fields = {k: v for k, v in data.items()
                              if k not in ("schema", "tables_version", "v8")}
            data = data.get("v8") if isinstance(data.get("v8"), dict) else {}

        raw_settings = data.get("settings")
        if not isinstance(raw_settings, dict):
            raw_settings = {}
        for cmd in SETTINGS_COMMANDS:
            value = raw_settings.get(cmd, DEFAULT_SETTINGS[cmd])
            if valid_value(cmd, value):
                self.settings[cmd] = value
            else:
                # Out-of-table value (e.g. tables changed between deploys):
                # reset just that key, keep the rest of the field fix.
                self.load_info["reset_keys"].append(cmd)
                print(f"[CMD][WARN] state {cmd}={value!r} not in tables; reset to "
                      f"{DEFAULT_SETTINGS[cmd]}")

        raw_touched = data.get("touched")
        if isinstance(raw_touched, list):
            self.touched = {
                cmd for cmd in raw_touched
                if cmd in SETTINGS_COMMANDS and cmd not in self.load_info["reset_keys"]
            }

        raw_ids = data.get("applied_ids")
        if isinstance(raw_ids, list):
            self.applied_ids = [
                i for i in raw_ids if isinstance(i, int) and not isinstance(i, bool)
            ][-DEDUPE_KEEP:]

        # Sprint12: pending one-shot trigger. Absent (v2 file) or null is
        # normal; anything malformed or out-of-table is dropped loudly —
        # a bad trigger must never brick or surprise-fire the boot.
        raw_trigger = data.get("pending_trigger")
        if isinstance(raw_trigger, dict):
            trig_id = raw_trigger.get("id")
            trig_value = raw_trigger.get("value")
            id_ok = isinstance(trig_id, int) and not isinstance(trig_id, bool)
            if id_ok and valid_value("trg", trig_value) and trig_value != 0:
                self.pending_trigger = {"id": trig_id, "value": trig_value}
            else:
                print(f"[CMD][WARN] pending_trigger {raw_trigger!r} invalid; "
                      "dropped")
        elif raw_trigger is not None:
            print(f"[CMD][WARN] pending_trigger {raw_trigger!r} not an object; "
                  "dropped")

        # Sprint25 S5: pending heals. Bad entries drop one by one, loudly.
        raw_heals = data.get("pending_heals")
        if isinstance(raw_heals, list):
            for item in raw_heals:
                heal = _valid_heal(item)
                if heal is None:
                    print(f"[CMD][WARN] pending heal {item!r} invalid; dropped")
                elif all(h["key"] != heal["key"] for h in self.pending_heals):
                    self.pending_heals.append(heal)
            self.pending_heals = self.pending_heals[:PENDING_HEALS_MAX]
        elif raw_heals is not None:
            print(f"[CMD][WARN] pending_heals {raw_heals!r} not a list; dropped")

    # ------------------------------------------------------------------
    # Dedupe + record (apply path)
    # ------------------------------------------------------------------

    def is_duplicate(self, command_id):
        """True if this command id was already applied (D4: ack, don't
        re-apply)."""
        return command_id in self.applied_ids

    def record(self, command_id, cmd, value):
        """Record a successfully processed command and persist.

        Settings commands update their key; action commands (trg) arm or
        cancel the pending one-shot; ping only records its id. Call AFTER
        the setting is accepted for apply — a rejected command must never
        enter the dedupe store (a corrected re-send with the same id
        semantics is not expected, but rejects also don't change state,
        so recording them would only bloat the file).
        """
        journal = None
        if cmd in SETTINGS_COMMANDS:
            if self.is_v2:
                journal = (cmd, self.settings[cmd] if cmd in self.touched else None, value)
            self.settings[cmd] = value
            self.touched.add(cmd)
        elif cmd in ACTION_COMMANDS:
            # trg 0 cancels; any other index arms (re-arming with a new id
            # replaces the previous pending trigger — last command wins).
            if value == 0:
                self.pending_trigger = None
            else:
                self.pending_trigger = {"id": command_id, "value": value}
        elif cmd in HEAL_COMMANDS:
            self._record_heals(command_id, value)
        if not self.is_duplicate(command_id):
            self.applied_ids.append(command_id)
            self.applied_ids = self.applied_ids[-DEDUPE_KEEP:]
        self.save()
        if journal is not None:
            # Sprint26 S2e: a config-v2 unit journals every v8 setting change
            # AFTER the state is persisted (the state is the truth; a journal
            # failure is logged, never fatal). old None = was not overridden.
            try:
                import config_journal
                config_journal.append(config_journal.path_beside(self.path), "v8",
                                      key=f"v8.{journal[0]}", old=journal[1],
                                      new=journal[2], cid=command_id)
            except Exception as exc:   # the state IS saved: never turn that into an err ack
                print(f"[CMD][WARN] config journal not written: {exc}")

    def _record_heals(self, command_id, value):
        """rsd: {"x": 1} cancels every pending heal; {"h": [[key, ns], ...]}
        holds the ACCEPTED heals (the daemon already removed refused ones — an
        all-refused command arrives as {"h": []} and changes nothing but the id
        record, so a re-send is acked-duplicate, not re-refused; spec §5).
        A key already pending is replaced (newest id wins); the command's heals
        go to the front in command order; the list keeps PENDING_HEALS_MAX."""
        value = value or {}
        if value.get("x"):
            self.pending_heals = []
            return
        new = [{"key": key, "n": sorted(set(ns)), "id": command_id,
                "wakes_left": HEAL_WAKES} for key, ns in value.get("h", [])]
        new_keys = {h["key"] for h in new}
        kept = [h for h in self.pending_heals if h["key"] not in new_keys]
        merged = new + kept
        for evicted in merged[PENDING_HEALS_MAX:]:
            print(f"[CMD][WARN] pending heal list full; oldest heal "
                  f"key={evicted['key']} id={evicted['id']} evicted")
        self.pending_heals = merged[:PENDING_HEALS_MAX]

    def set_pending_heals(self, heals):
        """rc_heal's end-of-wake update (sent/dropped heals removed,
        wakes_left decremented). Persists; raises on I/O failure."""
        self.pending_heals = [h for h in (_valid_heal(x) for x in heals) if h][:PENDING_HEALS_MAX]
        self.save()

    def consume_trigger(self):
        """Take the pending one-shot trigger, clearing it FIRST (boot path).

        Returns the {"id", "value"} dict or None. The clear is persisted
        before the caller acts, so a cycle that crashes while servicing
        the trigger cannot re-fire it on every subsequent boot. If the
        persist of the clear fails, the trigger is NOT returned — one
        extra quiet boot beats a capture loop.
        """
        if self.pending_trigger is None:
            return None
        trigger = self.pending_trigger
        self.pending_trigger = None
        try:
            self.save()
        except Exception as exc:
            print(f"[CMD][ERROR] could not persist trigger consume: {exc}; "
                  "trigger NOT serviced this boot")
            self.pending_trigger = trigger
            return None
        return trigger

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------

    def save(self):
        """Atomic write (atomic_io): unique tmp + fsync + os.replace + dir fsync.
        Raises on I/O failure — the caller decides whether an unpersisted
        apply should still ack (daemon policy, §2)."""
        body = {
            "settings": {cmd: self.settings[cmd] for cmd in SETTINGS_COMMANDS},
            "touched": sorted(self.touched),
            "applied_ids": list(self.applied_ids),
            "pending_trigger": self.pending_trigger,
            "pending_heals": list(self.pending_heals),
        }
        if self.is_v2:
            payload = dict(self.v2_fields, schema=STATE_SCHEMA_V2,
                           tables_version=TABLES_VERSION, v8=body)
        else:
            payload = dict(schema=STATE_SCHEMA, tables_version=TABLES_VERSION, **body)
        # Sprint26 S2e: unique tmp name + fsync + rename + fsync of the
        # directory (atomic_io); the bytes are the same as before.
        import atomic_io
        atomic_io.write_text(self.path, json.dumps(payload, separators=(",", ":")))
