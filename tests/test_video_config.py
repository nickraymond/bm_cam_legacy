#!/usr/bin/env python3
# filename: test_video_config.py
# description: Sprint15 chunk 1 — video island parsing, geometry, mode dispatch.
"""
Sprint15 chunk 1 off-device tests.

Covers:
  - the `video:` YAML island loader/validator in BM_Devel_Pi/video_recorder.py
    (SPEC-locked defaults, loud failures on nonsense values)
  - geometry derived from the stills keys (D-S15-3): crop_xywh -> --roi
    exact-value conversion, even output-size rule, encoder argv builder
    reusing the stills camera-controls builder
  - validate_schedule accepting capture_mode: video (geometry validated,
    stills-only quality checks NOT applied to video)
  - capture_mode dispatch in rc_progressive_jpeg.main(): video enters
    run_video_mode, stills/HEIC paths never touch the video module

Run (repo root, no hardware):
  python3 -m unittest tests.test_video_config -v
"""

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_DIR = os.path.join(REPO_ROOT, "BM_Devel_Pi")
REPO_YAML = os.path.join(PI_DIR, "camera_schedule.yaml")

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
import video_recorder as vr  # noqa: E402


def _write_yaml(text):
    f = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    f.write(text)
    f.close()
    return f.name


def _quiet_load(path):
    with contextlib.redirect_stdout(io.StringIO()):
        return vr.load_video_config(path)


# Minimal valid video-mode YAML for dispatch tests. system time source so
# nothing ever wants a UART; explicit geometry matches the repo defaults.
VIDEO_YAML = """
time_source: "system"
capture_mode: "video"
video:
  clip_minutes: 0.25
  fps: 15
  bitrate_mbps: 2.0
"""


class TestVideoIslandDefaults(unittest.TestCase):
    def test_missing_file_resolves_to_spec_defaults(self):
        cfg = _quiet_load("/nonexistent/nowhere.yaml")
        self.assertEqual(cfg["clip_minutes"], 5.0)
        self.assertEqual(cfg["fps"], 15)
        self.assertEqual(cfg["bitrate_mbps"], 2.0)
        self.assertEqual(cfg["session_minutes"], 0)
        self.assertEqual(cfg["dir"], vr.DEFAULT_VIDEO_DIR)
        self.assertEqual(cfg["storage"]["max_used_pct"], 75.0)
        self.assertEqual(cfg["storage"]["min_free_gb"], 10.0)
        self.assertFalse(cfg["storage"]["ring_dry_run"])
        self.assertTrue(cfg["ui"]["enabled"])
        self.assertEqual(cfg["ui"]["port"], 8080)
        self.assertEqual(cfg["source"], "defaults")

    def test_yaml_without_island_resolves_to_defaults(self):
        path = _write_yaml("time_source: \"system\"\ncapture_mode: \"video\"\n")
        cfg = _quiet_load(path)
        self.assertEqual(cfg["source"], "defaults")
        self.assertEqual(cfg["clip_minutes"], 5.0)

    def test_repo_yaml_island_matches_spec_defaults(self):
        cfg = _quiet_load(REPO_YAML)
        self.assertEqual(cfg["source"], "yaml")
        self.assertEqual(cfg["clip_minutes"], 5.0)
        self.assertEqual(cfg["fps"], 15)
        self.assertEqual(cfg["bitrate_mbps"], 2.0)
        self.assertEqual(cfg["session_minutes"], 0)
        self.assertEqual(cfg["storage"]["max_used_pct"], 75.0)
        self.assertEqual(cfg["storage"]["min_free_gb"], 10.0)
        self.assertFalse(cfg["storage"]["ring_dry_run"])
        self.assertTrue(cfg["ui"]["enabled"])
        self.assertEqual(cfg["ui"]["port"], 8080)


