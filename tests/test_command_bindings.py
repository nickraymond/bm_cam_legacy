#!/usr/bin/env python3
# filename: test_command_bindings.py
# description: Sprint10 §3 — overlay bindings (roi/win settings, foc/awb/exp controls).
"""
Sprint10 — tests for command_bindings.py (+ the touched-tracking added
to command_state.py and the --ev support added to process_image_v2).

The end-to-end pins here matter most: overlay output is fed into the
PRODUCTION _camera_controls_from_settings builder and must produce the
exact rpicam CLI flags (--autofocus-mode/--lens-position, --awb
[--awbgains], --ev) the Q2 audit mapped each command to.

Run (repo root; serial stubbed, no UART/camera):
  python3 -m unittest tests.test_command_bindings -v
"""

import os
import sys
import tempfile
import types
import unittest
import unittest.mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

import command_tables as ct  # noqa: E402
from command_bindings import (  # noqa: E402
    apply_trigger,
    describe_overrides,
    overlay_camera_controls,
    overlay_rc_settings,
    stranding_warnings,
)
from command_state import CommandState  # noqa: E402
from process_image_v2 import _camera_controls_from_settings  # noqa: E402

# Minimal slice of resolve_rc_settings() output the overlay touches.
YAML_SETTINGS = {
    "crop_native_xywh": (1504, 846, 1600, 900),
    "output_width": 1000,
    "output_size": (1000, 562),
    "max_run_time_min": 16,
    "budget_seconds": 960,
    # Sprint12 slice: production-shaped halt/window keys.
    "power_halt_enabled": True,
    "power_halt_dry_run": False,
    "power_halt_source": "yaml",
    "window_start": "10:00",
    "window_end": "15:00",
    "transmit_window": "10:00-15:00",
    "window_source": "yaml",
    # Sprint13 tmz slice.
    "timezone": "America/New_York",
    "timezone_source": "yaml",
}


class BindingsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.state = CommandState(path=os.path.join(self.tmpdir.name, "state.json"))


class TestOverlaySettings(BindingsTestCase):
    def test_untouched_state_changes_nothing(self):
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(overrides, [])
        self.assertEqual(s["crop_native_xywh"], YAML_SETTINGS["crop_native_xywh"])
        self.assertEqual(s["max_run_time_min"], 16)
        self.assertEqual(s["command_overrides"], [])

    def test_roi_override_updates_crop_and_output_size(self):
        self.state.record(1, "roi", 2)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(s["crop_native_xywh"], ct.ROI_TABLE[2]["crop"])
        self.assertEqual(s["output_size"], (1000, 562))  # 16:9 held
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0][0], "crop_native_xywh")
        self.assertIn("roi=2", overrides[0][3])

    def test_roi_commanded_to_yaml_value_no_override_line(self):
        # roi=0 IS the YAML default rect: applied, but nothing to report.
        self.state.record(1, "roi", 0)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(overrides, [])
        self.assertEqual(s["crop_native_xywh"], ct.ROI_TABLE[0]["crop"])

    def test_win_override_updates_budget(self):
        # v5 table order: win 1 = 5 min (12/5/8/16).
        self.state.record(2, "win", 1)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(s["max_run_time_min"], 5)
        self.assertEqual(s["budget_seconds"], 300)
        self.assertEqual(len(overrides), 1)

    def test_input_dict_not_mutated(self):
        self.state.record(1, "roi", 4)
        original = dict(YAML_SETTINGS)
        overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(YAML_SETTINGS, original)

    def test_describe_overrides_lines(self):
        self.state.record(1, "win", 2)
        _s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        lines = describe_overrides(overrides)
        self.assertEqual(len(lines), 1)
        self.assertIn("max_run_time_min", lines[0])
        self.assertIn("win=2", lines[0])


