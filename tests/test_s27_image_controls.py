#!/usr/bin/env python3
# filename: test_s27_image_controls.py
# description: Sprint27 (Nick Q2) — camera.image_processing.* ranges / names match the P0 probe on the unit; the video duplicate-option rule fires exactly when the real rpicam-vid argv would repeat an option.
"""
Sprint27 SPEC §9.1 (Nick Q2): the 7 image-processing keys became remote controls with
ranges / names MEASURED on bmcam004 (runs/s27_ladder_20261001/p0_rpicam_limits/ on
feature/r1-hil-test-engineer 27ffd9b: rpicam-apps v1.12.0, libcamera v0.7.1, IMX708):

  sharpness 0..16, contrast 0..32, saturation 0..32, brightness -1..1  (Picamera2 camera_controls)
  denoise   auto off cdn_off cdn_fast cdn_hq                           (rpicam --help; bogus -> 255)
  hdr       off auto sensor single-exp (+ true/false: true = auto)     (rpicam --help; bogus -> 255)

rpicam does NOT refuse out-of-range floats (P0 finding 1), so the registry range is the only
check. A repeated option makes rpicam-vid exit 255 (P0 finding 2): on a video unit
camera.image_processing.denoise / sharpness and video.record.encoder.denoise / sharpness would
both emit --denoise / --sharpness.

Pins:
  - the registry carries exactly the measured ranges / names;
  - PARITY: over every combination of the gate switches and both sides of each pair, the rule
    config_validate._rule_video_duplicate_flags fires IFF the rpicam-vid argv the runtime
    builds (render -> load_video_config + the camera_controls island -> build_encoder_command)
    contains a repeated option;
  - the dispatcher answers e:xk to such a set; a still unit is not judged;
  - the catalog makes the 7 keys writable controls.

Run (repo root):
  python3 -m unittest tests.test_s27_image_controls -v
"""

import contextlib
import io
import itertools
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import config_validate as V  # noqa: E402

MEASURED = {
    "camera.image_processing.sharpness": {"range": (0.0, 16.0)},
    "camera.image_processing.contrast": {"range": (0.0, 32.0)},
    "camera.image_processing.saturation": {"range": (0.0, 32.0)},
    "camera.image_processing.brightness": {"range": (-1.0, 1.0)},
    "camera.image_processing.denoise": {"enum": ("auto", "off", "cdn_off", "cdn_fast", "cdn_hq")},
    "camera.image_processing.hdr": {"enum": (True, False, "off", "auto", "sensor", "single-exp")},
}


def _video_base():
    d = R.defaults()
    d.update({"mode.media": "video", "video.record.framing": "wide_720p"})
    return d


def _argv(values):
    """The rpicam-vid argv the runtime would build for this effective config."""
    import rc_progressive_jpeg as rc
    import video_recorder
    with tempfile.TemporaryDirectory() as tmp:
        render = os.path.join(tmp, "render.yaml")
        with open(render, "w", encoding="utf-8") as fh:
            fh.write(config_v2.render_v1_text(values))
        with contextlib.redirect_stdout(io.StringIO()):
            vcfg = video_recorder.load_video_config(render)
            controls = rc._load_camera_controls_island(render)
            argv, _req = video_recorder.build_encoder_command(
                {"capture_backend": "rpicam"}, vcfg, os.path.join(tmp, "x.h264.part"),
                binary="rpicam-vid", controls=controls)
    return argv


def _repeated(argv):
    opts = [a for a in argv if a.startswith("--")]
    return sorted({o for o in opts if opts.count(o) > 1})


def _dup_hits(values):
    return [v for v in V.validate(values, "effective") if "repeated option" in v.message]


class Measured(unittest.TestCase):
    def test_registry_carries_the_measured_limits(self):
        self.assertEqual(R.REGISTRY_VERSION, 6)
        for path, want in MEASURED.items():
            key = R.BY_PATH[path]
            if "range" in want:
                self.assertEqual(key.range, want["range"], path)
            if "enum" in want:
                self.assertEqual(key.enum, want["enum"], path)

    def test_out_of_range_and_bogus_names_refused(self):
        for path, bad in (("camera.image_processing.sharpness", 16.5),
                          ("camera.image_processing.contrast", -0.1),
                          ("camera.image_processing.brightness", 1.21),
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


class DuplicateFlags(unittest.TestCase):
    def test_rule_fires_iff_the_real_argv_repeats_an_option(self):
        checked = fired = 0
        for ctl, ip_on, ip_dn, enc_dn, ip_sh, enc_sh in itertools.product(
                (True, False), (True, False), (None, "cdn_fast"), ("", "cdn_off"),
                (None, 2.0), (None, 1.0)):
            values = _video_base()
            values.update({"camera.controls_enabled": ctl, "camera.image_processing.enabled": ip_on,
                           "camera.image_processing.denoise": ip_dn,
                           "video.record.encoder.denoise": enc_dn,
                           "camera.image_processing.sharpness": ip_sh,
                           "video.record.encoder.sharpness": enc_sh})
            hits = _dup_hits(values)
            rep = _repeated(_argv(values))
            with self.subTest(ctl=ctl, ip_on=ip_on, ip_dn=ip_dn, enc_dn=enc_dn, ip_sh=ip_sh, enc_sh=enc_sh):
                self.assertEqual(bool(hits), bool(rep), (rep, [h.message for h in hits]))
                for h in hits:
                    self.assertIn("mode.media", h.paths)
            checked += 1
            fired += bool(hits)
        self.assertEqual(checked, 64)
        self.assertGreater(fired, 5)

    def test_still_unit_not_judged(self):
        values = _video_base()
        values.update({"mode.media": "still", "camera.controls_enabled": True,
                       "camera.image_processing.enabled": True,
                       "camera.image_processing.denoise": "cdn_fast",
                       "video.record.encoder.denoise": "cdn_off"})
        self.assertEqual(_dup_hits(values), [])

    def test_dispatcher_answers_xk(self):
        from tests.test_s4_dispatch import Rig
        r = Rig(self, base_over={"mode.media": "video", "video.record.encoder.denoise": "cdn_off",
                                 "camera.controls_enabled": True,
                                 "camera.image_processing.enabled": True})
        r.send({"id": 1_000_400, "c": "set", "kv": {"camera.image_processing.denoise": "cdn_fast"}})
        ack = r.acks()[0]
        self.assertEqual((ack["ok"], ack.get("e"), ack.get("k")),
                         (0, "xk", "camera.image_processing.denoise"))
        r.send({"id": 1_000_401, "c": "set", "kv": {"camera.image_processing.contrast": 1.5,
                                                     "camera.image_processing.hdr": "sensor"}})
        self.assertEqual(r.acks()[0]["ok"], 1)


if __name__ == "__main__":
    unittest.main()
