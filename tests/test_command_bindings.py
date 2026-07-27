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
    describe_overrides,
    overlay_camera_controls,
    overlay_rc_settings,
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
        self.state.record(2, "win", 3)
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
        self.state.record(1, "awb", 3)
        controls = overlay_camera_controls(None, self.state)
        wb = controls["white_balance"]
        self.assertEqual(wb["mode"], "custom")
        self.assertEqual(
            (wb["red_gain"], wb["blue_gain"]), ct.AWB_TABLE[3]["gains"]
        )

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
        self.state.record(1, "awb", 3)
        args = self._args()
        self.assertEqual(args[args.index("--awb") + 1], "custom")
        red, blue = ct.AWB_TABLE[3]["gains"]
        self.assertEqual(args[args.index("--awbgains") + 1], f"{red},{blue}")

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
