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
    def test_v6_command_set_exact(self):
        # v6 adds wap (Sprint15 D-S15-10) — an IMMEDIATE command, neither
        # setting nor one-shot trigger.
        self.assertEqual(
            ct.COMMANDS,
            ("roi", "foc", "awb", "exp", "win", "txd", "cap", "src",
             "hlt", "twn", "tmz", "trg", "wap", "ping", "help", "cfg"),
        )
        self.assertEqual(ct.IMMEDIATE_COMMANDS, ("wap",))

    def test_settings_commands_exclude_ping_trg_and_queries(self):
        # trg is a one-shot ACTION (pending_trigger slot), never a setting;
        # help/cfg are QUERIES (Sprint13) — no state beyond the dedupe id.
        self.assertEqual(
            ct.SETTINGS_COMMANDS,
            ("roi", "foc", "awb", "exp", "win", "txd", "cap", "src",
             "hlt", "twn", "tmz"),
        )
        self.assertEqual(ct.ACTION_COMMANDS, ("trg",))
        self.assertEqual(ct.QUERY_COMMANDS, ("help", "cfg"))

    def test_factory_defaults_all_zero(self):
        self.assertEqual(
            ct.DEFAULT_SETTINGS,
            {"roi": 0, "foc": 0, "awb": 0, "exp": 0, "win": 0,
             "txd": 0, "cap": 0, "src": 0, "hlt": 0, "twn": 0, "tmz": 0}
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
        # v5: crops may be NARROWER than the 1000-wide output (reef-test
        # presets 5/6) — the overlay clamps output width to the crop width
        # so nothing ever upsamples. The invariant is now min(), matching
        # the clamp in command_bindings.overlay_rc_settings.
        from rc_jpeg_encoder import output_size_for_crop
        for v, entry in ct.ROI_TABLE.items():
            w, h = entry["crop"][2], entry["crop"][3]
            out_w = min(ct.ROI_OUTPUT_WIDTH, w)
            out = output_size_for_crop(w, h, out_w)  # raises on upsample
            self.assertLessEqual(out[0], w, f"roi[{v}] upsampled")

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
        # v5: the untuned custom-gain underwater preset (old index 3) was
        # dropped (Nick 2026-08-01 — nothing ships untested). Only the
        # libcamera presets remain until a tank-tuned entry returns.
        self.assertEqual(ct.AWB_TABLE[0]["mode"], "auto")
        self.assertEqual(ct.AWB_TABLE[1]["mode"], "daylight")
        self.assertEqual(ct.AWB_TABLE[2]["mode"], "cloudy")
        self.assertEqual(sorted(ct.AWB_TABLE), [0, 1, 2])
        for entry in ct.AWB_TABLE.values():
            self.assertIsNone(entry["gains"])

    def test_exp_zero_is_auto_and_steps_are_floats(self):
        self.assertIsNone(ct.EXP_TABLE[0]["ev"])
        for v, entry in ct.EXP_TABLE.items():
            if v == 0:
                continue
            self.assertIsInstance(entry["ev"], float, f"exp[{v}]")

    def test_win_values_production_spec(self):
        # v5 (Nick 2026-08-01): 12 min is the production default (index 0
        # = shipped YAML behaviour, factory-reset doctrine).
        minutes = {v: e["minutes"] for v, e in ct.WIN_TABLE.items()}
        self.assertEqual(minutes, {0: 12, 1: 5, 2: 8, 3: 16})


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


class TestV2Tables(unittest.TestCase):
    """txd / cap / src — added 2026-07-29 (Nick-approved, Phase E follow-on)."""

    def test_index_zero_is_the_shipped_default_everywhere(self):
        # Factory reset is all-zero, so index 0 of every table MUST be the
        # value a field unit already runs. If someone reorders a table so
        # index 0 becomes a test value, a factory reset would silently push
        # every unit into that mode.
        self.assertEqual(ct.TXD_TABLE[0]["seconds"], 1.0)   # Sprint09 shipped
        self.assertEqual(ct.CAP_TABLE[0]["messages"], 195)  # field default
        self.assertIsNone(ct.SRC_TABLE[0]["path"])          # live camera

    def test_txd_covers_the_phase_e_zero_loss_point(self):
        # Phase E: loss = max(0, 9 s / txd - 2) -> zero needs >= 4.5 s.
        # The table must be able to express a value at or above that, or the
        # command cannot reach the only known-good configuration.
        self.assertTrue(
            any(e["seconds"] >= 4.5 for e in ct.TXD_TABLE.values()),
            "no txd index reaches the 4.5 s zero-loss threshold",
        )

    def test_txd_seconds_strictly_increasing_after_default(self):
        secs = [ct.TXD_TABLE[i]["seconds"] for i in sorted(ct.TXD_TABLE)]
        self.assertEqual(secs, sorted(secs))
        self.assertEqual(len(secs), len(set(secs)), "duplicate txd values")

    def test_cap_messages_are_positive_ints(self):
        for i, e in ct.CAP_TABLE.items():
            self.assertIsInstance(e["messages"], int)
            self.assertGreater(e["messages"], 0, f"cap index {i}")

    def test_every_src_reference_exists_and_is_native_4608x2592(self):
        """THE finding-009 regression test.

        Soak 2026-07-28 lost a whole overnight run because the reference
        handed to the pipeline was 2090x1668 instead of 4608x2592 — a
        `find | head -1` grabbed a prep artifact. The lesson recorded then
        was 'verify artifact dimensions at pack time, not just presence'.
        This is that check, and it reads the JPEG header rather than
        trusting the filename.
        """
        import struct

        def dims(path):
            with open(path, "rb") as fh:
                data = fh.read()
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                if marker in (0xD8, 0xD9):
                    i += 2
                    continue
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
            return None

        checked = 0
        for index, entry in sorted(ct.SRC_TABLE.items()):
            if entry["path"] is None:
                continue
            full = os.path.join(REPO_ROOT, entry["path"])
            self.assertTrue(
                os.path.exists(full),
                f"src index {index} ({entry['label']}) missing: {entry['path']}",
            )
            self.assertEqual(
                dims(full), (4608, 2592),
                f"src index {index} ({entry['label']}) is {dims(full)}, "
                f"not native 4608x2592 — the pipeline will reject it",
            )
            checked += 1
        self.assertGreater(checked, 0, "no src reference images to check")

    def test_src_paths_are_repo_relative_not_absolute(self):
        # Absolute paths would break the moment the runtime is deployed to
        # /home/pi/BM_Devel_Pi instead of a dev checkout.
        for index, entry in ct.SRC_TABLE.items():
            if entry["path"] is not None:
                self.assertFalse(
                    os.path.isabs(entry["path"]), f"src index {index} is absolute"
                )

    def test_new_commands_validate_indices_strictly(self):
        for cmd, table in (("txd", ct.TXD_TABLE), ("cap", ct.CAP_TABLE),
                           ("src", ct.SRC_TABLE)):
            for index in table:
                self.assertTrue(ct.valid_value(cmd, index))
            self.assertFalse(ct.valid_value(cmd, max(table) + 1))
            self.assertFalse(ct.valid_value(cmd, -1))
            self.assertFalse(ct.valid_value(cmd, True))     # bool is not an index
            self.assertFalse(ct.valid_value(cmd, "0"))      # string is not an index

    def test_tables_version_bumped_for_v2(self):
        self.assertGreaterEqual(ct.TABLES_VERSION, 2)


class TestSprint12Tables(unittest.TestCase):
    """hlt / twn / trg — Sprint12 remote-config commands (2026-07-31)."""

    def test_tables_version_is_7(self):
        # v7 = Sprint16 wap 0/1/2 (this assert exists to force a conscious
        # bump on ANY table change — update it WITH the change, never
        # alone).
        self.assertEqual(ct.TABLES_VERSION, 7)

    def test_hlt_index_zero_carries_no_override(self):
        # 0 = YAML governs. If someone gives index 0 an override payload, a
        # factory reset would start overriding the YAML instead of clearing.
        self.assertIsNone(ct.HLT_TABLE[0]["override"])

    def test_hlt_overrides_match_spec(self):
        self.assertEqual(ct.HLT_TABLE[1]["override"],
                         {"enabled": True, "dry_run": False})
        self.assertEqual(ct.HLT_TABLE[2]["override"],
                         {"enabled": True, "dry_run": True})
        self.assertEqual(ct.HLT_TABLE[3]["override"],
                         {"enabled": False, "dry_run": True})

    def test_twn_index_zero_carries_no_override(self):
        self.assertIsNone(ct.TWN_TABLE[0]["override"])

    def test_twn_windows_match_spec_and_are_valid_hhmm(self):
        expected = {1: ("10:00", "15:00"), 2: ("00:00", "00:00"),
                    3: ("08:00", "12:00"), 4: ("11:00", "14:00")}
        for index, (start, end) in expected.items():
            override = ct.TWN_TABLE[index]["override"]
            self.assertEqual((override["start"], override["end"]),
                             (start, end), f"twn[{index}]")
        # Every preset must parse as HH:MM with sane ranges — the entire
        # point of a preset table is that a bad time cannot exist in it.
        for index, entry in ct.TWN_TABLE.items():
            if entry["override"] is None:
                continue
            for key in ("start", "end"):
                hh, mm = entry["override"][key].split(":")
                self.assertTrue(0 <= int(hh) <= 23, f"twn[{index}].{key}")
                self.assertTrue(0 <= int(mm) <= 59, f"twn[{index}].{key}")

    def test_twn_windows_are_start_before_end_except_all_day(self):
        # Overnight windows are supported by the gate but NOT offered as
        # presets — a wrapped preset chosen by accident would look like
        # "unit transmits at 3am". The ONE exception is the deliberate
        # full-circle pair (start == end == all day, D-S12-9) on index 2.
        for index, entry in ct.TWN_TABLE.items():
            if entry["override"] is None:
                continue
            if index == 2:
                self.assertEqual(entry["override"]["start"],
                                 entry["override"]["end"], "twn[2] full-circle")
                continue
            self.assertLess(entry["override"]["start"],
                            entry["override"]["end"], f"twn[{index}]")

    def test_trg_index_zero_is_cancel(self):
        self.assertIsNone(ct.TRG_TABLE[0]["action"])
        self.assertIsNone(ct.TRG_TABLE[0]["src"])

    def test_trg_actions_are_known(self):
        for index, entry in ct.TRG_TABLE.items():
            if index == 0:
                continue
            self.assertIn(entry["action"], ("capture", "capture_transmit"),
                          f"trg[{index}]")

    def test_trg_src_indices_point_into_src_table(self):
        # Reference triggers ride SRC_TABLE (single source of truth for
        # paths — finding 009's dimension test covers them there).
        for index, entry in ct.TRG_TABLE.items():
            if entry["src"] is None:
                continue
            self.assertIn(entry["src"], ct.SRC_TABLE, f"trg[{index}]")
            self.assertIsNotNone(ct.SRC_TABLE[entry["src"]]["path"],
                                 f"trg[{index}] points at live-camera src 0")

    def test_trg_reference_triggers_transmit(self):
        # A reference trigger that didn't transmit would be pointless: the
        # image is already on the SD card in the repo.
        for index, entry in ct.TRG_TABLE.items():
            if entry["src"] is not None:
                self.assertEqual(entry["action"], "capture_transmit",
                                 f"trg[{index}]")

    def test_new_commands_validate_indices_strictly(self):
        for cmd, table in (("hlt", ct.HLT_TABLE), ("twn", ct.TWN_TABLE),
                           ("trg", ct.TRG_TABLE)):
            for index in table:
                self.assertTrue(ct.valid_value(cmd, index))
            self.assertFalse(ct.valid_value(cmd, max(table) + 1))
            self.assertFalse(ct.valid_value(cmd, -1))
            self.assertFalse(ct.valid_value(cmd, True))
            self.assertFalse(ct.valid_value(cmd, "0"))
