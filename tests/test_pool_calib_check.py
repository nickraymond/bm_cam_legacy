#!/usr/bin/env python3
# filename: test_pool_calib_check.py
# description: Pool test 2026-10-13 (N4) — tools/pool_calib_check.py on synthetic ChArUco renders with a KNOWN camera matrix.
"""
tools/pool_calib_check.py gate on synthetic renders (cv2 draws the board; a pinhole camera with a
KNOWN K, no distortion, warps it into native 4608x2592 frames at known poses, + blur + noise):

  1. 18 distinct poses -> PASS; recovered fx, fy, cx, cy within 1 % / 15 px of truth; RMS < 1 px;
     crop intrinsics = cx - 1504, cy - 846, same fx/fy; report, CSV and contact sheet written;
  2. duplicates (the same pose captured 3 times) count once as distinct;
  3. blank / board-less frames are rejected, and too few poses -> FAIL (exit 1) + stop-rule note;
  4. missing board spec -> REFUSED (exit 2), nothing written;
  5. crop_intrinsics() pure math, incl. the optional resample and the non-native skip.

Needs opencv-contrib-python-headless (>= 4.7) in the venv; skipped otherwise.
Run (repo root):  python3 -m pytest -q tests/test_pool_calib_check.py
"""
import contextlib
import csv
import io
import json
import math
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

try:
    import cv2
    import numpy as np
    HAVE_CV2 = hasattr(cv2, "aruco") and hasattr(cv2.aruco, "CharucoDetector")
except ImportError:
    HAVE_CV2 = False

import pool_calib_check as pcc  # noqa: E402

W, H = 4608, 2592
K_TRUE = [[3100.0, 0.0, 2290.0], [0.0, 3100.0, 1310.0], [0.0, 0.0, 1.0]]
# Nick's pool board (calib.io CCT600400C): 9 x 14 squares, 40 / 30 mm, DICT_5X5 -> the tool's defaults
BOARD = dict(sx=9, sy=14, sq=40.0, mk=30.0, dict_name="DICT_5X5_100")
BOARD_ARGS = []


def rot(ax, ay, az):
    """Rotation matrix from degrees about x, y, z."""
    rx, ry, rz = (math.radians(v) for v in (ax, ay, az))
    Rx = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Ry = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    Rz = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def render(pose, ppm=8.0, seed=0, legacy=False):
    """One synthetic native frame: the board (top-left origin, mm) seen at pose (ax, ay, az, cx_frac,
    cy_frac, dist_mm) through K_TRUE. cx/cy_frac place the board centre in the image."""
    ax, ay, az, fx_, fy_, dist = pose
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, BOARD["dict_name"]))
    board = cv2.aruco.CharucoBoard((BOARD["sx"], BOARD["sy"]), BOARD["sq"], BOARD["mk"], d)
    board.setLegacyPattern(legacy)
    bw, bh = int(BOARD["sx"] * BOARD["sq"] * ppm), int(BOARD["sy"] * BOARD["sq"] * ppm)
    img = board.generateImage((bw, bh), marginSize=0, borderBits=1)
    K = np.array(K_TRUE)
    R = rot(ax, ay, az)
    # board centre in mm -> put it on the ray through the requested image point at `dist`
    centre = np.array([BOARD["sx"] * BOARD["sq"] / 2, BOARD["sy"] * BOARD["sq"] / 2, 0.0])
    ray = np.linalg.inv(K) @ np.array([fx_ * W, fy_ * H, 1.0])
    t = ray / np.linalg.norm(ray) * dist - R @ centre
    S = np.diag([1 / ppm, 1 / ppm, 1.0])                      # board pixels -> board mm
    Hm = K @ np.column_stack([R[:, 0], R[:, 1], t]) @ S
    # white card around the board (the printed margin), grey background
    pad = int(BOARD["sq"] * ppm * 0.6)
    card = np.full((bh + 2 * pad, bw + 2 * pad), 255, np.uint8)
    card[pad:pad + bh, pad:pad + bw] = img
    T = np.array([[1, 0, -pad], [0, 1, -pad], [0, 0, 1.0]])
    frame = cv2.warpPerspective(card, Hm @ T, (W, H), flags=cv2.INTER_LINEAR, borderValue=90)
    frame = cv2.GaussianBlur(frame, (0, 0), 1.0)
    rng = np.random.default_rng(seed)
    frame = np.clip(frame.astype(np.int16) + rng.normal(0, 3, frame.shape).astype(np.int16), 0, 255).astype(np.uint8)
    return frame


