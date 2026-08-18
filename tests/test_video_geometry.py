#!/usr/bin/env python3
# filename: test_video_geometry.py
# description: Sprint17 — video-only geometry, ROI vs sensor-mode FOV, no-upscale rule.
"""
Sprint17 geometry tests (D-S17-1..3).

These tests exist because Sprint15 shipped a defect that no test could have
caught: the video path assumed `--roi` fractions were relative to the sensor
and that a requested output size described real detail. Measured on bmcam000
2026-08-18 (runs/sprint17_sensor_mode_probe_20260818/), neither held —
production video ran at a 1.88x upscale on a field of view nobody configured.

So the assertions below are anchored to MEASURED hardware values, not to the
module's own arithmetic:
  - mode 1536x864 covers only the centre 3072x1728 of the array
  - the shipped argv left 533x299 real px behind a 1000x562 output
  - 2304x1296 output cannot be encoded at all
  - the full sensor mode reads out at ~14.3 fps

Run: python3 -m unittest tests.test_video_geometry -v
"""

import contextlib
import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import video_geometry as vg  # noqa: E402


class TestMeasuredHardwareFacts(unittest.TestCase):
    """The table must keep matching what the probe measured on bmcam000."""

    def test_1536_mode_field_of_view_is_centre_crop_not_full_sensor(self):
        # THE Sprint15 trap. libcamera reported
        # ScalerCrop : [(768, 432)/128x128..(768, 432)/3072x1728]
        self.assertEqual(vg.SENSOR_MODES["1536x864"]["fov_xywh"],
                         (768, 432, 3072, 1728))

    def test_wider_modes_cover_the_whole_sensor(self):
        for key in ("2304x1296", "4608x2592"):
            self.assertEqual(vg.SENSOR_MODES[key]["fov_xywh"],
                             (0, 0, vg.NATIVE_W, vg.NATIVE_H))

    def test_full_mode_readout_is_capped_near_14_fps(self):
        self.assertLess(vg.SENSOR_MODES["4608x2592"]["max_fps"], 15)

    def test_encoder_ceiling_is_1080p(self):
        # 2304x1296 failed with "failed to start output streaming" (rc=255).
        self.assertEqual((vg.MAX_ENCODE_W, vg.MAX_ENCODE_H), (1920, 1080))
        with self.assertRaises(vg.GeometryError) as ctx:
            vg.parse_output("2304x1296")
        self.assertIn("encoder ceiling", str(ctx.exception))


class TestTheShippedDefect(unittest.TestCase):
    """Reproduce the Sprint15 numbers exactly, then prove they are refused."""

    SHIPPED_APPLIED_CROP = (1770, 996, 1066, 599)   # what libcamera actually used

    def test_available_pixels_reproduces_the_measured_533x299(self):
        avail = vg.available_pixels(self.SHIPPED_APPLIED_CROP, "1536x864")
        self.assertEqual(avail, (533, 299))

    def test_that_geometry_is_now_refused_by_name(self):
        with self.assertRaises(vg.GeometryError) as ctx:
            vg.resolve_geometry({
                "crop_native_xywh": "1770,996,1066,599",
                "output": "1000x562",
                "sensor_mode": "1536x864",
            })
        msg = str(ctx.exception)
        self.assertIn("refusing to upscale", msg)
        self.assertIn("533x299", msg)      # the arithmetic is in the message

    def test_intended_crop_on_the_1536_mode_frames_a_different_picture(self):
        # The whole reason video FOV never matched stills FOV: the same crop
        # numbers produce different roi fractions on a centre-only mode.
        intended = (1504, 846, 1600, 900)
        roi_full = vg.crop_to_roi(intended, "4608x2592")
        roi_narrow = vg.crop_to_roi(intended, "1536x864")
        self.assertNotEqual(roi_full, roi_narrow)
        self.assertEqual(roi_full, "0.326389,0.326389,0.347222,0.347222")


