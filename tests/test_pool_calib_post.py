#!/usr/bin/env python3
# filename: test_pool_calib_post.py
# description: tools/pool_calib_post (Nick's Q1 numbers): area-scale / displacement maths on known lenses, observed-area-only rules.
"""
Checks the post-run maths behind the pool049 report's question-1 answer:
  - a distortion-free lens has area scale 1, zero displacement and zero density error everywhere;
  - a barrel lens covers MORE seafloor per pixel toward the edge (area scale > 1, growing with radius);
  - q1 reports only inside the observed area (hull), never the whole frame, and finds the 5 % radius;
  - lens_error_budget labels the whole frame as extrapolated and gives 0 % for a perfect lens.
Run (repo root):  .venv-dev/bin/python -m pytest -q tests/test_pool_calib_post.py
"""
import csv
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "pool_calib_post"))

try:
    import numpy as np
    import scipy  # noqa: F401
    import matplotlib  # noqa: F401
    import q1_observed_area as q1
    import lens_error_budget as lb
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

W, H = 1280, 800
K = [[1000.0, 0.0, 639.5], [0.0, 1000.0, 399.5], [0.0, 0.0, 1.0]]


def write_cam(root, D, hull, model="standard"):
    """A minimal finished-pipeline camera folder: intrinsics, coverage hull, models (3 identical), bootstrap."""
    os.makedirs(root, exist_ok=True)
    json.dump({"model": model, "camera_matrix": K, "dist_coeffs": D, "image_size": [W, H]},
              open(os.path.join(root, f"intrinsics_full_{W}x{H}.json"), "w"))
    json.dump({"observed_hull_px": hull}, open(os.path.join(root, "coverage.json"), "w"))
    json.dump({"models": {m: {"K": K, "D": D} for m in ("standard",)}}, open(os.path.join(root, "models.json"), "w"))
    with open(os.path.join(root, "bootstrap.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["replicate", "fx", "fy", "cx", "cy", "rms_px", "dist_coeffs", "error"])
        for i, f in enumerate((999.0, 1000.0, 1001.0)):
            wr.writerow([i, f, f, 639.5, 399.5, 0.3, " ".join(str(v) for v in D), ""])
    return root


TOP = [[40, 20], [1240, 20], [1240, 500], [40, 500]]          # observed area = the upper ~60 % (like pool049)


@unittest.skipUnless(HAVE_DEPS, "numpy / scipy / matplotlib not installed")
class TestQ1(unittest.TestCase):
    def test_perfect_lens_has_no_error(self):
        Km, D = np.array(K), np.zeros(5)
        p = np.array([[10.0, 10.0], [639.5, 399.5], [1200.0, 700.0]])
        a = q1.detj("standard", Km, D, p) * Km[0, 0] * Km[1, 1]
        self.assertTrue(np.allclose(a, 1.0, atol=1e-9))
        with tempfile.TemporaryDirectory() as t:
            res = q1.analyse(write_cam(os.path.join(t, "cam"), [0, 0, 0, 0, 0], TOP), 8)
        self.assertIsNone(res["r_area_dev_gt5pct"])
        self.assertAlmostEqual(res["density_error_if_skipped_observed_area_pct"], 0.0, places=6)
        self.assertTrue(all(abs(r["disp_px_median"]) < 1e-6 for r in res["rings"]))

    def test_barrel_area_grows_with_radius_and_whole_frame_is_not_computed(self):
        with tempfile.TemporaryDirectory() as t:
            res = q1.analyse(write_cam(os.path.join(t, "cam"), [-0.25, 0.05, 0, 0, 0], TOP), 8)
        scales = [r["area_scale_median"] for r in res["rings"]]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(scales, scales[1:])))   # barrel: more area per px outward
        self.assertGreater(scales[-1], 1.05)
        self.assertIsNotNone(res["r_area_dev_gt5pct"])
        self.assertGreater(res["density_error_if_skipped_observed_area_pct"], 0)  # pinhole underestimates the area
        self.assertTrue(res["whole_frame"].startswith("NOT COMPUTED"))
        self.assertLess(res["observed_frac_of_frame"], 0.7)
        crop = res["central_crops"]["frame_centre"]
        self.assertIn(crop["limited_by"], ("observed area", "5 % area scale"))
        # the reported crop lies wholly inside the observed hull
        x, y, w, h = crop["crop"]["xywh"]
        self.assertTrue(x >= 40 and y >= 20 and x + w <= 1240 and y + h <= 500)


@unittest.skipUnless(HAVE_DEPS, "numpy / scipy / matplotlib not installed")
class TestBudget(unittest.TestCase):
    def test_perfect_lens_and_labels(self):
        with tempfile.TemporaryDirectory() as t:
            res = lb.budget(write_cam(os.path.join(t, "cam"), [0, 0, 0, 0, 0], TOP), 750.0, 8)
        self.assertIn(lb.FULL, res["regions"])
        self.assertNotIn("full", res["regions"])                      # never an unlabelled whole-frame number
        self.assertTrue(lb.FULL.endswith("EXTRAPOLATED_not_measured"))
        for r in res["regions"].values():
            self.assertAlmostEqual(r["density_error_if_ignored_pct"], 0.0, places=6)
        # in-air focal 750 px vs underwater 1000 px: area overestimated (1000/750)^2, density 0.5625x
        self.assertAlmostEqual(res["density_error_if_in_air_fx_pct"], 100 * (750 ** 2 / 1000 ** 2 - 1), places=6)
        self.assertAlmostEqual(res["area_ratio_in_air_vs_water_pct"], 100 * (1000 ** 2 / 750 ** 2 - 1), places=6)


if __name__ == "__main__":
    unittest.main()
