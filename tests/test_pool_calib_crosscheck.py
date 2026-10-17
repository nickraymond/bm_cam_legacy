#!/usr/bin/env python3
# filename: test_pool_calib_crosscheck.py
# description: tools/pool_calib_crosscheck.py: the pre-registered DNG-vs-JPEG bar (R3) on hand-made pipeline outputs.
"""
Checks the cross-check bar from docs/pool_calib_prereg_20261016.md (R3): |dfx|/fx <= 0.5 %, |dcx|, |dcy| <= 5 px,
ray difference at the primary's B4 probes <= 1 px; exit 0 within, 3 outside (a finding), 2 on bad input.
Run (repo root):  .venv-dev/bin/python -m pytest -q tests/test_pool_calib_crosscheck.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

try:
    import scipy  # noqa: F401  (pool_calib_models needs it for the fisheye inverse)
    import pool_calib_crosscheck as cc
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

W, H = 4608, 2592
K = [[2600.0, 0.0, 2304.0], [0.0, 2600.0, 1296.0], [0.0, 0.0, 1.0]]
D = [-0.05, 0.01, 0.0, 0.0, 0.0]
PROBES = [{"group": "frame", "name": n, "x": x, "y": y} for n, (x, y) in
          {"TL": (325.0, 182.0), "TR": (4270.0, 235.0), "BL": (194.0, 2507.0), "BR": (4455.0, 2529.0),
           "top": (2303.5, 0.0), "bottom": (2303.5, 2591.0), "left": (0.0, 1295.5), "right": (4607.0, 1295.5)}.items()]


def write_cam(root, name, K_, D_, size=(W, H)):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "models.json"), "w") as f:
        json.dump({"chosen": "standard", "f_ref_px": 2600.0, "models": {"standard": {"K": K_, "D": D_}},
                   "probes": PROBES + [{"group": "crop", "name": "crop_TL", "x": 1504, "y": 846}]}, f)
    with open(os.path.join(d, f"intrinsics_full_{size[0]}x{size[1]}.json"), "w") as f:
        json.dump({"image_size": list(size)}, f)
    return d


def run(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = cc.main(args)
    return rc, buf.getvalue()


@unittest.skipUnless(HAVE_DEPS, "numpy / scipy not installed")
class TestCrossCheck(unittest.TestCase):
    def test_identical_is_within(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = write_cam(t, "dng", K, D), write_cam(t, "jpg", K, D)
            rc, out = run([a, b])
            self.assertEqual(rc, 0, out)
            r = json.load(open(os.path.join(a, "crosscheck.json")))
            self.assertTrue(r["within_bar"])
            self.assertEqual(len(r["items"]["ray_at_B4_probes"]["per_probe"]), 8)     # frame probes only
            self.assertAlmostEqual(r["items"]["ray_at_B4_probes"]["value"], 0.0)

    def test_each_limit(self):
        cases = [  # (K change, D change, failing item)
            (lambda k: k[0].__setitem__(0, 2600.0 * 1.006), None, "fx"),
            (lambda k: k[0].__setitem__(2, 2304.0 + 5.5), None, "cx"),
            (lambda k: k[1].__setitem__(2, 1296.0 - 5.5), None, "cy"),
            (None, lambda d: d.__setitem__(0, -0.052), "ray_at_B4_probes"),
        ]
        for kfun, dfun, item in cases:
            with tempfile.TemporaryDirectory() as t:
                k2, d2 = [row[:] for row in K], D[:]
                if kfun:
                    kfun(k2)
                if dfun:
                    dfun(d2)
                a, b = write_cam(t, "dng", K, D), write_cam(t, "jpg", k2, d2)
                rc, out = run([a, b, "--out", os.path.join(t, "o")])
                self.assertEqual(rc, 3, (item, out))
                r = json.load(open(os.path.join(t, "o", "crosscheck.json")))
                self.assertFalse(r["items"][item]["pass"], item)
                self.assertIn("OUTSIDE the bar", out)

    def test_inside_limits(self):
        with tempfile.TemporaryDirectory() as t:
            k2 = [row[:] for row in K]
            k2[0][0] *= 1.004
            k2[0][2] += 4.0
            a, b = write_cam(t, "dng", K, D), write_cam(t, "jpg", k2, D)
            rc, out = run([a, b])
            r = json.load(open(os.path.join(a, "crosscheck.json")))
            self.assertTrue(r["items"]["fx"]["pass"] and r["items"]["cx"]["pass"], out)

    def test_bad_input_refused(self):
        with tempfile.TemporaryDirectory() as t:
            a = write_cam(t, "dng", K, D)
            b = write_cam(t, "small", K, D, size=(1280, 800))
            self.assertEqual(run([a, b])[0], 2)
            self.assertEqual(run([a, os.path.join(t, "missing")])[0], 2)


if __name__ == "__main__":
    unittest.main()
