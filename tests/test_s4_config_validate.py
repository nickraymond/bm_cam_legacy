#!/usr/bin/env python3
# filename: test_s4_config_validate.py
# description: Sprint26 S4 a.3 — the pure whole-config validator: purity, one test per rule, scopes, derived width, S3 parity oracle.
"""
Sprint26 S4 commit a.3 (PLAN_S4.md a.3, G3, G12, G14; R12).

Pins:
  - validate() never touches subprocess, shutil.which, open() or zoneinfo
    (all patched to raise) — environment facts come in through `env`;
  - each S4 rule fires exactly when it should (manual WB gains, video crop,
    video cap floor 80, ffmpeg, time zone);
  - scopes: `base` = the S3 rules only; `strict` adds the S4 rules;
    `effective` also derives the sent still width instead of refusing it;
  - base_rules() reproduces the S3 config_v2._cross_key_errors output byte
    for byte (a frozen copy is the oracle) over randomised inputs;
  - every device profile passes strict and effective;
  - a strict load gains the S4 rules; a plain boot load does not.

Run (repo root):
  python3 -m unittest tests.test_s4_config_validate -v
"""

import builtins
import contextlib
import io
import os
import random
import shutil
import subprocess
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v1_reader  # noqa: E402
import config_v2 as C  # noqa: E402
import config_validate as V  # noqa: E402
from tests.test_config_v2 import Unit, quiet  # noqa: E402

PROFILES = ("bmcam000", "bmcam001", "bmcam002", "bmcam003", "rc_field_template",
            "bmcam003/live_20260925", "bmcam004/live_20260925")   # the live bench pulls
ENV_OK = {"ffmpeg": True, "timezones_ok": {"America/Los_Angeles", "America/New_York", "UTC"}}


def profile_values(unit):
    path = os.path.join(REPO_ROOT, "device_profiles", unit, "camera_schedule.yaml")
    with contextlib.redirect_stdout(io.StringIO()):
        got = config_v1_reader.read_v1(path)
    assert not got.problems, got.problems
    return dict(got.values)


def still_values(**over):
    v = R.defaults()
    v["mode.media"] = "still"
    v.update(over)
    return v


def codes(violations):
    return [(x.code, x.paths[0]) for x in violations]


def _frozen_s3_cross_key_errors(values):
    """config_v2._cross_key_errors at 43d4d03 (S3c), verbatim: the oracle."""
    errs = []
    crop, w, h = values.get("still.crop"), values.get("camera.native.width"), \
        values.get("camera.native.height")
    if isinstance(crop, list) and len(crop) == 4 and isinstance(w, int) and isinstance(h, int):
        x, y, cw, ch = crop
        if x + cw > w or y + ch > h:
            errs.append(("still.crop", f"{crop} does not fit the native frame {w}x{h}"))
        ow = values.get("still.output_width")
        if isinstance(ow, int) and ow > cw:
            errs.append(("still.output_width", f"{ow} is wider than still.crop w {cw} "
                         "(no upscale)"))
    for path in ("mode.run", "mode.output"):
        key = R.BY_PATH[path]
        if key.runnable and values.get(path) not in key.runnable:
            errs.append((path, f"{values.get(path)!r} is not runnable "
                               f"(runnable: {', '.join(key.runnable)})"))
    if values.get("mode.run") == "stay_on":
        if values.get("commands.runtime") != "supervisor":
            errs.append(("mode.run", "stay_on needs commands.runtime: supervisor"))
        if values.get("commands.enabled") is not True:
            errs.append(("mode.run", "stay_on needs commands.enabled: true"))
    if values.get("mode.output") == "save_local":
        if values.get("commands.runtime") != "supervisor":
            errs.append(("mode.output", "save_local needs commands.runtime: supervisor"))
        if values.get("commands.enabled") is not True:
            errs.append(("mode.output", "save_local needs commands.enabled: true"))
    for path in ("mode.interval_s", "mode.heartbeat_s"):
        v = values.get(path)
        if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 60:
            errs.append((path, f"{v} must be 0 (off) or at least 60 s"))
    return errs


class Purity(unittest.TestCase):
    def test_no_subprocess_file_or_tzdata(self):
        def boom(*a, **k):
            raise AssertionError("the validator must be pure")
        samples = [profile_values(u) for u in PROFILES]
        samples.append(still_values(**{"schedule.timezone": "Mars/Olympus"}))
        import zoneinfo
        with mock.patch.object(subprocess, "run", boom), \
                mock.patch.object(subprocess, "Popen", boom), \
                mock.patch.object(shutil, "which", boom), \
                mock.patch.object(builtins, "open", boom), \
                mock.patch.object(zoneinfo, "ZoneInfo", boom):
            for values in samples:
                for scope in ("base", "strict", "effective"):
                    V.validate(values, scope, env=ENV_OK)
                V.derived_output_width(values)


