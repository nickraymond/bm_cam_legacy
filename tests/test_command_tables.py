#!/usr/bin/env python3
# filename: test_command_tables.py
# description: Sprint10 §1 — invariants for the command value tables.
"""
Sprint10 — tests for command_tables.py (single source of truth).

Pins the SPEC contract: the six v1 commands, factory-zero defaults, the
S07-validated ROI default rect, geometric sanity of every ROI preset
(in-bounds, 16:9, never upsampled), and strict index validation (bools
and strings are not indices).

Run (repo root; pure module, no hardware):
  python3 -m unittest tests.test_command_tables -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_tables as ct  # noqa: E402


class TestCommandSet(unittest.TestCase):
    def test_v1_command_set_exact(self):
        self.assertEqual(ct.COMMANDS, ("roi", "foc", "awb", "exp", "win", "ping"))

    def test_settings_commands_exclude_ping(self):
        self.assertEqual(ct.SETTINGS_COMMANDS, ("roi", "foc", "awb", "exp", "win"))

    def test_factory_defaults_all_zero(self):
        self.assertEqual(
            ct.DEFAULT_SETTINGS, {"roi": 0, "foc": 0, "awb": 0, "exp": 0, "win": 0}
        )

    def test_every_command_has_a_table_with_index_zero(self):
        for cmd in ct.COMMANDS:
            table = ct.table_for(cmd)
            self.assertIn(0, table, f"{cmd}: index 0 (default) missing")

    def test_every_entry_has_a_label(self):
        for cmd in ct.COMMANDS:
            for v, entry in ct.table_for(cmd).items():
                self.assertTrue(entry.get("label"), f"{cmd}[{v}]: label missing")


class TestRoiGeometry(unittest.TestCase):
    def test_index_zero_is_production_default(self):
        # Must match progressive_jpeg.crop in camera_schedule.yaml
        # (S07 byte-validated exact center crop).
        self.assertEqual(ct.ROI_TABLE[0]["crop"], (1504, 846, 1600, 900))

    def test_index_one_is_full_frame(self):
        self.assertEqual(ct.ROI_TABLE[1]["crop"], (0, 0, 4608, 2592))

    def test_all_crops_within_native_bounds(self):
        for v, entry in ct.ROI_TABLE.items():
            x, y, w, h = entry["crop"]
            self.assertGreaterEqual(x, 0, f"roi[{v}]")
            self.assertGreaterEqual(y, 0, f"roi[{v}]")
            self.assertLessEqual(x + w, ct.NATIVE_WIDTH, f"roi[{v}]")
            self.assertLessEqual(y + h, ct.NATIVE_HEIGHT, f"roi[{v}]")

    def test_all_crops_are_16_9(self):
        for v, entry in ct.ROI_TABLE.items():
            x, y, w, h = entry["crop"]
            self.assertAlmostEqual(
                w / h, 16 / 9, places=2, msg=f"roi[{v}] {w}x{h} not 16:9"
            )

    def test_no_crop_upsamples_at_output(self):
        # SPEC: the 1000-wide floor guarantees output never upsamples.
        for v, entry in ct.ROI_TABLE.items():
            w = entry["crop"][2]
            self.assertGreaterEqual(
                w, ct.ROI_OUTPUT_WIDTH, f"roi[{v}] narrower than output width"
            )

    def test_zoom_presets_are_centered(self):
        # Q3: zoom only, no pan — every preset (incl. full frame) centers
        # on the native frame.
        for v, entry in ct.ROI_TABLE.items():
            x, y, w, h = entry["crop"]
            self.assertEqual(x, (ct.NATIVE_WIDTH - w) // 2, f"roi[{v}] x not centered")
            self.assertEqual(y, (ct.NATIVE_HEIGHT - h) // 2, f"roi[{v}] y not centered")


class TestOtherTables(unittest.TestCase):
    def test_foc_zero_is_auto(self):
        self.assertEqual(ct.FOC_TABLE[0]["mode"], "auto")
        self.assertIsNone(ct.FOC_TABLE[0]["lens_position"])

    def test_foc_manual_entries_have_numeric_lens_position(self):
        for v, entry in ct.FOC_TABLE.items():
            if v == 0:
                continue
            self.assertEqual(entry["mode"], "manual", f"foc[{v}]")
            self.assertIsInstance(entry["lens_position"], float, f"foc[{v}]")
            self.assertGreaterEqual(entry["lens_position"], 0.0, f"foc[{v}]")

    def test_awb_modes(self):
        self.assertEqual(ct.AWB_TABLE[0]["mode"], "auto")
        self.assertEqual(ct.AWB_TABLE[1]["mode"], "daylight")
        self.assertEqual(ct.AWB_TABLE[2]["mode"], "cloudy")
        self.assertEqual(ct.AWB_TABLE[3]["mode"], "custom")
        # Custom preset must carry both gains (rpicam --awbgains R,B).
        gains = ct.AWB_TABLE[3]["gains"]
        self.assertEqual(len(gains), 2)
        for g in gains:
            self.assertGreater(g, 0.0)

    def test_exp_zero_is_auto_and_steps_are_floats(self):
        self.assertIsNone(ct.EXP_TABLE[0]["ev"])
        for v, entry in ct.EXP_TABLE.items():
            if v == 0:
                continue
            self.assertIsInstance(entry["ev"], float, f"exp[{v}]")

    def test_win_values_fixed_by_spec(self):
        minutes = {v: e["minutes"] for v, e in ct.WIN_TABLE.items()}
        self.assertEqual(minutes, {0: 16, 1: 12, 2: 8, 3: 5})


class TestLookupHelpers(unittest.TestCase):
    def test_valid_value_accepts_known_indices(self):
        for cmd in ct.COMMANDS:
            for v in ct.table_for(cmd):
                self.assertTrue(ct.valid_value(cmd, v), f"{cmd}[{v}]")

    def test_valid_value_rejects_unknown_index(self):
        self.assertFalse(ct.valid_value("roi", 99))
        self.assertFalse(ct.valid_value("win", -1))
        self.assertFalse(ct.valid_value("ping", 1))

    def test_valid_value_rejects_non_int(self):
        self.assertFalse(ct.valid_value("roi", "2"))
        self.assertFalse(ct.valid_value("roi", 2.0))
        self.assertFalse(ct.valid_value("roi", None))
        # JSON true must never alias index 1 (bool is an int subclass).
        self.assertFalse(ct.valid_value("roi", True))

    def test_valid_value_rejects_unknown_command(self):
        self.assertFalse(ct.valid_value("zoom", 0))

    def test_command_options_sorted_with_labels(self):
        for cmd in ct.COMMANDS:
            options = ct.command_options(cmd)
            indices = [v for v, _label in options]
            self.assertEqual(indices, sorted(ct.table_for(cmd)))
            for _v, label in options:
                self.assertTrue(label)

    def test_is_command(self):
        for cmd in ct.COMMANDS:
            self.assertTrue(ct.is_command(cmd))
        self.assertFalse(ct.is_command("reboot"))


if __name__ == "__main__":
    unittest.main()