class TestOverlayCameraControls(BindingsTestCase):
    def test_nothing_touched_no_yaml_returns_none(self):
        self.assertIsNone(overlay_camera_controls(None, self.state))
        self.assertIsNone(overlay_camera_controls({}, self.state))

    def test_nothing_touched_yaml_island_passthrough(self):
        island = {"enabled": True, "focus": {"mode": "manual", "lens_position": 3.2}}
        self.assertEqual(overlay_camera_controls(island, self.state), island)

    def test_focus_manual_command(self):
        self.state.record(1, "foc", 3)
        controls = overlay_camera_controls(None, self.state)
        self.assertTrue(controls["enabled"])
        self.assertEqual(controls["focus"]["mode"], "manual")
        self.assertEqual(
            controls["focus"]["lens_position"], ct.FOC_TABLE[3]["lens_position"]
        )

    def test_commanded_auto_replaces_yaml_manual_focus(self):
        island = {"enabled": True, "focus": {"mode": "manual", "lens_position": 3.2}}
        self.state.record(1, "foc", 0)
        controls = overlay_camera_controls(island, self.state)
        self.assertEqual(controls["focus"]["mode"], "auto")
        self.assertNotIn("lens_position", controls["focus"])

    def test_awb_custom_carries_gains(self):
        # v5 dropped the untuned underwater preset from the table, but the
        # custom-gains code path stays for the future tank-tuned entry —
        # covered here via a temporary table entry.
        custom = {"label": "test custom", "mode": "custom", "gains": (1.8, 1.2)}
        with unittest.mock.patch.dict(ct.AWB_TABLE, {3: custom}):
            self.state.record(1, "awb", 3)
            controls = overlay_camera_controls(None, self.state)
        wb = controls["white_balance"]
        self.assertEqual(wb["mode"], "custom")
        self.assertEqual((wb["red_gain"], wb["blue_gain"]), (1.8, 1.2))

    def test_awb_3_no_longer_a_valid_wire_value(self):
        # v5: an old sender's awb=3 must reject cleanly (ERR_VAL path).
        self.assertFalse(ct.valid_value("awb", 3))

    def test_exp_auto_has_no_ev_key(self):
        self.state.record(1, "exp", 0)
        controls = overlay_camera_controls(None, self.state)
        self.assertEqual(controls["exposure"], {"enabled": True})

    def test_untouched_yaml_blocks_survive_alongside_commands(self):
        island = {"enabled": True, "exposure": {"enabled": True, "shutter_us": 500}}
        self.state.record(1, "foc", 2)
        controls = overlay_camera_controls(island, self.state)
        self.assertEqual(controls["exposure"], {"enabled": True, "shutter_us": 500})
        self.assertEqual(controls["focus"]["mode"], "manual")


class TestProductionArgBuilder(BindingsTestCase):
    """Overlay output must drive the real rpicam arg builder (Q2 map)."""

    def _args(self):
        controls = overlay_camera_controls(None, self.state)
        args, _requested = _camera_controls_from_settings(
            {"camera_controls": controls}
        )
        return args

    def test_focus_flags(self):
        self.state.record(1, "foc", 3)
        args = self._args()
        self.assertIn("--autofocus-mode", args)
        self.assertEqual(args[args.index("--autofocus-mode") + 1], "manual")
        self.assertIn("--lens-position", args)
        self.assertEqual(args[args.index("--lens-position") + 1], "1")

    def test_awb_mode_flag(self):
        self.state.record(1, "awb", 1)
        args = self._args()
        self.assertEqual(args[args.index("--awb") + 1], "daylight")

    def test_awb_custom_gains_flags(self):
        # Same temporary-entry trick as test_awb_custom_carries_gains: the
        # --awbgains flag path outlives the dropped v4 preset.
        custom = {"label": "test custom", "mode": "custom", "gains": (1.8, 1.2)}
        with unittest.mock.patch.dict(ct.AWB_TABLE, {3: custom}):
            self.state.record(1, "awb", 3)
            args = self._args()
        self.assertEqual(args[args.index("--awb") + 1], "custom")
        self.assertEqual(args[args.index("--awbgains") + 1], "1.8,1.2")

    def test_ev_flag(self):
        self.state.record(1, "exp", 6)
        args = self._args()
        self.assertIn("--ev", args)
        self.assertEqual(args[args.index("--ev") + 1], "2")

    def test_exp_auto_emits_no_exposure_flags(self):
        self.state.record(1, "exp", 0)
        args = self._args()
        for flag in ("--ev", "--shutter", "--gain"):
            self.assertNotIn(flag, args)

    def test_factory_reset_state_emits_auto_flags_only(self):
        for i, cmd in enumerate(("foc", "awb", "exp"), start=1):
            self.state.record(i, cmd, 0)
        args = self._args()
        self.assertEqual(args[args.index("--autofocus-mode") + 1], "auto")
        self.assertEqual(args[args.index("--awb") + 1], "auto")
        self.assertNotIn("--ev", args)