class Rules(unittest.TestCase):
    def test_manual_wb_needs_gains(self):
        bad = still_values(**{"camera.white_balance.mode": "manual"})
        self.assertIn(("xk", "camera.white_balance.mode"), codes(V.validate(bad)))
        good = still_values(**{"camera.white_balance.mode": "manual",
                               "camera.white_balance.gains": [1.62, 1.91]})
        self.assertEqual(V.validate(good), [])
        self.assertEqual(V.validate(bad, "base"), [])      # S3 boot rules unchanged

    def test_video_crop_inside_native(self):
        bad = still_values(**{"video.record.crop": [4000, 0, 1000, 500]})
        v = V.validate(bad)
        self.assertEqual(codes(v), [("xk", "video.record.crop")])
        self.assertEqual(V.validate(still_values(**{"video.record.crop": [0, 0, 4608, 2592]})), [])

    def test_video_cap_floor(self):
        for cap, ok in ((79, False), (80, True), (126, True), (8, False)):
            vals = still_values(**{"mode.media": "video", "video.send.message_cap": cap})
            got = codes(V.validate(vals))
            self.assertEqual(got == [], ok, (cap, got))
        # a still unit's unused video cap is not judged (a trg kv media=video
        # copy is, because its mode.media is video)
        self.assertEqual(V.validate(still_values(**{"video.send.message_cap": 40})), [])
        self.assertEqual(V.VIDEO_CAP_FLOOR, 80)

    def test_env_ffmpeg_and_timezone(self):
        vid = still_values(**{"mode.media": "video"})
        self.assertEqual(codes(V.validate(vid, env={"ffmpeg": False, "timezones_ok": None})),
                         [("xk", "mode.media")])
        self.assertEqual(V.validate(vid, env=None), [])
        tz = still_values(**{"schedule.timezone": "Mars/Olympus"})
        self.assertEqual(codes(V.validate(tz, env=ENV_OK)), [("xk", "schedule.timezone")])
        self.assertEqual(V.validate(tz, env=None), [])     # no env = not judged here

    def test_per_key_and_unknown(self):
        bad = still_values(**{"camera.exposure.ev": float("nan"), "no.such": 1,
                              "still.message_cap": True})
        got = codes(V.validate(bad))
        self.assertIn(("val", "camera.exposure.ev"), got)
        self.assertIn(("val", "still.message_cap"), got)
        self.assertIn(("val", "no.such"), got)

    def test_every_path_is_reported(self):
        v = V.validate(still_values(**{"still.output_width": 2000}), "strict")
        self.assertEqual([x.paths for x in v], [("still.output_width", "still.crop")])


class DerivedWidth(unittest.TestCase):
    def test_effective_derives_base_refuses(self):
        roi5 = still_values(**{"still.crop": [1904, 1071, 800, 450]})   # output_width 1000
        self.assertEqual(V.validate(roi5, "effective"), [])
        self.assertEqual(codes(V.validate(roi5, "base")), [("xk", "still.output_width")])
        self.assertEqual(V.derived_output_width(roi5), 800)
        self.assertEqual(V.derived_output_width(still_values()), 1000)
        roi6 = still_values(**{"still.crop": [1984, 1116, 640, 360]})
        self.assertEqual(V.derived_output_width(roi6), 640)


class S3Parity(unittest.TestCase):
    def test_base_rules_match_the_frozen_s3_rules(self):
        rng = random.Random(4)
        choices = {
            "still.crop": [[1504, 846, 1600, 900], [4000, 2000, 1000, 900], [0, 0, 4608, 2592],
                           [1904, 1071, 800, 450], "junk", None],
            "still.output_width": [1000, 2000, 640, None],
            "mode.run": ["per_boot", "stay_on", None],
            "mode.output": ["transmit", "save_local"],
            "commands.runtime": ["legacy", "supervisor", None],
            "commands.enabled": [True, False, 1],
            "mode.interval_s": [0, 30, 60, True],
            "mode.heartbeat_s": [0, 59, 300],
            "camera.native.width": [4608, 1000, "x"],
        }
        for _ in range(2000):
            values = still_values(**{k: rng.choice(v) for k, v in choices.items()})
            self.assertEqual(V.base_rules(values), _frozen_s3_cross_key_errors(values))
            self.assertEqual(C._cross_key_errors(values), _frozen_s3_cross_key_errors(values))


class Profiles(unittest.TestCase):
    def test_every_profile_passes_strict_and_effective(self):
        for unit in PROFILES:
            values = profile_values(unit)
            for scope in ("strict", "effective"):
                self.assertEqual(V.validate(values, scope, env=ENV_OK), [], (unit, scope))


class Loader(unittest.TestCase):
    def test_strict_load_gains_s4_rules_boot_load_does_not(self):
        u = Unit(self)
        u.edit_v2("camera.white_balance.mode", "manual")
        with self.assertRaises(C.ConfigError) as ctx:
            C.load_config(u.v2, strict=True)
        self.assertIn("camera.white_balance.gains", str(ctx.exception))
        cfg = C.load_config(u.v2, strict=False)
        self.assertEqual(cfg.errors, [])
        self.assertEqual(quiet(u.boot).level, "v2")


class ProbeEnv(unittest.TestCase):
    def test_probe(self):
        env = V.probe_env(["UTC", "Mars/Olympus", None])
        self.assertIn("UTC", env["timezones_ok"])
        self.assertNotIn("Mars/Olympus", env["timezones_ok"])
        self.assertIsInstance(env["ffmpeg"], bool)


if __name__ == "__main__":
    unittest.main()