class TestRoiIsRelativeToTheModeField(unittest.TestCase):
    def test_full_sensor_crop_is_the_whole_frame_on_a_full_fov_mode(self):
        self.assertEqual(vg.crop_to_roi((0, 0, 4608, 2592), "2304x1296"),
                         "0.000000,0.000000,1.000000,1.000000")

    def test_offset_is_measured_from_the_field_origin(self):
        # On the 1536x864 mode the field starts at (768, 432), so a crop that
        # starts exactly there is roi origin 0,0 — NOT 768/4608.
        self.assertTrue(
            vg.crop_to_roi((768, 432, 3072, 1728), "1536x864").startswith(
                "0.000000,0.000000"))

    def test_crop_outside_the_mode_field_is_refused(self):
        # A corner of the sensor simply is not visible on a centre-only mode.
        with self.assertRaises(vg.GeometryError) as ctx:
            vg.crop_to_roi((0, 0, 1600, 900), "1536x864")
        self.assertIn("field of view", str(ctx.exception))

    def test_unknown_mode_named_loudly(self):
        with self.assertRaises(vg.GeometryError) as ctx:
            vg.crop_to_roi((0, 0, 100, 100), "9999x9999")
        self.assertIn("unknown video.sensor_mode", str(ctx.exception))


class TestNoUpscaleRule(unittest.TestCase):
    def test_exact_1to1_is_allowed(self):
        geo = vg.resolve_geometry({"preset": "stills_roi_1600p"})
        self.assertEqual(geo["scale"], 1.0)
        self.assertEqual(geo["available_px"], (1600, 900))

    def test_downscale_is_allowed(self):
        geo = vg.resolve_geometry({"preset": "wide_1080p"})
        self.assertLess(geo["scale"], 1.0)

    def test_one_pixel_over_is_refused(self):
        # 1600 available px, 1604 asked for -> beyond the round-down slack.
        with self.assertRaises(vg.GeometryError):
            vg.resolve_geometry({
                "crop_native_xywh": "1504,846,1600,900",
                "output": "1604x900",
                "sensor_mode": "4608x2592",
            })

    def test_libcamera_round_down_slack_is_tolerated(self):
        # libcamera lands a 1600 px request at 1599; that is not upscaling.
        self.assertEqual(vg.UPSCALE_SLACK_PX, 2)
        geo = vg.resolve_geometry({
            "crop_native_xywh": "1504,846,1600,900",
            "output": "1600x900",
            "sensor_mode": "4608x2592",
        })
        self.assertEqual(geo["scale"], 1.0)

    def test_resolve_never_returns_a_scale_above_one(self):
        for name in vg.PRESETS:
            geo = vg.resolve_geometry({"preset": name})
            self.assertLessEqual(
                geo["scale"], 1.0,
                f"preset {name} would upscale — no preset may ship fake detail")


class TestPresetTable(unittest.TestCase):
    """SPEC 4, locked with Nick 2026-08-18."""

    EXPECTED = {
        "wide_1080p", "wide_1080p_lean", "wide_720p", "wide_720p_lean",
        "stills_roi_1000p", "stills_roi_1600p",
    }

    def test_exact_membership(self):
        self.assertEqual(set(vg.PRESETS), self.EXPECTED)

    def test_no_preset_uses_the_narrow_field_mode(self):
        # 1536x864 stays reachable only by explicit override, never by preset.
        for name, row in vg.PRESETS.items():
            self.assertNotEqual(row["mode"], "1536x864", f"preset {name}")

    def test_every_preset_names_its_mode_explicitly(self):
        for name, row in vg.PRESETS.items():
            self.assertIn(row["mode"], vg.SENSOR_MODES, f"preset {name}")

    def test_every_preset_fits_the_encoder_ceiling(self):
        for name, row in vg.PRESETS.items():
            w, h = row["output"]
            self.assertLessEqual(w, vg.MAX_ENCODE_W, f"preset {name}")
            self.assertLessEqual(h, vg.MAX_ENCODE_H, f"preset {name}")

    def test_unknown_preset_named_loudly(self):
        with self.assertRaises(vg.GeometryError) as ctx:
            vg.resolve_geometry({"preset": "gopro_mode"})
        self.assertIn("unknown video.preset", str(ctx.exception))


class TestMigrationDefault(unittest.TestCase):
    """SPEC 5.1 option A (Nick 2026-08-18): an island with no geometry keys
    lands on the geometry its YAML always MEANT."""

    def test_empty_island_lands_on_stills_roi_1000p(self):
        geo = vg.resolve_geometry({})
        self.assertEqual(geo["preset"], "stills_roi_1000p")
        self.assertEqual(geo["source"], "migration-default")

    def test_migration_announces_that_the_picture_changes(self):
        geo = vg.resolve_geometry({})
        self.assertTrue(any("1.88x upscale" in n for n in geo["notes"]),
                        "migration must say the framing changes, loudly")

    def test_migration_delivers_real_detail_where_sprint15_upscaled(self):
        geo = vg.resolve_geometry({})
        # 533x299 before, 1600x900 behind the same 1000x562 output.
        self.assertEqual(geo["available_px"], (1600, 900))
        self.assertEqual(geo["output_wh"], (1000, 562))


