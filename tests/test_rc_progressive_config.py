#!/usr/bin/env python3
# filename: test_rc_progressive_config.py
# description: Off-device tests for Sprint08 P0 — RC YAML keys + entry skeleton.
"""
Sprint08 P0 off-device tests.

Covers:
  - the extended camera_schedule.yaml parser (capture_mode, progressive_jpeg,
    power_halt) in BM_Devel_Pi/spotter_time_sync.py, including regression
    checks that the existing keys still parse to the committed values
  - validation gating: RC settings are only strictly validated when
    capture_mode: progressive_jpeg, so a broken RC block cannot fail-closed
    a HEIC-mode field unit
  - the quality-ladder computation in BM_Devel_Pi/rc_progressive_jpeg.py
  - the P0 skeleton end-to-end: resolved-settings output on the committed
    repo camera_schedule.yaml

Run (repo root, works without pyserial/PyYAML — serial is stubbed):
  python3 -m unittest tests.test_rc_progressive_config -v
  # or: python3 tests/test_rc_progressive_config.py

Assumptions: PIL importable (needed by process_image_v2 import chain).
"""

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_DIR = os.path.join(REPO_ROOT, "BM_Devel_Pi")
REPO_YAML = os.path.join(PI_DIR, "camera_schedule.yaml")

# spotter_time_sync.py and bm_serial.py import pyserial at module top.
# Off-device (Mac) pyserial may be absent; stub it so config parsing is
# testable. The stub raises if anything actually tries to open a UART.
try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

sys.path.insert(0, PI_DIR)

from spotter_time_sync import load_camera_schedule, validate_schedule  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402


def _write_yaml(text):
    """Write YAML text to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    f.write(text)
    f.close()
    return f.name


class TestRepoYamlParses(unittest.TestCase):
    """The committed camera_schedule.yaml parses; RC keys + legacy keys intact."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_camera_schedule(REPO_YAML)

    def test_rc_keys_resolved(self):
        self.assertEqual(self.cfg.capture_mode, "heic")
        self.assertEqual(self.cfg.progressive_jpeg_max_run_time_min, 18)
        self.assertEqual(self.cfg.progressive_jpeg_message_cap, 195)
        self.assertEqual(self.cfg.progressive_jpeg_q_max, 15)
        self.assertEqual(self.cfg.progressive_jpeg_q_min, 9)
        self.assertEqual(self.cfg.progressive_jpeg_q_step, 2)
        # RC frozen geometry (S07 scene/center crop), separate from the HEIC crop.
        self.assertEqual(
            (
                self.cfg.progressive_jpeg_crop_x,
                self.cfg.progressive_jpeg_crop_y,
                self.cfg.progressive_jpeg_crop_w,
                self.cfg.progressive_jpeg_crop_h,
            ),
            (1504, 846, 1600, 900),
        )
        self.assertEqual(self.cfg.progressive_jpeg_output_width, 1000)
        self.assertFalse(self.cfg.power_halt_enabled)
        self.assertTrue(self.cfg.power_halt_dry_run)
        self.assertEqual(self.cfg.power_halt_mode, "halt")
        self.assertEqual(self.cfg.power_halt_script_path, "/home/pi/BM_Devel_Pi/tuned_halt.sh")

    def test_legacy_keys_unchanged(self):
        # Regression guard: extending the parser must not disturb the
        # committed production values.
        self.assertEqual(self.cfg.time_source, "spotter_utc")
        self.assertEqual(self.cfg.timezone_preset, "sf")
        self.assertEqual(self.cfg.transmit_start, "08:00")
        self.assertEqual(self.cfg.transmit_end, "15:00")
        self.assertTrue(self.cfg.enforce_spotter_time_window)
        self.assertEqual(self.cfg.resolution_key, "720p")
        self.assertEqual(self.cfg.image_quality, 25)
        self.assertTrue(self.cfg.image_pipeline_enabled)
        self.assertEqual(self.cfg.image_pipeline_capture_backend, "rpicam")
        self.assertEqual(
            (
                self.cfg.image_pipeline_crop_x,
                self.cfg.image_pipeline_crop_y,
                self.cfg.image_pipeline_crop_w,
                self.cfg.image_pipeline_crop_h,
            ),
            (768, 432, 3072, 1728),
        )
        self.assertEqual(self.cfg.image_pipeline_spatial_output_width, 2688)
        self.assertEqual(self.cfg.image_pipeline_spatial_output_height, 1512)
        self.assertEqual(self.cfg.image_pipeline_heic_quality, 20)

    def test_repo_yaml_validates(self):
        validate_schedule(self.cfg)  # must not raise


