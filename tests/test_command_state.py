#!/usr/bin/env python3
# filename: test_command_state.py
# description: Sprint10 §1/§4 — dedupe + settings persistence across restart.
"""
Sprint10 — tests for command_state.py.

Covers the TRACKER §4 units "dedupe (duplicate ID -> ack, no re-apply)"
and "state persistence across simulated restart": a restart is simulated
by constructing a fresh CommandState on the same path (Q10: every active
window is a new process, so this IS the boot path). Also pins corrupt-
file recovery, per-key reset of out-of-table values, last-N id trim, and
that saves are atomic (no .tmp residue).

Run (repo root; tmpdir only, no hardware):
  python3 -m unittest tests.test_command_state -v
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_tables as ct  # noqa: E402
from command_state import DEDUPE_KEEP, STATE_SCHEMA, CommandState  # noqa: E402


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "bm_command_state.json")


class TestFirstBoot(StateTestCase):
    def test_missing_file_gives_factory_defaults(self):
        state = CommandState(path=self.path)
        self.assertEqual(state.settings, ct.DEFAULT_SETTINGS)
        self.assertEqual(state.applied_ids, [])
        self.assertEqual(state.load_info["source"], "defaults")

    def test_no_file_created_until_first_record(self):
        CommandState(path=self.path)
        self.assertFalse(os.path.exists(self.path))


class TestPersistence(StateTestCase):
    def test_record_persists_across_restart(self):
        state = CommandState(path=self.path)
        state.record(417, "roi", 2)
        state.record(418, "win", 3)

        rebooted = CommandState(path=self.path)  # simulated power cycle
        self.assertEqual(rebooted.settings["roi"], 2)
        self.assertEqual(rebooted.settings["win"], 3)
        self.assertEqual(rebooted.settings["foc"], 0)
        self.assertIn(417, rebooted.applied_ids)
        self.assertIn(418, rebooted.applied_ids)
        self.assertEqual(rebooted.load_info["source"], "file")

    def test_ping_records_id_but_not_settings(self):
        state = CommandState(path=self.path)
        state.record(5, "ping", 0)
        rebooted = CommandState(path=self.path)
        self.assertEqual(rebooted.settings, ct.DEFAULT_SETTINGS)
        self.assertTrue(rebooted.is_duplicate(5))

    def test_factory_reset_sequence(self):
        state = CommandState(path=self.path)
        state.record(1, "roi", 4)
        state.record(2, "awb", 3)
        # SPEC factory reset: all-zero command sequence.
        for i, cmd in enumerate(ct.SETTINGS_COMMANDS, start=10):
            state.record(i, cmd, 0)
        rebooted = CommandState(path=self.path)
        self.assertEqual(rebooted.settings, ct.DEFAULT_SETTINGS)

    def test_file_shape_and_schema(self):
        state = CommandState(path=self.path)
        state.record(417, "roi", 2)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["schema"], STATE_SCHEMA)
        self.assertEqual(data["tables_version"], ct.TABLES_VERSION)
        self.assertEqual(sorted(data["settings"]), sorted(ct.SETTINGS_COMMANDS))
        self.assertEqual(data["applied_ids"], [417])

    def test_save_leaves_no_tmp_residue(self):
        state = CommandState(path=self.path)
        state.record(1, "roi", 1)
        self.assertEqual(
            sorted(os.listdir(self.tmpdir.name)), ["bm_command_state.json"]
        )


class TestDedupe(StateTestCase):
    def test_duplicate_detection(self):
        state = CommandState(path=self.path)
        self.assertFalse(state.is_duplicate(417))
        state.record(417, "roi", 2)
        self.assertTrue(state.is_duplicate(417))

    def test_duplicate_survives_restart(self):
        state = CommandState(path=self.path)
        state.record(417, "roi", 2)
        rebooted = CommandState(path=self.path)
        self.assertTrue(rebooted.is_duplicate(417))

    def test_duplicate_record_does_not_duplicate_id(self):
        state = CommandState(path=self.path)
        state.record(417, "roi", 2)
        state.record(417, "roi", 2)
        self.assertEqual(state.applied_ids.count(417), 1)

    def test_last_n_trim(self):
        state = CommandState(path=self.path)
        for i in range(DEDUPE_KEEP + 10):
            state.record(i, "ping", 0)
        self.assertEqual(len(state.applied_ids), DEDUPE_KEEP)
        self.assertFalse(state.is_duplicate(0))  # oldest evicted
        self.assertTrue(state.is_duplicate(DEDUPE_KEEP + 9))


class TestCorruptRecovery(StateTestCase):
    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_corrupt_json_falls_back_to_defaults(self):
        self._write("{ this is not json")
        state = CommandState(path=self.path)
        self.assertEqual(state.settings, ct.DEFAULT_SETTINGS)
        self.assertIsNotNone(state.load_info["error"])

    def test_non_object_json_falls_back(self):
        self._write("[1, 2, 3]")
        state = CommandState(path=self.path)
        self.assertEqual(state.settings, ct.DEFAULT_SETTINGS)

    def test_out_of_table_value_resets_only_that_key(self):
        self._write(json.dumps({
            "schema": STATE_SCHEMA,
            "settings": {"roi": 99, "foc": 1, "awb": 0, "exp": 0, "win": 2},
            "applied_ids": [7],
        }))
        state = CommandState(path=self.path)
        self.assertEqual(state.settings["roi"], 0)      # reset
        self.assertEqual(state.settings["foc"], 1)      # kept
        self.assertEqual(state.settings["win"], 2)      # kept
        self.assertEqual(state.load_info["reset_keys"], ["roi"])

    def test_bad_types_in_settings_reset(self):
        self._write(json.dumps({
            "settings": {"roi": "2", "foc": True, "awb": None, "exp": 1.0, "win": 1},
            "applied_ids": "nope",
        }))
        state = CommandState(path=self.path)
        self.assertEqual(state.settings["roi"], 0)
        self.assertEqual(state.settings["foc"], 0)
        self.assertEqual(state.settings["awb"], 0)
        self.assertEqual(state.settings["exp"], 0)
        self.assertEqual(state.settings["win"], 1)
        self.assertEqual(state.applied_ids, [])

    def test_applied_ids_filtered_and_trimmed(self):
        self._write(json.dumps({
            "settings": dict(ct.DEFAULT_SETTINGS),
            "applied_ids": ["x", 1, True, 2.5, 3] + list(range(100, 100 + DEDUPE_KEEP)),
        }))
        state = CommandState(path=self.path)
        self.assertEqual(len(state.applied_ids), DEDUPE_KEEP)
        self.assertNotIn("x", state.applied_ids)
        self.assertNotIn(True, state.applied_ids)

    def test_corrupt_file_recovers_after_next_record(self):
        self._write("garbage")
        state = CommandState(path=self.path)
        state.record(1, "roi", 3)
        rebooted = CommandState(path=self.path)
        self.assertEqual(rebooted.settings["roi"], 3)
        self.assertIsNone(rebooted.load_info["error"])


class TestSprint12Settings(StateTestCase):
    """hlt/twn ride the existing settings machinery unchanged."""

    def test_hlt_twn_persist_across_restart(self):
        state = CommandState(path=self.path)
        state.record(500, "hlt", 2)
        state.record(501, "twn", 2)
        rebooted = CommandState(path=self.path)
        self.assertEqual(rebooted.settings["hlt"], 2)
        self.assertEqual(rebooted.settings["twn"], 2)
        self.assertEqual(rebooted.touched, {"hlt", "twn"})

    def test_v2_state_file_loads_with_sprint12_defaults(self):
        # A deployed unit's existing state file has no hlt/twn/pending_trigger
        # keys. It must load clean: defaults, no reset warnings, field fixes
        # (roi etc.) intact.
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "schema": STATE_SCHEMA, "tables_version": 2,
                "settings": {"roi": 2, "foc": 0, "awb": 0, "exp": 0,
                             "win": 0, "txd": 5, "cap": 0, "src": 1},
                "touched": ["roi", "txd", "src"],
                "applied_ids": [415, 416, 417],
            }, f)
        state = CommandState(path=self.path)
        self.assertEqual(state.settings["roi"], 2)      # field fix kept
        self.assertEqual(state.settings["hlt"], 0)      # default
        self.assertEqual(state.settings["twn"], 0)      # default
        self.assertIsNone(state.pending_trigger)
        self.assertEqual(state.load_info["reset_keys"], [])


class TestPendingTrigger(StateTestCase):
    def test_trg_arms_and_persists(self):
        state = CommandState(path=self.path)
        state.record(600, "trg", 2)
        self.assertEqual(state.pending_trigger, {"id": 600, "value": 2})
        # trg is an action, never a setting: settings/touched untouched.
        self.assertEqual(state.settings, ct.DEFAULT_SETTINGS)
        self.assertEqual(state.touched, set())
        rebooted = CommandState(path=self.path)
        self.assertEqual(rebooted.pending_trigger, {"id": 600, "value": 2})
        self.assertTrue(rebooted.is_duplicate(600))

    def test_consume_returns_once_and_clears_persistently(self):
        state = CommandState(path=self.path)
        state.record(600, "trg", 2)
        rebooted = CommandState(path=self.path)
        self.assertEqual(rebooted.consume_trigger(), {"id": 600, "value": 2})
        self.assertIsNone(rebooted.consume_trigger())
        # The clear survives a crash-after-consume: a fresh load (next
        # boot) must NOT see the trigger again.
        rebooted2 = CommandState(path=self.path)
        self.assertIsNone(rebooted2.consume_trigger())

    def test_trg_zero_cancels(self):
        state = CommandState(path=self.path)
        state.record(600, "trg", 2)
        state.record(601, "trg", 0)
        self.assertIsNone(state.pending_trigger)
        rebooted = CommandState(path=self.path)
        self.assertIsNone(rebooted.pending_trigger)

    def test_rearm_replaces_pending(self):
        state = CommandState(path=self.path)
        state.record(600, "trg", 1)
        state.record(601, "trg", 3)
        self.assertEqual(state.pending_trigger, {"id": 601, "value": 3})

    def test_duplicate_trg_id_does_not_rearm_after_consume(self):
        # Cloud re-send of an already-consumed trg must ack (dedupe) but
        # never fire a second image. The daemon checks is_duplicate BEFORE
        # record(); this pins the store side of that contract.
        state = CommandState(path=self.path)
        state.record(600, "trg", 2)
        self.assertEqual(state.consume_trigger(), {"id": 600, "value": 2})
        self.assertTrue(state.is_duplicate(600))
        self.assertIsNone(state.pending_trigger)

    def test_malformed_pending_trigger_dropped_on_load(self):
        for bad in ({"id": 1, "value": 99},        # out of table
                    {"id": 1, "value": 0},         # cancel is never pending
                    {"id": True, "value": 2},      # bool id
                    {"value": 2},                  # id missing
                    "trigger", 7, []):
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"schema": STATE_SCHEMA,
                           "settings": dict(ct.DEFAULT_SETTINGS),
                           "applied_ids": [],
                           "pending_trigger": bad}, f)
            state = CommandState(path=self.path)
            self.assertIsNone(state.pending_trigger, f"pending={bad!r}")

    def test_consume_with_unwritable_path_defers_trigger(self):
        # Persist-the-clear fails -> trigger must NOT be serviced this boot
        # (one quiet boot beats a capture loop) and must still be armed.
        state = CommandState(path=self.path)
        state.record(600, "trg", 2)
        state.path = os.path.join(self.tmpdir.name, "no_such_dir", "state.json")
        self.assertIsNone(state.consume_trigger())
        self.assertEqual(state.pending_trigger, {"id": 600, "value": 2})


if __name__ == "__main__":
    unittest.main()
