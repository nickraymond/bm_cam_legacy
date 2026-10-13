#!/usr/bin/env python3
# filename: test_rc_focus_sweep.py
# description: rc_focus_sweep — lens list rules, focus metrics rank sharp over blurred, sweep picks the sharpest lens, manifest + per-frame errors.
"""
Run (repo root):  .venv-dev/bin/python -m pytest tests/test_rc_focus_sweep.py -q
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import rc_focus_sweep as F  # noqa: E402


def board(path, blur=0.0, size=(4608, 2592)):
    """A ChArUco-ish checkerboard in the centre, optionally Gaussian-blurred."""
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("L", size, 128)
    d = ImageDraw.Draw(img)
    cx, cy, sq = size[0] // 2, size[1] // 2, 40
    for i in range(-12, 12):
        for j in range(-12, 12):
            if (i + j) % 2 == 0:
                x, y = cx + i * sq, cy + j * sq
                d.rectangle([x, y, x + sq - 1, y + sq - 1], fill=20)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    img.convert("RGB").save(path, quality=90)
    return path


class LensList(unittest.TestCase):
    def test_default_and_forms(self):
        self.assertEqual(F.lens_list_from(), list(F.DEFAULT_LENS_LIST))
        self.assertEqual(F.lens_list_from(lp=[0, 1, 15]), [0.0, 1.0, 15.0])
        self.assertEqual(F.lens_list_from(lo=0, hi=1.8, step=0.2),
                         [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8])
        self.assertEqual(F.lens_list_from(lo=0.6, hi=1.0, step=0.1), [0.6, 0.7, 0.8, 0.9, 1.0])

    def test_refusals(self):
        for kw in ({"lp": [15.5]}, {"lp": [-0.1]}, {"lp": [True]}, {"lp": "0.8"},
                   {"lp": [0.1] * 13}, {"lo": 0, "hi": 2.4, "step": 0.2},
                   {"lo": 0, "step": 0.1}, {"lp": [1], "step": 1}, {"lo": 0, "hi": 1, "step": -1}):
            with self.assertRaises(F.SweepSpecError, msg=kw):
                F.lens_list_from(**kw)


class Metrics(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="focus_")
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_sharp_beats_blurred_and_crop_coords(self):
        sharp = F.focus_metrics(board(os.path.join(self.d, "a.jpg")))
        soft = F.focus_metrics(board(os.path.join(self.d, "b.jpg"), blur=3))
        softer = F.focus_metrics(board(os.path.join(self.d, "c.jpg"), blur=8))
        self.assertGreater(sharp["lapvar"], soft["lapvar"])
        self.assertGreater(soft["lapvar"], softer["lapvar"])
        self.assertGreater(sharp["tenengrad"], soft["tenengrad"])
        self.assertGreater(soft["tenengrad"], softer["tenengrad"])
        self.assertEqual(sharp["crop_xywh"], [1792, 784, 1024, 1024])

    def test_small_image_crop_clamps(self):
        m = F.focus_metrics(board(os.path.join(self.d, "s.jpg"), size=(640, 360)))
        self.assertEqual(m["crop_xywh"], [140, 0, 360, 360])

    def test_cli(self):
        path = board(os.path.join(self.d, "a.jpg"))
        self.assertEqual(F.main(["--metrics", path]), 0)


class Sweep(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sweep_")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.calls = []

    def capture(self, out_dir, stem, lp):
        """Sharpest at lp 0.8; blur grows with the distance from it. lp 1.2 fails."""
        self.calls.append(lp)
        if lp == 1.2:
            raise RuntimeError("camera busy")
        jpg = board(os.path.join(out_dir, stem + ".jpg"), blur=abs(lp - 0.8) * 6)
        dng = os.path.join(out_dir, stem + ".dng")
        open(dng, "wb").write(b"d" * 100)
        return {"jpg": jpg, "dng": dng, "capture_s": 0.01,
                "metadata": {"LensPosition": lp, "ExposureTime": 10000, "AnalogueGain": 1.12,
                             "SensorTimestamp": 123}}

    def test_best_lp_manifest_and_errors(self):
        lens = [0.0, 0.4, 0.8, 1.2, 1.6]
        res = F.run_sweep(self.capture, lens, self.d, log=lambda *_: None,
                          context={"id": 7})
        self.assertEqual(self.calls, lens)
        self.assertEqual(res["best_lp"], 0.8)
        self.assertEqual(res["best_lp_tenengrad"], 0.8)
        self.assertEqual((res["n"], res["n_ok"]), (5, 4))
        m = json.load(open(os.path.join(self.d, "sweep_manifest.json")))
        self.assertEqual(m["best_lp"], 0.8)
        self.assertEqual(m["context"], {"id": 7})
        bad = [f for f in m["frames"] if f["lens_requested"] == 1.2][0]
        self.assertIn("camera busy", bad["error"])
        good = [f for f in m["frames"] if f["lens_requested"] == 0.8][0]
        self.assertTrue(good["jpg"].endswith("_sweep_lp0.80.jpg"))
        self.assertEqual(good["metadata"]["LensPosition"], 0.8)
        self.assertEqual(good["metadata"]["AnalogueGain"], 1.12)
        self.assertIn("lapvar", good["focus"])

    def test_stop_cuts_the_sweep(self):
        res = F.run_sweep(self.capture, [0.0, 0.4, 0.8], self.d, log=lambda *_: None,
                          stop_fn=lambda: len(self.calls) >= 1)
        self.assertEqual(self.calls, [0.0])
        self.assertTrue(res["stopped"])
        self.assertEqual(res["best_lp"], 0.0)

    def test_metric_failure_is_recorded(self):
        def bad_metric(path):
            raise ValueError("decode")
        res = F.run_sweep(self.capture, [0.8], self.d, log=lambda *_: None, metric_fn=bad_metric)
        self.assertIsNone(res["best_lp"])
        self.assertIn("decode", res["frames"][0]["metric_error"])

    def test_with_manual_focus(self):
        cs = {"camera_controls": {"enabled": False, "focus": {"mode": "auto"},
                                  "exposure": {"enabled": True}}, "exposure_profile": {"x": 1}}
        out = F.with_manual_focus(cs, 0.0)
        self.assertEqual(out["camera_controls"]["focus"],
                         {"enabled": True, "mode": "manual", "lens_position": 0.0})
        self.assertEqual(out["camera_controls"]["exposure"], {"enabled": True})
        self.assertEqual(out["exposure_profile"], {"x": 1})
        self.assertEqual(cs["camera_controls"]["focus"], {"mode": "auto"})


if __name__ == "__main__":
    unittest.main()
