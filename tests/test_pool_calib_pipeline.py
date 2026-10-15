#!/usr/bin/env python3
# filename: test_pool_calib_pipeline.py
# description: tools/pool_calib_pipeline.py (+ _models, _figures) on synthetic ChArUco renders through a camera with KNOWN intrinsics AND distortion.
"""
Synthetic gate for the underwater calibration pipeline. A pinhole render of the calib.io board is
pushed through a KNOWN distortion (standard model, barrel) into 1536x864 frames, so every claim
the pipeline makes can be checked against the truth:

  1. the numpy distortion models match cv2.projectPoints / cv2.fisheye.projectPoints, the inverse
     round-trips, and a folding model is reported as non-invertible (NaN), not as a wrong ray;
  2. FULL coverage (board pushed into all corners and edges): every full-frame bar passes, exit 0,
     recovered K within 1 % / 6 px and the distortion within 1 px at the frame corners, crop
     intrinsics = cx, cy shifted by the crop offset; the run folder has every promised artifact;
  3. CENTRE-ONLY coverage (the 10/14 failure mode): B3 + B4 FAIL, exit 3, the recipe names the
     empty corner cells; the per-frame screen rejects a blank, a clipped and a blurred frame
     and drops held-still duplicates;
  4. amendment A1: the literal (unaligned) ray spread is non-zero at the principal point, where the
     distortion is zero by definition (it measures cx/cy), and the aligned one is not;
  5. motion-blur anisotropy rises under a directional blur and stays ~1 without it;
  6. coverage maths, outlier rule, model choice tie-break, refusals.

Needs opencv-contrib-python-headless >= 4.7, scipy, matplotlib (the repo's main .venv); skipped
otherwise. Run (repo root):  .venv/bin/python -m pytest -q tests/test_pool_calib_pipeline.py
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

import importlib.util

try:
    import cv2
    import numpy as np
    HAVE_DEPS = (hasattr(cv2, "aruco") and hasattr(cv2.aruco, "CharucoDetector")
                 and all(importlib.util.find_spec(m) for m in ("scipy", "matplotlib")))
except ImportError:
    HAVE_DEPS = False

if HAVE_DEPS:
    import pool_calib_models as pm
    import pool_calib_pipeline as pp

W, H = 1536, 864
K_TRUE = np.array([[880.0, 0.0, 772.0], [0.0, 880.0, 428.0], [0.0, 0.0, 1.0]])
D_TRUE = np.array([-0.12, 0.06, 0.0, 0.0, -0.01])          # standard model, barrel, monotonic over the frame
CROP = (512, 288, 512, 288)                                 # stands in for [1504, 846, 1600, 900] at 1/3 scale
COMMON = ["--native-size", f"{W}x{H}", "--crop", ",".join(map(str, CROP)), "--folds", "3", "--bootstrap", "12",
          "--workers", "1", "--thumb-width", "240"]
SQ, MK = 40.0, 30.0


def rot(ax, ay, az):
    rx, ry, rz = (math.radians(v) for v in (ax, ay, az))
    Rx = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Ry = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    Rz = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


_MAPS = {}


def distortion_maps(K, D, model="standard"):
    """remap() maps: output (distorted) pixel -> pixel of an enlarged pinhole canvas."""
    key = (model, tuple(np.ravel(K)), tuple(np.ravel(D)))
    if key not in _MAPS:
        xs, ys = np.meshgrid(np.arange(W, dtype=float), np.arange(H, dtype=float))
        r = pm.rays(model, K, D, np.stack([xs.ravel(), ys.ravel()], 1))
        assert np.all(np.isfinite(r)), "test distortion must be invertible over the frame"
        p = pm.pinhole_px(K, r)
        mx = int(math.ceil(max(0.0, -p[:, 0].min(), p[:, 0].max() - (W - 1)))) + 4
        my = int(math.ceil(max(0.0, -p[:, 1].min(), p[:, 1].max() - (H - 1)))) + 4
        map_x = (p[:, 0] + mx).reshape(H, W).astype(np.float32)
        map_y = (p[:, 1] + my).reshape(H, W).astype(np.float32)
        _MAPS[key] = (map_x, map_y, mx, my)
    return _MAPS[key]


def render(pose, seed=0, ppm=4.0, blur=0.8, noise=2.0, K=K_TRUE, D=D_TRUE):
    """Board 14x9 modern (as the 10/14 board detected), centre on the ray through the requested
    DISTORTED pixel at `dist` mm, rotated (ax, ay, az)."""
    ax, ay, az, fxf, fyf, dist = pose
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    board = cv2.aruco.CharucoBoard((14, 9), SQ, MK, d)
    bw, bh = int(14 * SQ * ppm), int(9 * SQ * ppm)
    img = board.generateImage((bw, bh), marginSize=0, borderBits=1)
    pad = int(SQ * ppm * 0.6)
    card = np.full((bh + 2 * pad, bw + 2 * pad), 235, np.uint8)
    card[pad:pad + bh, pad:pad + bw] = np.where(img > 127, 235, 25).astype(np.uint8)
    R = rot(ax, ay, az)
    centre = np.array([14 * SQ / 2, 9 * SQ / 2, 0.0])
    ray = pm.rays("standard", K, D, np.array([[fxf * (W - 1), fyf * (H - 1)]]))[0]
    v = np.array([ray[0], ray[1], 1.0])
    t = v / np.linalg.norm(v) * dist - R @ centre
    map_x, map_y, mx, my = distortion_maps(K, D)
    Kc = K.copy()
    Kc[0, 2] += mx
    Kc[1, 2] += my
    S = np.diag([1 / ppm, 1 / ppm, 1.0])
    T = np.array([[1, 0, -pad], [0, 1, -pad], [0, 0, 1.0]])
    Hm = Kc @ np.column_stack([R[:, 0], R[:, 1], t]) @ S @ T
    canvas = cv2.warpPerspective(card, Hm, (W + 2 * mx, H + 2 * my), flags=cv2.INTER_AREA, borderValue=90)
    frame = cv2.remap(canvas, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=90)
    if blur:
        frame = cv2.GaussianBlur(frame, (0, 0), blur)
    rng = np.random.default_rng(seed)
    frame = np.clip(frame.astype(np.int16) + rng.normal(0, noise, frame.shape).astype(np.int16), 0, 255)
    return frame.astype(np.uint8)


# board pushed into the corners and edges (dist 750-900 mm: pattern ~40-45 % of the width) + centre poses
FULL_POSES = [
    (0, 0, 0, .20, .22, 800), (15, -15, 0, .80, .22, 800), (-15, 15, 0, .20, .78, 800), (0, 0, 0, .80, .78, 800),
    (20, 0, 0, .50, .20, 850), (-20, 0, 0, .50, .80, 850), (0, 25, 0, .18, .50, 850), (0, -25, 0, .82, .50, 850),
    (0, 0, 0, .50, .50, 900), (25, 20, 5, .30, .35, 900), (-25, -20, -5, .70, .65, 900), (10, -30, 8, .70, .35, 900),
    (-10, 30, -8, .30, .65, 900), (30, 0, 0, .22, .25, 820), (-30, 0, 0, .78, .75, 820), (0, 30, 0, .78, .25, 820),
    (0, -30, 0, .22, .75, 820), (15, 15, 30, .50, .50, 1200), (-15, -15, -30, .50, .50, 1200), (0, 0, 0, .5, .5, 1500),
]
# what 10/14 looked like: board ~1.2-1.6 m, always near the middle
CENTRE_POSES = [
    (0, 0, 0, .50, .50, 1500), (20, 0, 0, .45, .45, 1400), (-20, 0, 0, .55, .55, 1400), (0, 25, 0, .45, .55, 1500),
    (0, -25, 0, .55, .45, 1500), (15, 15, 5, .50, .45, 1300), (-15, -15, -5, .50, .55, 1300), (25, -20, 0, .42, .50, 1600),
    (-25, 20, 0, .58, .50, 1600), (10, 30, 10, .50, .50, 1450), (-10, -30, -10, .48, .52, 1450), (30, 10, 0, .52, .48, 1350),
    (-30, -10, 0, .47, .47, 1350), (5, 20, -15, .53, .53, 1550), (-5, -20, 15, .46, .54, 1550), (20, 20, 20, .5, .5, 1250),
]


def write_set(folder, poses, seed0=0):
    os.makedirs(folder, exist_ok=True)
    for i, pose in enumerate(poses, 1):
        cv2.imwrite(os.path.join(folder, f"cal_{i:04d}.jpg"), render(pose, seed=seed0 + i), [cv2.IMWRITE_JPEG_QUALITY, 95])


def run_pipeline(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = pp.main(args)
    return rc, buf.getvalue()


def only_run_dir(root):
    runs = [os.path.join(root, d) for d in os.listdir(root)]
    assert len(runs) == 1, runs
    return runs[0]


# ============================================================================ models

@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestModels(unittest.TestCase):
    def test_forward_matches_opencv_and_inverse_round_trips(self):
        rng = np.random.default_rng(1)
        K = np.array([[2600.0, 0, 2300], [0, 2605, 1290], [0, 0, 1]])
        obj = np.c_[rng.uniform(-800, 800, (300, 2)), np.zeros(300)]
        rv, tv = np.array([0.2, -0.3, 0.1]), np.array([50, -30, 1500.0])
        R, _ = cv2.Rodrigues(rv)
        P = obj @ R.T + tv
        truth = P[:, :2] / P[:, 2:]
        cases = {"standard": [-0.12, 0.05, 0.001, -0.0005, -0.01],
                 "rational": [-0.1, 0.04, 0.001, -0.0005, -0.01, 0.05, -0.01, 0.002],
                 "fisheye": [0.05, -0.02, 0.01, -0.003]}
        for model, D in cases.items():
            mine = pm.project(model, K, D, obj, rv, tv)
            if model == "fisheye":
                ref, _ = cv2.fisheye.projectPoints(obj.reshape(-1, 1, 3), rv.reshape(3, 1), tv.reshape(3, 1), K,
                                                   np.array(D, float).reshape(4, 1))
            else:
                ref, _ = cv2.projectPoints(obj, rv, tv, K, np.array(D, float))
            self.assertLess(np.abs(mine - ref.reshape(-1, 2)).max(), 1e-6, model)
            r = pm.rays(model, K, D, mine)
            self.assertLess(np.abs(r - truth).max(), 1e-9, model)

    def test_folding_model_is_not_invertible_at_the_edge(self):
        """The 10/14 bmcam003 fit (k1 +0.28, k2 +0.63, k3 -1.12) folds before the left/right edge:
        the inverse must say NaN there, not return a wrong ray."""
        K = np.array([[2617.0, 0, 2315], [0, 2637, 1176], [0, 0, 1]])
        D = [0.2751, 0.6273, -0.0401, 0.0019, -1.1158]
        r = pm.rays("standard", K, D, np.array([[2315.0, 1176.0], [2800, 1300], [0, 1296], [4607, 1296]]))
        self.assertTrue(np.all(np.isfinite(r[:2])))
        self.assertTrue(np.all(np.isnan(r[2:])))
        h, v = pm.fov_deg("standard", K, D, (4608, 2592))
        self.assertIsNone(h)

    def test_outlier_rule_and_model_choice(self):
        self.assertAlmostEqual(pm.outlier_threshold([0.3] * 10), 0.9)          # 3 x median when MAD = 0
        cv = {"standard": {"held_out_rms": 0.360}, "rational": {"held_out_rms": 0.355},
              "fisheye": {"held_out_rms": 0.62}}
        self.assertEqual(pm.choose_model(cv)[0], "standard")                    # within 2 %: preference wins
        cv["rational"]["held_out_rms"] = 0.30
        self.assertEqual(pm.choose_model(cv)[0], "rational")                    # clearly better: lowest wins
        self.assertEqual(pm.choose_model({"standard": {"held_out_rms": float("nan")}})[0], None)

    def test_line_straightness(self):
        x = np.linspace(0, 100, 11)
        self.assertLess(pm.line_straightness(np.c_[x, 2 * x + 1])[1], 1e-9)
        bent = np.c_[x, 0.01 * (x - 50) ** 2]
        self.assertGreater(pm.line_straightness(bent)[1], 5)


# ============================================================================ screen / coverage units

@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestScreenUnits(unittest.TestCase):
    def test_coverage_centre_only_vs_full(self):
        rng = np.random.default_rng(0)
        centre = np.c_[rng.uniform(0.35 * W, 0.65 * W, 3000), rng.uniform(0.35 * H, 0.65 * H, 3000)]
        cov = pp.coverage_stats(centre, (W, H), (8, 5), 5, CROP)
        self.assertFalse(cov["all_corner_cells"])
        self.assertLessEqual(cov["covered_frac"], 0.3)                         # 4 x 3 of 8 x 5 cells
        self.assertAlmostEqual(cov["edge_gap_px"]["left"], centre[:, 0].min())
        self.assertLess(cov["radial_norm"]["max"], 0.4)
        full = np.c_[rng.uniform(0, W - 1, 5000), rng.uniform(0, H - 1, 5000)]
        cov = pp.coverage_stats(full, (W, H), (8, 5), 5, CROP)
        self.assertTrue(cov["all_corner_cells"])
        self.assertEqual(cov["covered_frac"], 1.0)

    def test_classify_thresholds(self):
        base = {"corners": 80, "markers": 50, "lap_norm": 0.10, "clip_hi": 0.0, "board_mean": 120, "aniso": 1.05}
        frames = [dict(base, stem=f"f{i}") for i in range(6)]
        frames[1].update(clip_hi=0.30)                    # white squares saturated
        frames[2].update(lap_norm=0.02)                   # 0.2 x median -> blurred
        frames[3].update(aniso=3.0)                       # directional blur
        frames[4].update(corners=5, markers=9)            # board mostly out of frame
        frames[5].update(error="decode failed", corners=0)
        pp.classify_frames(frames, pp.PREREG["screen"])
        st = [f["status"] for f in frames]
        self.assertEqual(st, ["PASS", "FAIL", "FAIL", "FAIL", "FAIL", "FAIL"])
        self.assertIn("clipped", frames[1]["reasons"][0])
        self.assertIn("blurred", frames[2]["reasons"][0])
        self.assertIn("motion", frames[3]["reasons"][0])

    def test_motion_anisotropy(self):
        pose = (0, 0, 0, .5, .5, 900)
        sharp = render(pose, seed=1)
        k = np.full((1, 5), 1 / 5.0, np.float32)
        moved = cv2.filter2D(sharp, -1, k)                # 5 px horizontal motion (more kills detection at this scale)
        spec = {"squares_x": 14, "squares_y": 9, "square_mm": SQ, "marker_mm": MK, "dictionary": "DICT_5X5_100",
                "legacy": False}
        det = cv2.aruco.CharucoDetector(pp.build_board(spec))
        out = []
        for img in (sharp, moved):
            cc, ci, _, _ = det.detectBoard(img)
            m = pp.board_metrics(img, cc.reshape(-1, 2).astype(float), ci.ravel(), 13)
            out.append(m["aniso"])
        self.assertLess(out[0], 1.3)
        self.assertGreater(out[1], pp.PREREG["screen"]["aniso_warn"])


# ============================================================================ end to end

@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestFullCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        unit = os.path.join(cls.tmp.name, "camA_run")
        cls.cal = os.path.join(unit, "calibration")
        write_set(cls.cal, FULL_POSES)
        os.makedirs(os.path.join(unit, "loop"))
        cv2.imwrite(os.path.join(unit, "loop", "x_loop_01_still.jpg"),
                    cv2.cvtColor(render((10, -10, 0, .5, .5, 1000), seed=77), cv2.COLOR_GRAY2BGR))
        cls.root = os.path.join(cls.tmp.name, "runs")
        cls.rc, cls.out = run_pipeline([f"camA={cls.cal}", "--out-root", cls.root, "--tag", "full"] + COMMON)
        cls.run_dir = only_run_dir(cls.root)
        with open(os.path.join(cls.run_dir, "summary.json")) as f:
            cls.cam = json.load(f)["cameras"][0]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_full_frame_bars_pass(self):
        self.assertEqual(self.rc, 0, self.out[-3000:])
        bars = {b["bar"].split()[0]: b for b in self.cam["bars"]}
        for k in ("B1", "B2", "B3", "B4", "B5", "B5c"):
            self.assertTrue(bars[k]["pass"], (k, bars[k]))
        self.assertEqual(bars["B6"]["status"], "WARN")       # 880 px is not an IMX708 focal: sanity WARN only
        self.assertEqual(self.cam["verdict_full_frame"], "PASS")
        self.assertEqual(self.cam["verdict_production_crop"], "PASS")

    def test_recovers_truth(self):
        k = self.cam["intrinsics_full"]
        self.assertAlmostEqual(k["fx"], K_TRUE[0, 0], delta=0.01 * K_TRUE[0, 0])
        self.assertAlmostEqual(k["fy"], K_TRUE[1, 1], delta=0.01 * K_TRUE[1, 1])
        self.assertAlmostEqual(k["cx"], K_TRUE[0, 2], delta=6)
        self.assertAlmostEqual(k["cy"], K_TRUE[1, 2], delta=6)
        # the distortion SHAPE (A1: compared after a homography, which absorbs the cx/cy <-> rotation
        # trade-off) matches the truth to < 1 px at the frame corners and edge midpoints
        Kf = np.array(k["camera_matrix"])
        grid = np.stack(np.meshgrid(np.linspace(0, W - 1, 24), np.linspace(0, H - 1, 14)), -1).reshape(-1, 2)
        probes = np.array([[0, 0], [W - 1, 0], [0, H - 1], [W - 1, H - 1], [W / 2, 0], [0, H / 2]], float)
        d = pm._aligned_dist(pm.rays(k["model"], Kf, k["dist_coeffs"], grid), pm.rays("standard", K_TRUE, D_TRUE, grid),
                             pm.rays(k["model"], Kf, k["dist_coeffs"], probes), pm.rays("standard", K_TRUE, D_TRUE, probes),
                             K_TRUE[0, 0])
        self.assertLess(d.max(), 1.0)

    def test_crop_intrinsics(self):
        k, c = self.cam["intrinsics_full"], self.cam["intrinsics_crop"]
        self.assertEqual(c["image_size"], [CROP[2], CROP[3]])
        self.assertAlmostEqual(c["cx"], k["cx"] - CROP[0], places=6)
        self.assertAlmostEqual(c["cy"], k["cy"] - CROP[1], places=6)
        self.assertEqual(c["fx"], k["fx"])
        self.assertEqual(c["dist_coeffs"], k["dist_coeffs"])

    def test_run_folder_artifacts(self):
        for rel in ("run_manifest.json", "pipeline.log", "prereg_bars.json", "summary.json", "summary.md",
                    "camA/frames.csv", "camA/screen_sheet.jpg", "camA/coverage.png", "camA/coverage.json",
                    "camA/models.json", "camA/models.png", "camA/per_view_errors.csv", "camA/bootstrap.csv",
                    "camA/bootstrap.png", "camA/uncertainty.png", "camA/verdict.png", "camA/before_after.json",
                    f"camA/intrinsics_full_{W}x{H}.json", f"camA/intrinsics_crop_{CROP[2]}x{CROP[3]}.json"):
            p = os.path.join(self.run_dir, rel)
            self.assertTrue(os.path.isfile(p) and os.path.getsize(p) > 0, rel)
        with open(os.path.join(self.run_dir, "run_manifest.json")) as f:
            man = json.load(f)
        self.assertEqual(man["missing_outputs"], [])
        self.assertEqual(man["inputs"]["camA"]["n_files"], len(FULL_POSES))
        self.assertTrue(man["inputs"]["camA"]["card"].endswith("x_loop_01_still.jpg"))      # auto-discovered
        ba = self.cam["before_after"]
        self.assertIn("full_side_by_side", ba["calibration_frame"]["files"])
        self.assertIn("crop_side_by_side_1to1", ba["card_frame"]["files"])
        st = ba["calibration_frame"]["straightness"]
        self.assertLess(st["after_rms_px"]["median"], st["before_rms_px"]["median"])        # undistortion straightens
        with open(os.path.join(self.run_dir, "camA", "frames.csv")) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(FULL_POSES))

    def test_all_three_models_scored_held_out(self):
        self.assertEqual(set(self.cam["heldout_rms_px"]), {"standard", "rational", "fisheye"})
        self.assertLess(self.cam["heldout_rms_px"][self.cam["chosen_model"]], 1.0)


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestCentreOnly(unittest.TestCase):
    """The 10/14 failure mode: board always near the middle."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cal = os.path.join(cls.tmp.name, "camB", "calibration")
        write_set(cls.cal, CENTRE_POSES, seed0=200)
        for j in range(3):            # held still: same pose 3 more times
            cv2.imwrite(os.path.join(cls.cal, f"cal_9{j:03d}.jpg"), render(CENTRE_POSES[0], seed=300 + j))
        cv2.imwrite(os.path.join(cls.cal, "cal_9900.jpg"), np.full((H, W), 90, np.uint8))           # no board
        cv2.imwrite(os.path.join(cls.cal, "cal_9901.jpg"),                                           # clipped
                    np.clip(render(CENTRE_POSES[3], seed=401).astype(np.float32) * 1.6, 0, 255).astype(np.uint8))
        cv2.imwrite(os.path.join(cls.cal, "cal_9902.jpg"),                     # blurred, still detected
                    cv2.GaussianBlur(render((0, 0, 0, .5, .5, 700), seed=402), (0, 0), 2.0))
        cls.root = os.path.join(cls.tmp.name, "runs")
        cls.rc, cls.out = run_pipeline([f"camB={cls.cal}", "--out-root", cls.root, "--tag", "centre"] + COMMON)
        cls.run_dir = only_run_dir(cls.root)
        with open(os.path.join(cls.run_dir, "summary.json")) as f:
            cls.cam = json.load(f)["cameras"][0]
        with open(os.path.join(cls.run_dir, "camB", "frames.csv")) as f:
            cls.rows = {r["stem"]: r for r in csv.DictReader(f)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_fails_coverage_and_edges_with_exit_3(self):
        self.assertEqual(self.rc, 3, self.out[-3000:])
        bars = {b["bar"].split()[0]: b for b in self.cam["bars"]}
        self.assertTrue(bars["B1"]["pass"])
        self.assertTrue(bars["B2"]["pass"])
        self.assertFalse(bars["B3"]["pass"])
        self.assertFalse(bars["B4"]["pass"])
        self.assertEqual(self.cam["verdict_full_frame"], "FAIL")

    def test_recipe_names_the_gap(self):
        text = " ".join(self.cam["recipe"]["lines"])
        self.assertIn("Empty corner cells", text)
        self.assertIn("FULL-FRAME preview", text)
        self.assertIn("B3 coverage", self.cam["recipe"]["failed_bars"])

    def test_screen_rejects_bad_frames_and_duplicates(self):
        self.assertEqual(self.rows["cal_9900"]["status"], "FAIL")
        self.assertIn("no board", self.rows["cal_9900"]["reasons"])
        self.assertEqual(self.rows["cal_9901"]["status"], "FAIL")
        self.assertIn("clipped", self.rows["cal_9901"]["reasons"])
        self.assertEqual(self.rows["cal_9902"]["status"], "FAIL")
        self.assertIn("blurred", self.rows["cal_9902"]["reasons"])
        dups = [s for s, r in self.rows.items() if r["dup_of"]]
        self.assertGreaterEqual(len(dups), 3)                   # cal_0001 + its 3 repeats -> one kept
        self.assertTrue(all(self.rows[s]["in_set"] == "False" for s in dups))


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestAmendmentA1(unittest.TestCase):
    def test_literal_ray_metric_measures_the_principal_point(self):
        """Amendment A1: at the principal point the distortion is zero by definition, yet the literal
        (unaligned) bootstrap spread there is about the cx/cy spread. The aligned spread is ~0."""
        board_obj = np.array([[x * SQ, y * SQ, 0] for y in range(1, 9) for x in range(1, 14)], float)
        rng = np.random.default_rng(5)
        obj, img = [], []
        for pose in CENTRE_POSES:
            ax, ay, az, fxf, fyf, dist = pose
            R = rot(ax, ay, az)
            ray = pm.rays("standard", K_TRUE, D_TRUE, np.array([[fxf * (W - 1), fyf * (H - 1)]]))[0]
            v = np.array([ray[0], ray[1], 1.0])
            t = v / np.linalg.norm(v) * dist - R @ np.array([7 * SQ, 4.5 * SQ, 0])
            rv, _ = cv2.Rodrigues(R)
            p = pm.project("standard", K_TRUE, D_TRUE, board_obj, rv, t) + rng.normal(0, 0.15, (len(board_obj), 2))
            obj.append(board_obj)
            img.append(p)
        fit = pm.fit("standard", obj, img, (W, H))
        boots = [b for b in pm.bootstrap("standard", obj, img, (W, H), B=30, seed=1) if "error" not in b]
        Ks, Ds = [b["K"] for b in boots], [b["D"] for b in boots]
        f_ref = float(fit["K"][0, 0])
        pp_px = np.array([[fit["K"][0, 2], fit["K"][1, 2]]])
        grid = np.stack(np.meshgrid(np.linspace(0, W - 1, 32), np.linspace(0, H - 1, 18)), -1).reshape(-1, 2)
        region = pp.observed_region_points(np.concatenate(img), grid)
        literal = pm.ray_spread_px("standard", Ks, Ds, pp_px, f_ref)[0]
        aligned = pm.aligned_ray_spread_px("standard", Ks, Ds, fit["K"], fit["D"], region, pp_px, f_ref)[0]
        c_sd = max(np.std([K[0, 2] for K in Ks]), np.std([K[1, 2] for K in Ks]))
        self.assertGreater(literal, 0.5 * c_sd)          # tracks the principal-point spread
        self.assertLess(aligned, 0.1 * literal)          # the distortion there is known


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestRefusals(unittest.TestCase):
    def test_refusals(self):
        with tempfile.TemporaryDirectory() as t:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(pp.main([f"x={t}", "--out-root", t]), 2)                    # no images
                self.assertEqual(pp.main(["noequals", "--out-root", t]), 2)                  # NAME=PATH
                self.assertEqual(pp.main([f"x={t}", "--crop", "1,2,3", "--out-root", t]), 2)
            self.assertEqual(os.listdir(t), [])


if __name__ == "__main__":
    unittest.main()
