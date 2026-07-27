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


if __name__ == "__main__":
    unittest.main()