class TestRcBlockParsing(unittest.TestCase):
    def test_progressive_mode_with_overrides(self):
        path = _write_yaml(
            "capture_mode: \"progressive_jpeg\"\n"
            "progressive_jpeg:\n"
            "  max_run_time_min: 12\n"
            "  message_cap: 100\n"
            "  quality:\n"
            "    q_max: 13\n"
            "    q_min: 9\n"
            "    step: 4\n"
            "power_halt:\n"
            "  enabled: true\n"
            "  dry_run: false\n"
            "  mode: \"poweroff\"\n"
        )
        try:
            cfg = load_camera_schedule(path)
            validate_schedule(cfg)
            self.assertEqual(cfg.capture_mode, "progressive_jpeg")
            self.assertEqual(cfg.progressive_jpeg_max_run_time_min, 12)
            self.assertEqual(cfg.progressive_jpeg_message_cap, 100)
            self.assertEqual(cfg.progressive_jpeg_q_max, 13)
            self.assertEqual(cfg.progressive_jpeg_q_min, 9)
            self.assertEqual(cfg.progressive_jpeg_q_step, 4)
            self.assertTrue(cfg.power_halt_enabled)
            self.assertFalse(cfg.power_halt_dry_run)
            self.assertEqual(cfg.power_halt_mode, "poweroff")
        finally:
            os.unlink(path)

    def test_missing_rc_block_uses_sprint07_defaults(self):
        path = _write_yaml("capture_mode: \"progressive_jpeg\"\n")
        try:
            cfg = load_camera_schedule(path)
            validate_schedule(cfg)
            self.assertEqual(cfg.progressive_jpeg_max_run_time_min, 18)
            self.assertEqual(cfg.progressive_jpeg_message_cap, 195)
            self.assertEqual(cfg.progressive_jpeg_q_max, 15)
            self.assertEqual(cfg.progressive_jpeg_q_min, 9)
            self.assertEqual(cfg.progressive_jpeg_q_step, 2)
        finally:
            os.unlink(path)

    def test_missing_capture_mode_defaults_to_heic(self):
        path = _write_yaml("timezone: \"UTC\"\n")
        try:
            cfg = load_camera_schedule(path)
            self.assertEqual(cfg.capture_mode, "heic")
            validate_schedule(cfg)
        finally:
            os.unlink(path)


class TestValidationGating(unittest.TestCase):
    def _yaml_with_bad_quality(self, capture_mode):
        return _write_yaml(
            f"capture_mode: \"{capture_mode}\"\n"
            "progressive_jpeg:\n"
            "  quality:\n"
            "    q_max: 9\n"
            "    q_min: 15\n"  # inverted: q_min > q_max
        )

    def test_bad_quality_rejected_in_rc_mode(self):
        path = self._yaml_with_bad_quality("progressive_jpeg")
        try:
            cfg = load_camera_schedule(path)
            with self.assertRaises(ValueError):
                validate_schedule(cfg)
        finally:
            os.unlink(path)

    def test_bad_quality_ignored_in_heic_mode(self):
        # A broken RC block must never fail-closed a HEIC-mode field unit.
        path = self._yaml_with_bad_quality("heic")
        try:
            cfg = load_camera_schedule(path)
            validate_schedule(cfg)  # must not raise
        finally:
            os.unlink(path)

    def test_invalid_capture_mode_rejected(self):
        path = _write_yaml("capture_mode: \"jpeg2000\"\n")
        try:
            cfg = load_camera_schedule(path)
            with self.assertRaises(ValueError):
                validate_schedule(cfg)
        finally:
            os.unlink(path)

    def test_zero_step_rejected_in_rc_mode(self):
        path = _write_yaml(
            "capture_mode: \"progressive_jpeg\"\n"
            "progressive_jpeg:\n"
            "  quality:\n"
            "    step: 0\n"
        )
        try:
            cfg = load_camera_schedule(path)
            with self.assertRaises(ValueError):
                validate_schedule(cfg)
        finally:
            os.unlink(path)

    def test_rc_crop_override_parses_and_bad_crop_rejected(self):
        # Card-centered bench crop parses; out-of-native crop rejected in RC mode.
        good = _write_yaml(
            "capture_mode: \"progressive_jpeg\"\n"
            "progressive_jpeg:\n"
            "  crop:\n"
            "    x: 1467\n"
            "    y: 1255\n"
            "    w: 1600\n"
            "    h: 900\n"
            "  output_width: 1000\n"
        )
        bad = _write_yaml(
            "capture_mode: \"progressive_jpeg\"\n"
            "progressive_jpeg:\n"
            "  crop:\n"
            "    x: 4000\n"
            "    y: 846\n"
            "    w: 1600\n"
            "    h: 900\n"
        )
        try:
            cfg = load_camera_schedule(good)
            validate_schedule(cfg)
            self.assertEqual(
                (cfg.progressive_jpeg_crop_x, cfg.progressive_jpeg_crop_y),
                (1467, 1255),
            )
            cfg_bad = load_camera_schedule(bad)
            with self.assertRaises(ValueError):
                validate_schedule(cfg_bad)
        finally:
            os.unlink(good)
            os.unlink(bad)

    def test_bad_halt_mode_rejected_in_rc_mode(self):
        path = _write_yaml(
            "capture_mode: \"progressive_jpeg\"\n"
            "power_halt:\n"
            "  mode: \"reboot\"\n"
        )
        try:
            cfg = load_camera_schedule(path)
            with self.assertRaises(ValueError):
                validate_schedule(cfg)
        finally:
            os.unlink(path)