class TestTouchedTracking(BindingsTestCase):
    def test_touched_persists_across_restart(self):
        self.state.record(1, "foc", 2)
        rebooted = CommandState(path=self.state.path)
        self.assertEqual(rebooted.touched, {"foc"})

    def test_ping_never_touches(self):
        self.state.record(1, "ping", 0)
        self.assertEqual(self.state.touched, set())

    def test_untouched_keys_stay_untouched_after_other_commands(self):
        self.state.record(1, "roi", 2)
        self.state.record(2, "win", 1)
        rebooted = CommandState(path=self.state.path)
        self.assertEqual(rebooted.touched, {"roi", "win"})

    def test_reset_invalid_value_also_untouches(self):
        # A stored out-of-table value resets to default AND leaves the
        # key untouched (YAML wins again) — tables may shrink between
        # deploys and a stale index must not pin a bogus override.
        import json

        self.state.record(1, "roi", 2)
        with open(self.state.path, encoding="utf-8") as f:
            data = json.load(f)
        data["settings"]["roi"] = 99
        with open(self.state.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        rebooted = CommandState(path=self.state.path)
        self.assertNotIn("roi", rebooted.touched)
        self.assertEqual(rebooted.settings["roi"], 0)


if __name__ == "__main__":
    unittest.main()


# Fuller slice for the v2 transport commands (txd/cap/src).
V2_SETTINGS = dict(
    YAML_SETTINGS,
    pacing_delay_seconds=1.0,
    pacing_source="yaml",
    message_cap=195,
    source_image_path=None,
    budget_messages_if_transmit_only=960,
)


class TestOverlayV2(BindingsTestCase):
    """txd / cap / src overlay — added 2026-07-29."""

    def test_untouched_leaves_v2_settings_alone(self):
        s, overrides = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertEqual(overrides, [])
        self.assertEqual(s["pacing_delay_seconds"], 1.0)
        self.assertEqual(s["message_cap"], 195)
        self.assertIsNone(s["source_image_path"])

    def test_txd_overrides_pacing(self):
        self.state.record(1, "txd", 5)          # 5.0 s
        s, overrides = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertEqual(s["pacing_delay_seconds"], 5.0)
        self.assertIn("command txd=5", s["pacing_source"])
        self.assertTrue(any(o[0] == "pacing_delay_seconds" for o in overrides))

    def test_txd_recomputes_the_transmit_only_budget(self):
        """The interaction that silently truncates a slow cycle.

        At 5.0 s pacing with a 16-min window only 192 messages fit, which is
        BELOW the 195 cap — so the time budget, not message_cap, becomes the
        real ceiling. If this recompute is missed, telemetry keeps reporting
        960 and the truncation looks like packet loss.
        """
        self.state.record(1, "txd", 5)
        s, _ = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertEqual(s["budget_seconds"], 960)
        self.assertEqual(s["budget_messages_if_transmit_only"], 192)
        self.assertLess(s["budget_messages_if_transmit_only"], s["message_cap"])

    def test_txd_and_win_together_recompute_from_both(self):
        self.state.record(1, "txd", 5)   # 5.0 s
        self.state.record(2, "win", 2)   # 8 min -> 480 s
        s, _ = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertEqual(s["budget_seconds"], 480)
        self.assertEqual(s["budget_messages_if_transmit_only"], 96)

    def test_cap_overrides_message_cap(self):
        self.state.record(1, "cap", 1)   # 100
        s, overrides = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertEqual(s["message_cap"], 100)
        self.assertTrue(any(o[0] == "message_cap" for o in overrides))

    def test_src_sets_a_repo_relative_reference_path(self):
        self.state.record(1, "src", 1)   # reef primary
        s, overrides = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertIsNotNone(s["source_image_path"])
        self.assertIn("synthetic_native_4608x2592.jpg", s["source_image_path"])
        self.assertTrue(any(o[0] == "source_image_path" for o in overrides))

    def test_src_zero_returns_to_live_camera(self):
        self.state.record(1, "src", 1)
        s, _ = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertIsNotNone(s["source_image_path"])
        self.state.record(2, "src", 0)   # commanding 0 IS an override
        s, _ = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertIsNone(s["source_image_path"],
                          "src=0 must restore camera capture in the field")

    def test_commanded_value_equal_to_yaml_records_no_override(self):
        self.state.record(1, "txd", 0)   # 1.0 s == the YAML value
        s, overrides = overlay_rc_settings(V2_SETTINGS, self.state)
        self.assertEqual(s["pacing_delay_seconds"], 1.0)
        self.assertEqual([o for o in overrides if o[0] == "pacing_delay_seconds"], [])


class TestStageSourceImage(unittest.TestCase):
    """rc_progressive_jpeg.stage_source_image — the finding-009 guards."""

    def setUp(self):
        import rc_progressive_jpeg as rpj
        self.rpj = rpj
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_rejects_missing_file_with_actionable_message(self):
        with self.assertRaises(FileNotFoundError) as cm:
            self.rpj.stage_source_image("nope/does_not_exist.jpg", self.tmp.name)
        self.assertIn("reference_images", str(cm.exception))

    def test_rejects_wrong_dimensions_before_doing_any_work(self):
        """Finding 009: a 4000x3000 scene file reached the pipeline and every
        cycle died 100 s in. Fail immediately, and name both sizes."""
        raw = os.path.join(REPO_ROOT, "reference_images",
                           "reference_reef_coral_primary.jpg")
        if not os.path.exists(raw):
            self.skipTest("raw reference scene not present")
        with self.assertRaises(ValueError) as cm:
            self.rpj.stage_source_image(raw, self.tmp.name)
        msg = str(cm.exception)
        self.assertIn("4608", msg)
        self.assertIn("4000", msg)

    def test_copies_rather_than_consuming_the_committed_reference(self):
        """Finding 009 (original): the pipeline consumes the native it is
        handed. The master must still exist, byte-identical, afterwards."""
        import command_tables as ct2
        rel = ct2.SRC_TABLE[1]["path"]
        master = os.path.join(REPO_ROOT, rel)
        before = os.path.getsize(master)
        staged = self.rpj.stage_source_image(rel, self.tmp.name)
        self.assertTrue(os.path.exists(staged))
        self.assertNotEqual(os.path.abspath(staged), os.path.abspath(master))
        self.assertTrue(os.path.exists(master), "master reference was consumed")
        self.assertEqual(os.path.getsize(master), before)
        self.assertEqual(os.path.getsize(staged), before)

    def test_staged_name_carries_the_native_full_suffix(self):
        # The cycle derives image_stem by stripping "_native_full"; without
        # the suffix every staged image would get a malformed stem.
        import command_tables as ct2
        staged = self.rpj.stage_source_image(ct2.SRC_TABLE[1]["path"], self.tmp.name)
        self.assertTrue(os.path.basename(staged).endswith("_native_full.jpg"))


class TestSprint12HltOverlay(BindingsTestCase):
    """hlt — power-halt override (D-S12-1/2)."""

    def test_hlt_1_forces_real_halt(self):
        self.state.record(1, "hlt", 1)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertTrue(s["power_halt_enabled"])
        self.assertFalse(s["power_halt_dry_run"])
        self.assertEqual(s["power_halt_source"], "command hlt=1")

    def test_hlt_2_forces_dry_run(self):
        self.state.record(1, "hlt", 2)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertTrue(s["power_halt_enabled"])
        self.assertTrue(s["power_halt_dry_run"])
        self.assertEqual(len(overrides), 1)
        self.assertIn("hlt=2", overrides[0][3])

    def test_hlt_3_disables_halt(self):
        self.state.record(1, "hlt", 3)
        s, _ = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertFalse(s["power_halt_enabled"])

    def test_hlt_0_touched_leaves_yaml_governing(self):
        # The delete-the-override doctrine: commanded back to 0, the YAML
        # values pass through untouched and no override line is emitted.
        self.state.record(1, "hlt", 2)
        self.state.record(2, "hlt", 0)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(overrides, [])
        self.assertTrue(s["power_halt_enabled"])       # yaml value
        self.assertFalse(s["power_halt_dry_run"])      # yaml value
        self.assertEqual(s["power_halt_source"], "yaml")

    def test_mode_and_script_are_never_touched(self):
        settings = dict(YAML_SETTINGS,
                        power_halt_mode="halt",
                        power_halt_script_path="/home/pi/x.sh")
        self.state.record(1, "hlt", 3)
        s, _ = overlay_rc_settings(settings, self.state)
        self.assertEqual(s["power_halt_mode"], "halt")
        self.assertEqual(s["power_halt_script_path"], "/home/pi/x.sh")

    def test_stranding_warnings_per_mode(self):
        # hlt 1 (real) and hlt 3 (disabled) must WARN; hlt 2 informs;
        # hlt 0 / untouched say nothing.
        self.assertEqual(stranding_warnings(self.state), [])
        self.state.record(1, "hlt", 1)
        self.assertIn("REAL power halt", stranding_warnings(self.state)[0])
        self.state.record(2, "hlt", 3)
        self.assertIn("DISABLED", stranding_warnings(self.state)[0])
        self.state.record(3, "hlt", 2)
        self.assertIn("dry-run", stranding_warnings(self.state)[0])
        self.state.record(4, "hlt", 0)
        self.assertEqual(stranding_warnings(self.state), [])


class TestSprint12TwnOverlay(BindingsTestCase):
    """twn — transmit-window override (D-S12-1/6)."""

    def test_twn_2_opens_the_all_day_window(self):
        self.state.record(1, "twn", 2)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual((s["window_start"], s["window_end"]),
                         ("00:00", "00:00"))
        self.assertEqual(s["transmit_window"], "00:00-00:00")
        self.assertEqual(s["window_source"], "command twn=2")
        self.assertEqual(len(overrides), 1)
        self.assertIn("twn=2", overrides[0][3])

    def test_twn_1_equal_to_yaml_applies_without_override_line(self):
        # twn 1 == the production 10:00-15:00 already in the YAML slice:
        # source flips to command, but nothing to report as a change.
        self.state.record(1, "twn", 1)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(overrides, [])
        self.assertEqual(s["window_source"], "command twn=1")

    def test_twn_0_touched_leaves_yaml_governing(self):
        self.state.record(1, "twn", 2)
        self.state.record(2, "twn", 0)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(overrides, [])
        self.assertEqual((s["window_start"], s["window_end"]),
                         ("10:00", "15:00"))
        self.assertEqual(s["window_source"], "yaml")

    # ---- Sprint13 tmz (same index-0 doctrine as hlt/twn) ----

    def test_tmz_override_sets_timezone_and_source(self):
        self.state.record(1, "tmz", 1)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(s["timezone"], "America/Los_Angeles")
        self.assertEqual(s["timezone_source"], "command tmz=1")
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0][0], "timezone")

    def test_tmz_equal_to_yaml_applies_without_override_line(self):
        # tmz 2 == the New_York already in the YAML slice: source flips
        # to command, nothing to report as a change.
        self.state.record(1, "tmz", 2)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(overrides, [])
        self.assertEqual(s["timezone_source"], "command tmz=2")

    def test_tmz_0_touched_leaves_yaml_governing(self):
        self.state.record(1, "tmz", 3)
        self.state.record(2, "tmz", 0)
        s, overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(overrides, [])
        self.assertEqual(s["timezone"], "America/New_York")
        self.assertEqual(s["timezone_source"], "yaml")

    def test_roi_reef_test_crops_clamp_output_no_upsample(self):
        # v5 roi 5/6 are narrower than output_width=1000: output must
        # equal the native crop size, never upsampled.
        self.state.record(1, "roi", 5)
        s, _overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(s["output_size"], (800, 450))
        self.state.record(2, "roi", 6)
        s, _overrides = overlay_rc_settings(YAML_SETTINGS, self.state)
        self.assertEqual(s["output_size"], (640, 360))


