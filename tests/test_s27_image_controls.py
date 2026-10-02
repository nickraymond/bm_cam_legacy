#!/usr/bin/env python3
# filename: test_s27_image_controls.py
# description: Sprint27 (Nick Q2) — camera.image_processing.* ranges / names match the P0 probe on the unit.
"""
Sprint27 SPEC §9.1 (Nick Q2): the 7 image-processing keys became remote controls with
ranges / names MEASURED on bmcam004 (runs/s27_ladder_20261001/p0_rpicam_limits/ on
feature/r1-hil-test-engineer 27ffd9b: rpicam-apps v1.12.0, libcamera v0.7.1, IMX708):

  sharpness 0..16, contrast 0..32, saturation 0..32, brightness -1..1  (Picamera2 camera_controls)
  -> F-G3-5 (Nick 2026-10-02): contrast 0.5..2, saturation 0..2, brightness -0.25..0.25, the
     USABLE range from the TE's daylight probe (runs/s27_ip_range_probe_20261002/stats.csv)
  denoise   auto off cdn_off cdn_fast cdn_hq                           (rpicam --help; bogus -> 255)
  hdr       off auto sensor single-exp (+ true/false: true = auto)     (rpicam --help; bogus -> 255)

rpicam does NOT refuse out-of-range floats (P0 finding 1), so the registry range is the only
check. (The duplicate-option rule this file also pinned was removed with F-G3-4: the encoder
knobs that duplicated --denoise / --sharpness are retired; see test_s27_retired_encoder_knobs.)

Pins:
  - the registry carries exactly the measured ranges / names;
  - out-of-range and bogus values are refused;
  - the catalog makes the 7 keys writable controls.

Run (repo root):
  python3 -m unittest tests.test_s27_image_controls -v
"""

import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import config_registry as R  # noqa: E402

MEASURED = {
    "camera.image_processing.sharpness": {"range": (0.0, 16.0)},
    # F-G3-5: narrowed to the usable range (daylight probe, bmcam003 2026-10-02)
    "camera.image_processing.contrast": {"range": (0.5, 2.0)},
    "camera.image_processing.saturation": {"range": (0.0, 2.0)},
    "camera.image_processing.brightness": {"range": (-0.25, 0.25)},
    "camera.image_processing.denoise": {"enum": ("auto", "off", "cdn_off", "cdn_fast", "cdn_hq")},
    "camera.image_processing.hdr": {"enum": (True, False, "off", "auto", "sensor", "single-exp")},
}


class Measured(unittest.TestCase):
    def test_registry_carries_the_measured_limits(self):
        self.assertGreaterEqual(R.REGISTRY_VERSION, 6)      # 7 = F-G3-4 retired encoder knobs
        for path, want in MEASURED.items():
            key = R.BY_PATH[path]
            if "range" in want:
                self.assertEqual(key.range, want["range"], path)
            if "enum" in want:
                self.assertEqual(key.enum, want["enum"], path)

    def test_out_of_range_and_bogus_names_refused(self):
        for path, bad in (("camera.image_processing.sharpness", 16.5),
                          ("camera.image_processing.contrast", 0.25),
                          ("camera.image_processing.contrast", 3.0),
                          ("camera.image_processing.saturation", 3.0),
                          ("camera.image_processing.brightness", 0.5),
                          ("camera.image_processing.brightness", -0.3),
                          ("camera.image_processing.denoise", "bogus"),
                          ("camera.image_processing.hdr", "bogus")):
            self.assertIsNotNone(R.check_value(R.BY_PATH[path], bad), (path, bad))

    def test_catalog_makes_them_controls(self):
        import gen_config_catalog as G
        cat = json.loads(G.render())
        for path in MEASURED:
            self.assertEqual(next(k for k in cat["keys"] if k["path"] == path)["tier"], "control", path)
        self.assertEqual(next(k for k in cat["keys"] if k["path"] == "camera.exposure.mode")["tier"],
                         "engineering")


if __name__ == "__main__":
    unittest.main()
