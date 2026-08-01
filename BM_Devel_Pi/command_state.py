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
   "pending_trigger": {"id": 418, "value": 2}}

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

from command_tables import (
    ACTION_COMMANDS,
    DEFAULT_SETTINGS,
    SETTINGS_COMMANDS,
    TABLES_VERSION,
    valid_value,
)

STATE_SCHEMA = "bm_command_state_v1"

DEFAULT_STATE_PATH = os.environ.get(
    "BM_COMMAND_STATE_PATH",
    "/home/pi/BM_Devel_Pi/bm_command_state.json",
)

# Dedupe depth. Sofar's cloud queue is shallow (Spotter holds 2 slots;
# bursts on wake are a handful of queued commands) — 32 ids is far more
# history than one duty cycle can deliver, at ~6 bytes/id in the file.
DEDUPE_KEEP = 32


class CommandState:
    """Applied settings + dedupe ids, persisted atomically on change."""

    def __init__(self, path=None):
        self.path = path or DEFAULT_STATE_PATH
        self.settings = dict(DEFAULT_SETTINGS)
        self.touched = set()
        self.applied_ids = []
        self.pending_trigger = None  # Sprint12: armed one-shot trg, or None
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
        if cmd in SETTINGS_COMMANDS:
            self.settings[cmd] = value
            self.touched.add(cmd)
        elif cmd in ACTION_COMMANDS:
            # trg 0 cancels; any other index arms (re-arming with a new id
            # replaces the previous pending trigger — last command wins).
            if value == 0:
                self.pending_trigger = None
            else:
                self.pending_trigger = {"id": command_id, "value": value}
        if not self.is_duplicate(command_id):
            self.applied_ids.append(command_id)
            self.applied_ids = self.applied_ids[-DEDUPE_KEEP:]
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
        """Atomic write: tmp file in the same dir + fsync + os.replace.
        Raises on I/O failure — the caller decides whether an unpersisted
        apply should still ack (daemon policy, §2)."""
        payload = {
            "schema": STATE_SCHEMA,
            "tables_version": TABLES_VERSION,
            "settings": {cmd: self.settings[cmd] for cmd in SETTINGS_COMMANDS},
            "touched": sorted(self.touched),
            "applied_ids": list(self.applied_ids),
            "pending_trigger": self.pending_trigger,
        }
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.path)