POSES = [  # (ax, ay, az, centre x frac, centre y frac, distance mm); az 90 = board landscape in frame
    (0, 0, 0, .5, .5, 1500), (25, 0, 0, .35, .4, 1400), (-25, 0, 0, .65, .6, 1400),
    (0, 30, 0, .35, .6, 1500), (0, -30, 0, .65, .4, 1500), (20, 20, 10, .3, .5, 1450),
    (-20, -20, -10, .7, .5, 1450), (15, -25, 5, .5, .4, 1500), (-15, 25, -5, .5, .6, 1500),
    (30, 10, 90, .3, .35, 1600), (-30, -10, 90, .7, .65, 1600), (10, 35, 15, .4, .55, 1550),
    (-10, -35, -15, .6, .45, 1550), (0, 0, 90, .5, .5, 1400), (35, 0, -20, .4, .45, 1600),
    (-35, 0, 20, .6, .55, 1600), (5, 15, 45, .45, .45, 1500), (-5, -15, -45, .55, .55, 1500),
]


@unittest.skipUnless(HAVE_CV2, "opencv-contrib-python-headless >= 4.7 not installed")
class TestSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cal = os.path.join(cls.tmp.name, "bmcam003", "calibration")
        os.makedirs(cls.cal)
        for i, pose in enumerate(POSES, 1):
            cv2.imwrite(os.path.join(cls.cal, f"cal_{i:04d}.jpg"), render(pose, seed=i),
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
        # the same pose 3 more times (board held still) -> not distinct
        for j in range(3):
            cv2.imwrite(os.path.join(cls.cal, f"cal_{len(POSES) + 1 + j:04d}.jpg"), render(POSES[0], seed=100 + j),
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
        # a frame with no board (Nick walking in)
        cv2.imwrite(os.path.join(cls.cal, "cal_9999.jpg"), np.full((H, W), 90, np.uint8))
        cls.out = os.path.join(cls.tmp.name, "check")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cls.rc = pcc.main([cls.cal, "--out", cls.out, "--source", "jpeg"] + BOARD_ARGS)
        cls.stdout = buf.getvalue()
        with open(os.path.join(cls.out, "calib_report.json")) as f:
            cls.rep = json.load(f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_pass_and_recovered_intrinsics(self):
        self.assertEqual(self.rc, 0, self.stdout)
        self.assertEqual(self.rep["verdict"], "PASS", self.rep["reasons"])
        c = self.rep["calibration"]
        self.assertEqual(c["image_size"], [W, H])
        self.assertLess(c["rms_px"], 1.0)
        self.assertAlmostEqual(c["fx"], K_TRUE[0][0], delta=0.01 * K_TRUE[0][0])
        self.assertAlmostEqual(c["fy"], K_TRUE[1][1], delta=0.01 * K_TRUE[1][1])
        self.assertAlmostEqual(c["cx"], K_TRUE[0][2], delta=15)
        self.assertAlmostEqual(c["cy"], K_TRUE[1][2], delta=15)

    def test_crop_intrinsics_shift_only(self):
        c, k = self.rep["calibration"], self.rep["crop_intrinsics"]
        self.assertEqual(k["image_size"], [1600, 900])
        self.assertEqual(k["crop_native_xywh"], [1504, 846, 1600, 900])
        self.assertAlmostEqual(k["cx"], c["cx"] - 1504, places=6)
        self.assertAlmostEqual(k["cy"], c["cy"] - 846, places=6)
        self.assertEqual((k["fx"], k["fy"]), (c["fx"], c["fy"]))
        self.assertEqual(k["dist_coeffs_k1k2p1p2k3"], c["dist_coeffs_k1k2p1p2k3"])

    def test_duplicates_and_rejects(self):
        self.assertEqual(self.rep["frames_total"], len(POSES) + 4)
        self.assertEqual(self.rep["frames_used"], len(POSES) + 3)
        self.assertEqual(self.rep["calibration"]["distinct_poses"], len(POSES))
        blank = [f for f in self.rep["frames"] if f["stem"] == "cal_9999"][0]
        self.assertFalse(blank["used"])
        self.assertIn("corners", blank["reject"])
        dup = [f for f in self.rep["frames"] if f["stem"] == f"cal_{len(POSES) + 1:04d}"][0]
        self.assertTrue(dup["used"])
        self.assertFalse(dup["distinct"])

    def test_artifacts(self):
        for name in ("calib_report.json", "frames.csv", "contact_sheet.jpg", "calib_check.log"):
            self.assertGreater(os.path.getsize(os.path.join(self.out, name)), 100, name)
        with open(os.path.join(self.out, "frames.csv")) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(POSES) + 4)
        sheet = cv2.imread(os.path.join(self.out, "contact_sheet.jpg"))
        self.assertEqual(sheet.shape[1], 5 * pcc.THUMB_W)
        self.assertIn("IN-AIR", self.rep["check"])
        self.assertGreater(self.rep["calibration"]["coverage_union_cells"], 0.2)
        self.assertIn("OpenCV", self.stdout)

    def test_too_few_poses_fail_and_stop_rule(self):
        with tempfile.TemporaryDirectory() as d:
            cal = os.path.join(d, "calibration")
            os.makedirs(cal)
            for i, pose in enumerate(POSES[:6], 1):
                cv2.imwrite(os.path.join(cal, f"c{i}.jpg"), render(pose, seed=i))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = pcc.main([cal, "--out", os.path.join(d, "o"), "--no-sheet"] + BOARD_ARGS)
            with open(os.path.join(d, "o", "calib_report.json")) as f:
                rep = json.load(f)
        self.assertEqual(rc, 1)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertIn("distinct poses 6 < 15", rep["reasons"][0])
        self.assertIn("stop rule", rep["stop_rule"])


@unittest.skipUnless(HAVE_CV2, "opencv-contrib-python-headless >= 4.7 not installed")
class TestLayout(unittest.TestCase):
    """Even row count (14): modern vs legacy layouts differ; auto must pick the printed one."""

    def args(self, **kw):
        a = pcc.build_parser().parse_args(["x"])
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_auto_picks_legacy_print(self):
        grays = [render(POSES[i], seed=i, legacy=True) for i in (0, 3, 13)]
        best, trials, why = pcc.choose_layout(grays, self.args(), cv2)
        self.assertIsNone(why, trials)
        self.assertEqual((best["squares_x"], best["squares_y"], best["legacy"]), (9, 14, True))
        self.assertEqual(len(trials), 4)            # modern/legacy x 9x14/14x9

    def test_auto_picks_modern_print(self):
        grays = [render(POSES[i], seed=i) for i in (0, 13)]
        best, trials, why = pcc.choose_layout(grays, self.args(), cv2)
        self.assertIsNone(why, trials)
        self.assertEqual((best["squares_x"], best["squares_y"], best["legacy"]), (9, 14, False))

    def test_no_board_no_layout(self):
        best, trials, why = pcc.choose_layout([np.full((H, W), 90, np.uint8)], self.args(), cv2)
        self.assertIsNone(best)
        self.assertIn("no layout", why)

    def test_pinned_layout_is_one_candidate(self):
        self.assertEqual(pcc.layout_candidates(self.args(layout="legacy")), [(9, 14, True)])


class TestRefusalsAndMath(unittest.TestCase):
    def test_defaults_are_nicks_board(self):
        a = pcc.build_parser().parse_args(["x"])
        self.assertEqual((a.squares_x, a.squares_y, a.square_mm, a.marker_mm, a.dictionary, a.layout),
                         (9, 14, 40.0, 30.0, "DICT_5X5_100", "auto"))

    def test_bad_board_spec_refused(self):
        with tempfile.TemporaryDirectory() as d:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = pcc.main([d, "--marker-mm", "45"])
            self.assertEqual(rc, 2)
            self.assertIn("marker_mm < square_mm", err.getvalue())
            self.assertEqual(os.listdir(d), [])

    def test_crop_math(self):
        K = [[3000.0, 0, 2300.0], [0, 3000.0, 1300.0], [0, 0, 1]]
        k, why = pcc.crop_intrinsics(K, (4608, 2592), (1504, 846, 1600, 900))
        self.assertIsNone(why)
        self.assertEqual((k["fx"], k["cx"], k["cy"]), (3000.0, 796.0, 454.0))
        k2, _ = pcc.crop_intrinsics(K, (4608, 2592), (1504, 846, 1600, 900), out_width=800)
        self.assertEqual(k2["image_size"], [800, 450])
        self.assertAlmostEqual(k2["fx"], 1500.0)
        self.assertAlmostEqual(k2["cx"], (796.0 + 0.5) * 0.5 - 0.5)
        k3, why3 = pcc.crop_intrinsics(K, (2304, 1296), (1504, 846, 1600, 900))
        self.assertIsNone(k3)
        self.assertIn("not native", why3)


if __name__ == "__main__":
    unittest.main()