class TestApplyTrigger(BindingsTestCase):
    """trg — one-shot trigger mapping (D-S12-3/4/5)."""

    def test_trg_2_bypasses_window_and_keeps_camera(self):
        s, flags, lines = apply_trigger(YAML_SETTINGS, {"id": 9, "value": 2})
        self.assertTrue(flags["skip_time_window"])
        self.assertFalse(flags["capture_only"])
        self.assertNotIn("source_image_path", s)  # live camera untouched
        self.assertEqual(s["trigger"], {"id": 9, "value": 2})
        self.assertTrue(any("BYPASSED" in l for l in lines))

    def test_trg_1_is_capture_only(self):
        s, flags, lines = apply_trigger(YAML_SETTINGS, {"id": 9, "value": 1})
        self.assertTrue(flags["skip_time_window"])
        self.assertTrue(flags["capture_only"])

    def test_trg_3_sends_reef_reference_one_shot(self):
        s, flags, lines = apply_trigger(YAML_SETTINGS, {"id": 9, "value": 3})
        self.assertEqual(s["source_image_path"], ct.SRC_TABLE[1]["path"])
        self.assertFalse(flags["capture_only"])
        self.assertTrue(flags["skip_time_window"])

    def test_trg_4_sends_reference_card(self):
        s, _, _ = apply_trigger(YAML_SETTINGS, {"id": 9, "value": 4})
        self.assertEqual(s["source_image_path"], ct.SRC_TABLE[9]["path"])

    def test_trigger_does_not_mutate_input_settings(self):
        before = dict(YAML_SETTINGS)
        apply_trigger(YAML_SETTINGS, {"id": 9, "value": 3})
        self.assertEqual(YAML_SETTINGS, before)
