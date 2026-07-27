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
   "applied_ids": [415, 416, 417]}

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
        self.applied_ids = []
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

        raw_ids = data.get("applied_ids")
        if isinstance(raw_ids, list):
            self.applied_ids = [
                i for i in raw_ids if isinstance(i, int) and not isinstance(i, bool)
            ][-DEDUPE_KEEP:]

    # ------------------------------------------------------------------
    # Dedupe + record (apply path)
    # ------------------------------------------------------------------

    def is_duplicate(self, command_id):
        """True if this command id was already applied (D4: ack, don't
        re-apply)."""
        return command_id in self.applied_ids

    def record(self, command_id, cmd, value):
        """Record a successfully processed command and persist.

        Settings commands update their key; ping only records its id.
        Call AFTER the setting is accepted for apply — a rejected command
        must never enter the dedupe store (a corrected re-send with the
        same id semantics is not expected, but rejects also don't change
        state, so recording them would only bloat the file).
        """
        if cmd in SETTINGS_COMMANDS:
            self.settings[cmd] = value
        if not self.is_duplicate(command_id):
            self.applied_ids.append(command_id)
            self.applied_ids = self.applied_ids[-DEDUPE_KEEP:]
        self.save()

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
            "applied_ids": list(self.applied_ids),
        }
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.path)