class TestFpsRules(unittest.TestCase):
    def test_fps_clamped_to_mode_readout_not_refused(self):
        fps, notes = vg.clamp_fps(15, "4608x2592", (1000, 562))
        self.assertEqual(fps, 14)
        self.assertTrue(notes)

    def test_fps_under_the_cap_untouched(self):
        fps, notes = vg.clamp_fps(15, "2304x1296", (1920, 1080))
        self.assertEqual(fps, 15)
        self.assertEqual(notes, [])

    def test_1080p30_blocked_because_it_would_lose_every_clip(self):
        with self.assertRaises(vg.GeometryError) as ctx:
            vg.clamp_fps(30, "2304x1296", (1920, 1080))
        self.assertIn("blocked", str(ctx.exception))

    def test_720p30_allowed(self):
        fps, _notes = vg.clamp_fps(30, "2304x1296", (1280, 720))
        self.assertEqual(fps, 30)


class TestParsing(unittest.TestCase):
    def test_crop_accepts_bare_and_bracketed_forms(self):
        self.assertEqual(vg.parse_crop("1504,846,1600,900"),
                         (1504, 846, 1600, 900))
        self.assertEqual(vg.parse_crop("[0, 0, 4608, 2592]"),
                         (0, 0, 4608, 2592))
        self.assertEqual(vg.parse_crop([0, 0, 4608, 2592]), (0, 0, 4608, 2592))

    def test_crop_outside_native_frame_refused(self):
        with self.assertRaises(vg.GeometryError):
            vg.parse_crop("4000,0,1600,900")

    def test_crop_arity_and_type_refused(self):
        for bad in ("1,2,3", "a,b,c,d", "1,2,3,4,5"):
            with self.assertRaises(vg.GeometryError):
                vg.parse_crop(bad)

    def test_output_rounds_odd_dimensions_down(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(vg.parse_output("1001x563"), (1000, 562))

    def test_output_shape_validated(self):
        for bad in ("1920", "axb", "0x0"):
            with self.assertRaises(vg.GeometryError):
                vg.parse_output(bad)

    def test_mode_argument_shape(self):
        self.assertEqual(vg.mode_argument("2304x1296"), "2304:1296:10:P")


class TestModeAutoPick(unittest.TestCase):
    def test_prefers_the_cheap_binned_mode_when_it_suffices(self):
        # Full-sensor crop at 1080p: the binned readout has 2304 px, plenty.
        self.assertEqual(vg.pick_sensor_mode((0, 0, 4608, 2592), (1920, 1080)),
                         "2304x1296")

    def test_falls_through_to_the_full_mode_when_detail_demands_it(self):
        # A 1600 px crop only yields 800 px binned — needs the full readout.
        self.assertEqual(vg.pick_sensor_mode((1504, 846, 1600, 900), (1000, 562)),
                         "4608x2592")

    def test_refuses_when_no_mode_can_supply_the_output(self):
        with self.assertRaises(vg.GeometryError) as ctx:
            vg.pick_sensor_mode((0, 0, 400, 224), (1920, 1080))
        self.assertIn("without upscaling", str(ctx.exception))


class TestFieldMath(unittest.TestCase):
    """The numbers that decide what is field-viable (SPEC scope item 5)."""

    def test_storage_math_matches_the_spec_table(self):
        sm = vg.storage_math(9.3)
        self.assertAlmostEqual(sm["gb_per_day"], 100.4, places=1)
        self.assertLess(sm["ring_days"], 1.0)      # ~0.9 d, the 1080p cost

    def test_shipped_bitrate_was_already_near_the_quality_class(self):
        # 2 Mbps at 1000x562/15 = 0.237 bits/px/frame vs Hero8 class ~0.3.
        # Bitrate was never why the footage looked soft.
        self.assertAlmostEqual(
            vg.bits_per_pixel_frame(2.0, (1000, 562), 15), 0.237, places=3)

    def test_describe_states_every_coordinate_system(self):
        lines = "\n".join(vg.describe(vg.resolve_geometry({}), 2.0))
        for expected in ("NATIVE", "sensor mode", "--roi", "available detail",
                         "scale", "GB/day", "ring window"):
            self.assertIn(expected, lines)


if __name__ == "__main__":
    unittest.main()