class TestVideoIslandParsing(unittest.TestCase):
    def test_full_island_overrides(self):
        path = _write_yaml(
            "video:\n"
            "  clip_minutes: 0.5\n"
            "  fps: 10\n"
            "  bitrate_mbps: 1.5\n"
            "  session_minutes: 30\n"
            "  dir: \"/tmp/vids\"\n"
            "  storage:\n"
            "    max_used_pct: 50\n"
            "    min_free_gb: 20\n"
            "    ring_dry_run: true\n"
            "  ui:\n"
            "    enabled: false\n"
            "    port: 9000\n"
        )
        cfg = _quiet_load(path)
        self.assertEqual(cfg["clip_minutes"], 0.5)
        self.assertEqual(cfg["fps"], 10)
        self.assertEqual(cfg["bitrate_mbps"], 1.5)
        self.assertEqual(cfg["session_minutes"], 30)
        self.assertEqual(cfg["dir"], "/tmp/vids")
        self.assertEqual(cfg["storage"]["max_used_pct"], 50.0)
        self.assertEqual(cfg["storage"]["min_free_gb"], 20.0)
        self.assertTrue(cfg["storage"]["ring_dry_run"])
        self.assertFalse(cfg["ui"]["enabled"])
        self.assertEqual(cfg["ui"]["port"], 9000)
        self.assertEqual(cfg["source"], "yaml")

    def test_partial_island_keeps_other_defaults(self):
        path = _write_yaml("video:\n  clip_minutes: 1\n")
        cfg = _quiet_load(path)
        self.assertEqual(cfg["clip_minutes"], 1.0)
        self.assertEqual(cfg["fps"], 15)
        self.assertEqual(cfg["storage"]["max_used_pct"], 75.0)

    def test_island_does_not_disturb_legacy_keys(self):
        # video.ui.port / video.ui.enabled etc. must never leak into the
        # main schedule parser's top-level fallthrough.
        path = _write_yaml(
            "baudrate: 115200\n"
            "video:\n"
            "  ui:\n"
            "    enabled: false\n"
            "    port: 9000\n"
            "capture_mode: \"video\"\n"
        )
        cfg = load_camera_schedule(path)
        self.assertEqual(cfg.baudrate, 115200)
        self.assertEqual(cfg.capture_mode, "video")

    def test_unknown_video_key_warns_but_does_not_fail(self):
        path = _write_yaml("video:\n  clip_minutes: 1\n  frobnicate: 7\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cfg = vr.load_video_config(path)
        self.assertIn("unknown key video.frobnicate", out.getvalue())
        self.assertEqual(cfg["clip_minutes"], 1.0)


class TestVideoIslandValidation(unittest.TestCase):
    def _assert_rejects(self, island, needle):
        path = _write_yaml(island)
        with self.assertRaises(ValueError) as ctx:
            _quiet_load(path)
        self.assertIn(needle, str(ctx.exception))

    def test_loud_failures_on_nonsense(self):
        self._assert_rejects("video:\n  clip_minutes: 0\n", "video.clip_minutes")
        self._assert_rejects("video:\n  clip_minutes: banana\n", "video.clip_minutes")
        self._assert_rejects("video:\n  fps: 100\n", "video.fps")
        self._assert_rejects("video:\n  fps: 12.5\n", "video.fps")
        self._assert_rejects("video:\n  bitrate_mbps: 0\n", "video.bitrate_mbps")
        self._assert_rejects("video:\n  session_minutes: -1\n", "video.session_minutes")
        self._assert_rejects(
            "video:\n  storage:\n    max_used_pct: 99\n", "video.storage.max_used_pct")
        self._assert_rejects(
            "video:\n  storage:\n    min_free_gb: -1\n", "video.storage.min_free_gb")
        self._assert_rejects(
            "video:\n  storage:\n    ring_dry_run: maybe\n", "video.storage.ring_dry_run")
        self._assert_rejects("video:\n  ui:\n    port: 0\n", "video.ui.port")
        self._assert_rejects("video:\n  ui:\n    enabled: sometimes\n", "video.ui.enabled")


class TestGeometryIsResolvedAtConfigTime(unittest.TestCase):
    """Sprint17: the video island resolves (and upscale-checks) its OWN
    geometry. Sprint15's crop_xywh_to_roi/even_video_output_size are gone —
    they encoded the bug where --roi was divided by the native frame
    regardless of which sensor mode rpicam-vid had actually picked. The
    arithmetic now lives in video_geometry; see tests/test_video_geometry.py.
    """

    def test_removed_sprint15_helpers_are_really_gone(self):
        # A future edit must not quietly reintroduce the buggy helpers.
        self.assertFalse(hasattr(vr, "crop_xywh_to_roi"))
        self.assertFalse(hasattr(vr, "even_video_output_size"))

    def test_empty_island_resolves_to_migration_preset(self):
        vcfg = _quiet_load("/nonexistent/nowhere.yaml")
        geo = vcfg["geometry"]
        self.assertEqual(geo["preset"], "stills_roi_1000p")
        self.assertEqual(geo["source"], "migration-default")
        # The intended stills box, on the full sensor mode, at 0.625x —
        # i.e. a genuine downscale where Sprint15 recorded a 1.88x upscale.
        self.assertEqual(tuple(geo["crop_native_xywh"]), (1504, 846, 1600, 900))
        self.assertEqual(geo["sensor_mode"], "4608x2592")
        self.assertEqual(geo["available_px"], (1600, 900))
        self.assertLess(geo["scale"], 1.0)

    def test_fps_clamped_to_sensor_mode_readout_limit(self):
        # The full-sensor mode reads out at ~14.35 fps; a 15 fps island must
        # be clamped LOUDLY, not refused (rule 5: record, do not brick).
        vcfg = _quiet_load("/nonexistent/nowhere.yaml")
        self.assertEqual(vcfg["geometry"]["fps"], 14)     # effective
        self.assertEqual(vcfg["fps"], 15)                 # still what was asked
        self.assertTrue(any("readout limit" in n
                            for n in vcfg["geometry"]["notes"]))

    def test_upscaling_config_is_refused_by_name(self):
        yaml = ("video:\n"
                "  crop_native_xywh: \"1504,846,1600,900\"\n"
                "  output: \"1920x1080\"\n"
                "  sensor_mode: \"2304x1296\"\n")
        # 1600 native px on the binned mode = 800 available px, far under a
        # 1920 px output. This is the Sprint15 defect shape; it must not boot.
        with self._temp_yaml(yaml) as path:
            with self.assertRaises(ValueError) as ctx:
                _quiet_load(path)
        self.assertIn("refusing to upscale", str(ctx.exception))

    def test_preset_selects_its_own_geometry(self):
        yaml = "video:\n  preset: \"wide_1080p\"\n  fps: 15\n"
        with self._temp_yaml(yaml) as path:
            vcfg = _quiet_load(path)
        geo = vcfg["geometry"]
        self.assertEqual(geo["output_wh"], (1920, 1080))
        self.assertEqual(geo["sensor_mode"], "2304x1296")
        self.assertEqual(geo["roi"], "0.000000,0.000000,1.000000,1.000000")
        self.assertEqual(geo["fps"], 15)

    def test_1080p30_is_blocked(self):
        yaml = "video:\n  preset: \"wide_1080p\"\n  fps: 30\n"
        with self._temp_yaml(yaml) as path:
            with self.assertRaises(ValueError) as ctx:
                _quiet_load(path)
        self.assertIn("blocked", str(ctx.exception))

    def test_30fps_still_allowed_at_720p(self):
        yaml = "video:\n  preset: \"wide_720p\"\n  fps: 30\n"
        with self._temp_yaml(yaml) as path:
            vcfg = _quiet_load(path)
        self.assertEqual(vcfg["geometry"]["fps"], 30)

    @contextlib.contextmanager
    def _temp_yaml(self, text):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        try:
            yield path
        finally:
            os.unlink(path)


class TestEncoderCommand(unittest.TestCase):
    SETTINGS = {
        "capture_backend": "rpicam",
        "crop_native_xywh": (1504, 846, 1600, 900),
        "output_size": (1000, 562),
    }

    def test_argv_from_locked_defaults(self):
        vcfg = _quiet_load("/nonexistent/nowhere.yaml")
        argv, requested = vr.build_encoder_command(
            self.SETTINGS, vcfg, "/tmp/clip.h264.part",
            binary="/usr/bin/libcamera-vid")
        self.assertEqual(argv[0], "/usr/bin/libcamera-vid")
        self.assertIn("-n", argv)
        self.assertIn("--inline", argv)
        joined = " ".join(argv)
        self.assertIn("-t 300000", joined)              # 5 min in ms
        self.assertIn("--codec h264", joined)
        self.assertIn("--width 1000 --height 562", joined)
        # Sprint17: 14, not 15 — the full-sensor mode the migration preset
        # uses reads out at ~14.35 fps, so the island's 15 is clamped.
        self.assertIn("--framerate 14", joined)
        self.assertIn("--bitrate 2000000", joined)
        self.assertIn("--roi 0.326389,0.326389,0.347222,0.347222", joined)
        # D-S17-2: the sensor mode is ALWAYS named. Auto-selection is what
        # picked the 1536x864 mode and produced the 1.88x upscale.
        self.assertIn("--mode 4608:2592:10:P", joined)
        self.assertIn("-o /tmp/clip.h264.part", joined)
        self.assertFalse(requested["camera_controls_enabled"])

    def test_camera_controls_reuse_stills_builder(self):
        # The SAME island shape the stills capture consumes must produce the
        # same control args on the video argv (constraint 4).
        controls = {
            "enabled": True,
            "focus": {"enabled": True, "mode": "manual", "lens_position": 0.7},
            "white_balance": {"enabled": True, "red_gain": 1.5, "blue_gain": 2.1},
        }
        vcfg = _quiet_load("/nonexistent/nowhere.yaml")
        argv, requested = vr.build_encoder_command(
            self.SETTINGS, vcfg, "/tmp/clip.h264.part",
            binary="/usr/bin/libcamera-vid", controls=controls)
        joined = " ".join(argv)
        self.assertIn("--autofocus-mode manual", joined)
        self.assertIn("--lens-position 0.7", joined)
        self.assertIn("--awb custom", joined)
        self.assertIn("--awbgains 1.5,2.1", joined)
        self.assertTrue(requested["camera_controls_enabled"])
        self.assertEqual(requested["requested_lens_position"], 0.7)

    def test_bench_fractional_clip_minutes(self):
        path = _write_yaml("video:\n  clip_minutes: 0.25\n")
        vcfg = _quiet_load(path)
        argv, _ = vr.build_encoder_command(
            self.SETTINGS, vcfg, "/tmp/c.h264.part", binary="/bin/x")
        self.assertIn("-t 15000", " ".join(argv))       # 15 s bench clip


class TestScheduleValidationVideoMode(unittest.TestCase):
    def test_capture_mode_video_accepted(self):
        path = _write_yaml(VIDEO_YAML)
        cfg = load_camera_schedule(path)
        validate_schedule(cfg)  # must not raise
        self.assertEqual(cfg.capture_mode, "video")

    def test_video_mode_validates_inherited_geometry(self):
        path = _write_yaml(
            VIDEO_YAML + "progressive_jpeg:\n  crop:\n    x: 4000\n"
        )
        cfg = load_camera_schedule(path)
        with self.assertRaises(ValueError) as ctx:
            validate_schedule(cfg)
        self.assertIn("crop exceeds source width", str(ctx.exception))

    def test_video_mode_ignores_broken_stills_quality(self):
        # A broken JPEG quality block must never fail-closed a video unit.
        path = _write_yaml(
            VIDEO_YAML + "progressive_jpeg:\n  quality:\n    q_min: 50\n"
        )
        cfg = load_camera_schedule(path)
        validate_schedule(cfg)  # q_min > q_max would fail stills mode

    def test_stills_mode_still_validates_quality(self):
        path = _write_yaml(
            "time_source: \"system\"\ncapture_mode: \"progressive_jpeg\"\n"
            "progressive_jpeg:\n  quality:\n    q_min: 50\n"
        )
        cfg = load_camera_schedule(path)
        with self.assertRaises(ValueError):
            validate_schedule(cfg)

    def test_garbage_capture_mode_rejected(self):
        path = _write_yaml("capture_mode: \"vide0\"\n")
        cfg = load_camera_schedule(path)
        with self.assertRaises(ValueError) as ctx:
            validate_schedule(cfg)
        self.assertIn("capture_mode must be heic, progressive_jpeg, or video",
                      str(ctx.exception))

    def test_video_mode_ladder_fail_soft(self):
        # resolve_rc_settings must survive a garbage ladder in video mode
        # (video never encodes JPEGs) but keep failing loud in stills mode.
        video_path = _write_yaml(
            VIDEO_YAML + "progressive_jpeg:\n  quality:\n    ladder: \"junk\"\n"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            settings = rc.resolve_rc_settings(video_path)
        self.assertEqual(settings["capture_mode"], "video")
        self.assertEqual(settings["ladder_source"], "computed")

        stills_path = _write_yaml(
            "time_source: \"system\"\ncapture_mode: \"progressive_jpeg\"\n"
            "progressive_jpeg:\n  quality:\n    ladder: \"junk\"\n"
        )
        with self.assertRaises(Exception):
            rc.resolve_rc_settings(stills_path)


class TestModeDispatch(unittest.TestCase):
    def test_video_mode_dispatches_to_run_video_mode(self):
        path = _write_yaml(VIDEO_YAML)
        calls = {}

        def fake_run_video_mode(settings, **kwargs):
            calls["settings"] = settings
            calls["kwargs"] = kwargs
            return 0

        with mock.patch.object(vr, "run_video_mode", fake_run_video_mode):
            with contextlib.redirect_stdout(io.StringIO()):
                code = rc.main(["--config-path", path])
        self.assertEqual(code, 0)
        self.assertEqual(calls["settings"]["capture_mode"], "video")
        # The dispatched settings carry the parsed video island + the
        # stills-derived geometry (D-S15-3).
        self.assertEqual(calls["settings"]["video"]["clip_minutes"], 0.25)
        self.assertEqual(calls["settings"]["crop_native_xywh"],
                         (1504, 846, 1600, 900))
        self.assertEqual(calls["settings"]["output_size"], (1000, 562))
        self.assertFalse(calls["kwargs"]["transmit"])

    def test_video_mode_run_failure_exits_1(self):
        path = _write_yaml(VIDEO_YAML)
        with mock.patch.object(vr, "run_video_mode",
                               side_effect=RuntimeError("boom")):
            with contextlib.redirect_stdout(io.StringIO()):
                code = rc.main(["--config-path", path])
        self.assertEqual(code, 1)

    def test_video_mode_bad_island_exits_2(self):
        path = _write_yaml(VIDEO_YAML + "  fps: 999\n")
        # fps: 999 appended inside the island (2-space indent keeps it in
        # the video section).
        with mock.patch.object(vr, "run_video_mode") as run:
            with contextlib.redirect_stdout(io.StringIO()):
                code = rc.main(["--config-path", path])
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_print_config_video_mode_runs_nothing(self):
        path = _write_yaml(VIDEO_YAML)
        with mock.patch.object(vr, "run_video_mode") as run:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = rc.main(["--config-path", path, "--print-config"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        text = out.getvalue()
        self.assertIn("capture_mode=video", text)
        self.assertIn("[VID] video island", text)

    def test_heic_mode_never_touches_video(self):
        path = _write_yaml("time_source: \"system\"\ncapture_mode: \"heic\"\n")
        with mock.patch.object(vr, "run_video_mode") as run:
            with contextlib.redirect_stdout(io.StringIO()):
                code = rc.main(["--config-path", path])
        self.assertEqual(code, 0)
        run.assert_not_called()

    def test_stills_mode_never_touches_video(self):
        # progressive_jpeg mode with --print-config: full stills resolution
        # path, video module untouched.
        path = _write_yaml(
            "time_source: \"system\"\ncapture_mode: \"progressive_jpeg\"\n")
        with mock.patch.object(vr, "run_video_mode") as run:
            with contextlib.redirect_stdout(io.StringIO()):
                code = rc.main(["--config-path", path, "--print-config"])
        self.assertEqual(code, 0)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