class TestQualityLadder(unittest.TestCase):
    def test_sprint07_defaults(self):
        self.assertEqual(rc.compute_quality_ladder(15, 9, 2), [15, 13, 11, 9])

    def test_uneven_step_still_ends_at_floor(self):
        self.assertEqual(rc.compute_quality_ladder(15, 10, 2), [15, 13, 11, 10])
        self.assertEqual(rc.compute_quality_ladder(15, 9, 3), [15, 12, 9])

    def test_single_rung(self):
        self.assertEqual(rc.compute_quality_ladder(13, 13, 2), [13])

    def test_big_step_goes_straight_to_floor(self):
        self.assertEqual(rc.compute_quality_ladder(15, 9, 10), [15, 9])

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            rc.compute_quality_ladder(9, 15, 2)
        with self.assertRaises(ValueError):
            rc.compute_quality_ladder(15, 9, 0)


class TestSkeletonDryRun(unittest.TestCase):
    def test_print_config_prints_resolved_settings_for_repo_yaml(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ret = rc.main(["--config-path", REPO_YAML, "--print-config"])
        text = out.getvalue()
        self.assertEqual(ret, 0, msg=f"exit={ret}; output:\n{text}")
        self.assertIn("capture_mode=heic", text)
        self.assertIn("[15, 13, 11, 9]", text)
        self.assertIn("max_run_time_min=18", text)
        self.assertIn("message cap: 195", text)
        self.assertIn(
            "power_halt: enabled=False dry_run=True mode=halt "
            "script=/home/pi/BM_Devel_Pi/tuned_halt.sh",
            text,
        )
        self.assertIn("crop_xywh=(1504, 846, 1600, 900)", text)
        self.assertIn("output=1000x562", text)

    def test_print_config_rc_mode_banner(self):
        path = _write_yaml("capture_mode: \"progressive_jpeg\"\n")
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ret = rc.main(["--config-path", path, "--print-config"])
            self.assertEqual(ret, 0)
            self.assertIn("capture_mode=progressive_jpeg (RC path selected)", out.getvalue())
        finally:
            os.unlink(path)

    def test_heic_mode_cycle_is_gated_noop(self):
        # Without --print-config, HEIC capture_mode must exit 0 doing NOTHING
        # (the config gate protecting the known-good path).
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ret = rc.main(["--config-path", REPO_YAML])
        self.assertEqual(ret, 0)
        self.assertIn("RC inactive", out.getvalue())
        self.assertNotIn("cycle start", out.getvalue())

    def test_skeleton_fails_loud_on_bad_config(self):
        path = _write_yaml("capture_mode: \"jpeg2000\"\n")
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ret = rc.main(["--config-path", path])
            self.assertEqual(ret, 2)
            self.assertIn("[RC][ERROR]", err.getvalue())
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
