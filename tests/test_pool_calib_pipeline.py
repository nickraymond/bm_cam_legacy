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
  6. coverage maths, outlier rule, model choice tie-break, refusals;
  7. frames that are NOT --native-size (the OpenMV N6 1280x800 on 10/14): B5c + the crop verdict
     are N/A (not FAIL with inf), B6 is N/A unless --nominal-fx gives one, and summary.md /
     the verdict sheet print the real frame size; the native path keeps its pre-registered text.

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
NON_NATIVE = ["--native-size", "4608x2592"] + COMMON[2:]   # frames 1536x864 != native: like the N6 run
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

    def test_model_disagreement_is_max_pairwise(self):
        """QE #176: B and C each differ from A by +d / -d in k1; the widest pair is B vs C (~2x)."""
        grid = np.stack(np.meshgrid(np.linspace(300, 1200, 12), np.linspace(200, 650, 8)), -1).reshape(-1, 2)
        probes = np.array([[0, 0], [W - 1, H - 1]], float)
        mk = lambda d: {"model": "standard", "K": K_TRUE, "D": D_TRUE + np.array([d, 0, 0, 0, 0])}  # noqa: E731
        a, b, c = mk(0.0), mk(0.02), mk(-0.02)
        pair = pm.aligned_model_disagreement_px([a, b, c], grid, probes, K_TRUE[0, 0])

        def to_a(m):
            return pm._aligned_dist(pm.rays("standard", m["K"], m["D"], grid), pm.rays("standard", a["K"], a["D"], grid),
                                    pm.rays("standard", m["K"], m["D"], probes),
                                    pm.rays("standard", a["K"], a["D"], probes), K_TRUE[0, 0])
        to_ref = np.maximum(to_a(b), to_a(c))
        self.assertTrue(np.all(pair > 1.6 * to_ref), (pair, to_ref))

    def test_failed_bootstrap_replicates_count_as_inf(self):
        pts = np.array([[100.0, 100.0]])
        Ks, Ds = [K_TRUE] * 20, [D_TRUE] * 20
        self.assertTrue(np.isfinite(pm.ray_spread_px("standard", Ks, Ds, pts, 880.0, n_failed=0)[0]))
        self.assertTrue(np.isfinite(pm.ray_spread_px("standard", Ks, Ds, pts, 880.0, n_failed=1)[0]))   # 1/21 < 5 %
        self.assertTrue(np.isinf(pm.ray_spread_px("standard", Ks, Ds, pts, 880.0, n_failed=2)[0]))      # 2/22 > 5 %

    def test_fisheye_cv_ignores_outside_start_values(self):
        """QE #176: a fold's fisheye start values come from its own training views, not from K0."""
        board_obj = np.array([[x * SQ, y * SQ, 0] for y in range(1, 9) for x in range(1, 14)], float)
        rng = np.random.default_rng(3)
        obj, img = [], []
        for ax, ay, az, fxf, fyf, dist in FULL_POSES[:12]:
            R = rot(ax, ay, az)
            ray = pm.rays("standard", K_TRUE, D_TRUE, np.array([[fxf * (W - 1), fyf * (H - 1)]]))[0]
            v = np.array([ray[0], ray[1], 1.0])
            t = v / np.linalg.norm(v) * dist - R @ np.array([7 * SQ, 4.5 * SQ, 0])
            rv, _ = cv2.Rodrigues(R)
            obj.append(board_obj)
            img.append(pm.project("standard", K_TRUE, D_TRUE, board_obj, rv, t) + rng.normal(0, 0.1, (len(board_obj), 2)))
        a = pm.cross_validate("fisheye", obj, img, (W, H), folds=3, K0=None)["held_out_rms"]
        b = pm.cross_validate("fisheye", obj, img, (W, H), folds=3, K0=K_TRUE * 1.3)["held_out_rms"]
        self.assertAlmostEqual(a, b, places=9)               # identical up to OpenCV thread scheduling

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

    def test_native_path_keeps_prereg_b6_and_wording(self):
        """--native-size frames (the IMX708 path) keep the pre-registered B6 and summary wording."""
        b6 = {b["bar"].split()[0]: b for b in self.cam["bars"]}["B6"]
        self.assertEqual(b6["rule"], "fx / 1964 px (in-air nominal) in [1.25, 1.42]")
        self.assertAlmostEqual(b6["value"], self.cam["intrinsics_full"]["fx"] / pp.NOMINAL_FX_AIR, places=9)
        with open(os.path.join(self.run_dir, "summary.md")) as f:
            md = f.read()
        self.assertIn("- Underwater fx / in-air nominal (1964 px, datasheet assumption) = ", md)
        self.assertIn(f"Pixels = native {W}×{H} sensor px unless labelled.", md)
        self.assertIsNone(self.cam["prereg_note"])
        self.assertNotIn("not pre-registered", md)
        text = " ".join(self.cam["recipe"]["lines"])                         # bmcam recipe lines kept
        self.assertIn("the pool preview showed only the 1600x900 centre crop", text)
        self.assertIn("Keep the production lens position (0.8)", text)


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

    def test_negative_control_corner_spread(self):
        """QE #176: with centre-only data the aligned (A1) ray uncertainty at every frame corner is
        > 2 px (or unknown). A1 must not make an unconstrained corner look constrained."""
        corners = [p for p in self.cam["probes"] if p["group"] == "frame" and p["name"] in ("TL", "TR", "BL", "BR")]
        self.assertEqual(len(corners), 4)
        for p in corners:
            v = p["ray_uncertainty_px"]
            self.assertTrue(v is None or v > 2.0, p)          # None = inf in the JSON

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
class TestNonNativeSize(unittest.TestCase):
    """10/14 N6 run (1280x800 frames, default --native-size 4608x2592): no crop is derived, so B5c
    and the crop verdict must be N/A (not FAIL with inf); the IMX708 in-air nominal does not apply,
    so B6 is N/A unless --nominal-fx gives one. Same frames under two names: camN has no nominal,
    camF gets --nominal-fx camF=660 (truth 880 px -> ratio ~1.33)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cal = os.path.join(cls.tmp.name, "n6", "calibration")
        write_set(cal, FULL_POSES)
        cls.root = os.path.join(cls.tmp.name, "runs")
        cls.rc, cls.out = run_pipeline([f"camN={cal}", f"camF={cal}", "--nominal-fx", "camF=660",
                                        "--out-root", cls.root, "--tag", "nonnative"] + NON_NATIVE)
        cls.run_dir = only_run_dir(cls.root)
        with open(os.path.join(cls.run_dir, "summary.json")) as f:
            cls.cams = {c["camera"]: c for c in json.load(f)["cameras"]}
        with open(os.path.join(cls.run_dir, "summary.md")) as f:
            cls.md = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def bars(self, cam):
        return {b["bar"].split()[0]: b for b in self.cams[cam]["bars"]}

    def test_crop_is_na_not_fail(self):
        self.assertEqual(self.rc, 0, self.out[-3000:])               # full-frame bars still PASS
        for cam in ("camN", "camF"):
            b5c = self.bars(cam)["B5c"]
            self.assertEqual((b5c["status"], b5c["pass"]), ("N/A", None), b5c)
            self.assertIn("not native 4608x2592", b5c["value"])
            self.assertEqual(self.cams[cam]["verdict_full_frame"], "PASS")
            self.assertEqual(self.cams[cam]["verdict_production_crop"], "N/A")
            self.assertIsNone(self.cams[cam]["intrinsics_crop"])
            self.assertFalse([f for f in os.listdir(os.path.join(self.run_dir, cam)) if f.startswith("intrinsics_crop")])
        with open(os.path.join(self.run_dir, "run_manifest.json")) as f:
            man = json.load(f)
        self.assertEqual(man["missing_outputs"], [])
        self.assertEqual(man["verdicts"]["camN"]["production_crop"], "N/A")

    def test_b6_na_without_nominal(self):
        b6 = self.bars("camN")["B6"]
        self.assertEqual((b6["status"], b6["pass"]), ("N/A", None), b6)
        self.assertIn("--nominal-fx camN=PX", b6["value"])
        self.assertNotIn("1964 px (in-air nominal)", b6["rule"])
        self.assertIsNone(self.cams["camN"]["intrinsics_full"]["fx_over_nominal_in_air"])

    def test_b6_uses_nominal_fx(self):
        b6 = self.bars("camF")["B6"]
        fx = self.cams["camF"]["intrinsics_full"]["fx"]
        self.assertEqual(b6["status"], "PASS", b6)
        self.assertAlmostEqual(b6["value"], fx / 660.0, places=9)
        self.assertEqual(b6["rule"], "fx / 660 px (in-air nominal, --nominal-fx) in [1.25, 1.42]")
        self.assertEqual(self.cams["camF"]["intrinsics_full"]["nominal_fx_in_air_px"], 660.0)

    def test_summary_md_wording(self):
        self.assertNotIn("4608×2592 sensor px", self.md)
        self.assertNotIn("1964 px, datasheet assumption", self.md)
        self.assertIn("Pixels = each camera's own full-frame px", self.md)
        self.assertIn("- Underwater fx / in-air nominal: n/a (the IMX708 1964 px is for 4608x2592 frames only; "
                      "pass --nominal-fx camN=PX)", self.md)
        self.assertIn("- Underwater fx / in-air nominal (660 px, --nominal-fx) = 1.3", self.md)
        self.assertIn("production-crop intrinsics **N/A**", self.md)
        self.assertIn("| B5c production crop | N/A |", self.md)
        self.assertNotIn("| B5c production crop | FAIL |", self.md)
        # QE #178: the pixel bars were pre-registered for the IMX708 only; say so per camera
        self.assertIn("- **IMX708-pixel bars, not pre-registered for this sensor: the B1-B6 pixel numbers", self.md)
        self.assertIn("applied unchanged to 1536x864 frames", self.cams["camN"]["prereg_note"])

    def test_recipe_has_no_bmcam_lines(self):
        """QE #178 follow-up: the 1600x900 crop-only preview and LensPosition 0.8 are bmcam facts."""
        for cam in ("camN", "camF"):
            text = " ".join(self.cams[cam]["recipe"]["lines"])
            self.assertNotIn("1600x900", text)
            self.assertNotIn("lens position (0.8)", text)
            self.assertIn("Frame with a FULL-FRAME preview, so the frame edges are visible", text)
            self.assertIn("Calibrate at the focus you ship", text)
        self.assertNotIn("1600x900 centre crop", self.md)


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestNonNativeUnits(unittest.TestCase):
    """evaluate_bars / nominal_fx_for / verdict_titles without a pipeline run."""

    @staticmethod
    def fake_cal(fx=2620.0):
        groups = {"frame": ("TL", "TR", "BL", "BR", "top", "bottom", "left", "right"),
                  "crop": ("crop_TL", "crop_TR", "crop_BL", "crop_BR")}
        probes = [{"group": g, "name": n, "ray_uncertainty_px": 0.1, "model_disagreement_px": 0.1,
                   "ray_uncertainty_raw_px": 0.2, "model_disagreement_raw_px": 0.2}
                  for g, names in groups.items() for n in names]
        return {"chosen": "standard", "cv": {"standard": {"held_out_rms": 0.3}}, "probes": probes,
                "boot_stats": {"replicates_ok": 100, "replicates": 100, "fx": {"mean": fx, "std": 1.0},
                               "fy": {"mean": fx, "std": 1.0}, "cx": {"std": 2.0}, "cy": {"std": 2.0}},
                "final": {"K": np.array([[fx, 0, 2304], [0, fx, 1296], [0, 0, 1.0]])}}

    COV = {"covered_frac": 1.0, "all_corner_cells": True,
           "corner_cells_covered": {k: True for k in ("top-left", "top-right", "bottom-left", "bottom-right")}}

    def test_native_default_equals_prereg(self):
        """No kwargs and the native-size nominal give the exact pre-registered IMX708 rows."""
        bars = pp.PREREG["bars"]
        rows, v_full, v_crop = pp.evaluate_bars(20, self.fake_cal(), self.COV, bars)
        nom = pp.nominal_fx_for("bmcam003", pp.NATIVE, pp.NATIVE, {})
        self.assertEqual(nom, {"fx_px": bars["B6_nominal_fx_air_px"], "source": "datasheet assumption"})
        self.assertEqual(pp.evaluate_bars(20, self.fake_cal(), self.COV, bars, crop_skip=None, nominal=nom),
                         (rows, v_full, v_crop))
        by = {r["bar"].split()[0]: r for r in rows}
        self.assertEqual((v_full, v_crop), ("PASS", "PASS"))
        self.assertEqual(by["B5c"]["status"], "PASS")
        self.assertEqual(by["B6"]["rule"], "fx / 1964 px (in-air nominal) in [1.25, 1.42]")
        self.assertAlmostEqual(by["B6"]["value"], 2620.0 / pp.NOMINAL_FX_AIR)

    def test_crop_skip_and_no_nominal(self):
        bars = pp.PREREG["bars"]
        nom = pp.nominal_fx_for("n6", (1280, 800), pp.NATIVE, {})
        self.assertIsNone(nom["fx_px"])
        self.assertIn("--nominal-fx n6=PX", nom["source"])
        why = "frames are 1280x800, not native 4608x2592: crop derivation skipped"
        for n_poses, want_full in ((20, "PASS"), (8, "FAIL")):        # crop verdict N/A either way
            rows, v_full, v_crop = pp.evaluate_bars(n_poses, self.fake_cal(1628.0), self.COV, bars,
                                                    crop_skip=why, nominal=nom)
            by = {r["bar"].split()[0]: r for r in rows}
            self.assertEqual((v_full, v_crop), (want_full, "N/A"))
            self.assertEqual((by["B5c"]["status"], by["B5c"]["pass"], by["B5c"]["value"]), ("N/A", None, why))
            self.assertEqual((by["B6"]["status"], by["B6"]["pass"]), ("N/A", None))
        given = pp.nominal_fx_for("n6", (1280, 800), pp.NATIVE, {"n6": 1220.0})
        self.assertEqual(given, {"fx_px": 1220.0, "source": "--nominal-fx"})
        by = {r["bar"].split()[0]: r for r in pp.evaluate_bars(20, self.fake_cal(1628.0), self.COV, bars,
                                                               crop_skip=why, nominal=given)[0]}
        self.assertEqual(by["B6"]["status"], "PASS")                  # 1628 / 1220 = 1.334
        self.assertIn("1220 px (in-air nominal, --nominal-fx)", by["B6"]["rule"])

    def test_native_size_help_says_imx708(self):
        h = " ".join(pp.build_parser().format_help().split())
        self.assertIn("the IMX708 full frame WxH", h)
        self.assertIn("Leave it at the default for any other camera", h)

    def test_verdict_titles(self):
        import pool_calib_figures as figs
        t, s = figs.verdict_titles("bmcam003", "FAIL", "FAIL", (4608, 2592), (1504, 846, 1600, 900))
        self.assertEqual(t, "bmcam003 underwater calibration — full-frame intrinsics: FAIL   ·   "
                            "production 1600×900 crop: FAIL")
        self.assertEqual(s, "All axes in native 4608×2592 sensor pixels. Scaled figure, not 1:1. "
                            "Dashed box = production crop.")
        t, s = figs.verdict_titles("n6", "FAIL", "N/A", (1280, 800), None)
        self.assertTrue(t.endswith("production crop: n/a"), t)
        self.assertNotIn("1600", t)
        self.assertIn("1280×800 frame pixels", s)
        self.assertNotIn("4608", s)
        self.assertNotIn("Dashed box", s)
        self.assertIn("IMX708-pixel bars, not pre-registered for this sensor", s)


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


def write_bayer(path_no_ext, bgr_or_gray, cfa="BGGR", bits=8, extra=None):
    """Write an OpenMV-style raw: <stem>.bayer (8-bit mosaic) + <stem>.json (geometry)."""
    img = bgr_or_gray if bgr_or_gray.ndim == 3 else cv2.cvtColor(bgr_or_gray, cv2.COLOR_GRAY2BGR)
    h, w, _ = img.shape
    ch = {"R": 2, "G": 1, "B": 0}
    m = np.zeros((h, w), np.uint8)
    for dy in (0, 1):
        for dx in (0, 1):
            m[dy::2, dx::2] = img[dy::2, dx::2, ch[cfa[dy * 2 + dx]]]
    m.tofile(path_no_ext + ".bayer")
    meta = {"format": "openmv_bayer", "width": w, "height": h, "cfa": cfa, "bits": bits, "black_level": 0,
            "white_level": 255, "exposure_us": 3752, "gain_db": 0.0}
    meta.update(extra or {})
    with open(path_no_ext + ".json", "w") as f:
        json.dump(meta, f)


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestBayer(unittest.TestCase):
    """OpenMV raw input (nereus002 N6 / AE3): CFA mapping, bad files, and a full calibration."""

    def test_cfa_mapping_reproduces_colours(self):
        img = np.zeros((60, 90, 3), np.uint8)
        img[:, :30], img[:, 30:60], img[:, 60:] = (0, 0, 255), (0, 255, 0), (255, 0, 0)     # red, green, blue
        with tempfile.TemporaryDirectory() as t:
            for cfa in ("BGGR", "GBRG", "RGGB", "GRBG"):
                write_bayer(os.path.join(t, cfa), img, cfa)
                out = pp.load_bayer(os.path.join(t, cfa + ".bayer"), color=True)
                self.assertEqual(out.shape, img.shape)
                self.assertGreater(out[30, 15, 2], 200, cfa)        # red stays red
                self.assertGreater(out[30, 45, 1], 200, cfa)        # green stays green
                self.assertGreater(out[30, 75, 0], 200, cfa)        # blue stays blue
                self.assertLess(out[30, 15, 0], 60, cfa)

    def test_bad_files_fail_loudly(self):
        img = np.full((40, 64), 128, np.uint8)
        with tempfile.TemporaryDirectory() as t:
            write_bayer(os.path.join(t, "short"), img, extra={"width": 80})                 # size mismatch
            write_bayer(os.path.join(t, "cfa"), img, extra={"cfa": "XYZW"})
            write_bayer(os.path.join(t, "deep"), img, extra={"bits": 10})
            for stem, msg in (("short", "expected"), ("cfa", "unknown cfa"), ("deep", "8-bit only")):
                with self.assertRaisesRegex(ValueError, msg):
                    pp.load_bayer(os.path.join(t, stem + ".bayer"))
                spec = {"squares_x": 14, "squares_y": 9, "square_mm": SQ, "marker_mm": MK,
                        "dictionary": "DICT_5X5_100", "legacy": False}
                fr = pp.screen_frame((os.path.join(t, stem + ".bayer"), spec, 120))
                self.assertIn(msg, fr["error"])                     # becomes a FAIL reason, run continues

    def test_folder_mixing_raw_and_isp_jpeg_keeps_raw(self):
        """QE #180: a captures/openmv_n6/ folder holds openmv_n6_image_*.jpg (ISP) AND *_raw_*.bayer with
        different stems; calibrating both as one camera would mix two processing paths."""
        img = np.full((40, 64), 128, np.uint8)
        with tempfile.TemporaryDirectory() as t:
            for i in range(3):
                write_bayer(os.path.join(t, f"openmv_n6_raw_{i}"), img)
                cv2.imwrite(os.path.join(t, f"openmv_n6_image_{i}.jpg"), img)
            warn = []
            files = pp.resolve_frames(t, warn)
            self.assertEqual(len(files), 3)
            self.assertTrue(all(f.endswith(".bayer") for f in files))
            self.assertEqual(len(warn), 1)
            self.assertIn("keeping the raw only", warn[0])
            warn = []                                                 # an explicit glob is untouched
            self.assertEqual(len(pp.resolve_frames(os.path.join(t, "*.jpg"), warn)), 3)
            self.assertEqual(warn, [])

    def test_calibrates_from_bayer(self):
        with tempfile.TemporaryDirectory() as t:
            cal = os.path.join(t, "captures", "openmv_n6")
            os.makedirs(cal)
            for i, pose in enumerate(FULL_POSES, 1):
                write_bayer(os.path.join(cal, f"openmv_n6_raw_{i:04d}"), render(pose, seed=500 + i))
            root = os.path.join(t, "runs")
            rc, out = run_pipeline([f"n6={cal}/*.bayer", "--out-root", root, "--tag", "bayer"] + COMMON)
            self.assertIn(rc, (0, 3), out[-2000:])
            with open(os.path.join(only_run_dir(root), "summary.json")) as f:
                cam = json.load(f)["cameras"][0]
            self.assertEqual(cam["n_frames"], len(FULL_POSES))
            self.assertEqual(cam["image_size"], [W, H])
            self.assertGreaterEqual(cam["views_used"], 15)
            k = cam["intrinsics_full"]
            self.assertAlmostEqual(k["fx"], K_TRUE[0, 0], delta=0.01 * K_TRUE[0, 0])
            self.assertAlmostEqual(k["cx"], K_TRUE[0, 2], delta=6)
            with open(os.path.join(only_run_dir(root), "n6", "frames.csv")) as f:
                rows = list(csv.DictReader(f))
            self.assertTrue(all(r["exposure_us"] == "3752" for r in rows))       # .json sidecar read


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestRefusals(unittest.TestCase):
    def test_refusals(self):
        with tempfile.TemporaryDirectory() as t:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(pp.main([f"x={t}", "--out-root", t]), 2)                    # no images
                self.assertEqual(pp.main(["noequals", "--out-root", t]), 2)                  # NAME=PATH
                self.assertEqual(pp.main([f"x={t}", "--crop", "1,2,3", "--out-root", t]), 2)
                for bad in ("y=1000", "x=abc", "x=-5", "x=0", "x"):                         # --nominal-fx NAME=PX
                    self.assertEqual(pp.main([f"x={t}", "--nominal-fx", bad, "--out-root", t]), 2, bad)
                for crop, native in (("1504,846,1600,900", "1536x864"), ("-1,0,100,100", "4608x2592"),
                                     ("0,0,0,100", "4608x2592"), ("4000,0,700,100", "4608x2592")):
                    self.assertEqual(pp.main([f"x={t}", f"--crop={crop}", "--native-size", native, "--out-root", t]),
                                     2, (crop, native))                                  # crop outside native
            self.assertEqual(os.listdir(t), [])
            self.assertIn("--nominal-fx 'y=1000' must be NAME=PX", err.getvalue())
            self.assertIn("--crop 1504,846,1600,900 is not inside --native-size 1536x864", err.getvalue())


if __name__ == "__main__":
    unittest.main()


# ============================================================================ prereg 2026-10-16 (pool049 re-dive)

def synthetic_views(poses, K=K_TRUE, D=D_TRUE, noise=0.15, seed=0):
    """Board corners projected through the TRUE camera (no rendering): obj (N,3) mm, img (N,2) px per
    view, only the corners that land inside the frame. poses as FULL_POSES."""
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(1, 14) * SQ, np.arange(1, 9) * SQ)
    obj = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], 1)
    centre = np.array([14 * SQ / 2, 9 * SQ / 2, 0.0])
    O, I = [], []
    for ax, ay, az, fxf, fyf, dist in poses:
        R = rot(ax, ay, az)
        ray = pm.rays("standard", K, D, np.array([[fxf * (W - 1), fyf * (H - 1)]]))[0]
        v = np.array([ray[0], ray[1], 1.0])
        t = v / np.linalg.norm(v) * dist - R @ centre
        rv = cv2.Rodrigues(R)[0].ravel()
        p = pm.project("standard", K, D, obj, rv, t) + rng.normal(0, noise, (len(obj), 2))
        ok = (p[:, 0] >= 0) & (p[:, 0] <= W - 1) & (p[:, 1] >= 0) & (p[:, 1] <= H - 1)
        O.append(obj[ok])
        I.append(p[ok])
    return O, I, centre


def corner_mask(r_cut):
    """Synthetic port vignette: pixels farther than r_cut x the half-diagonal from the centre are masked."""
    ys, xs = np.mgrid[0:H, 0:W]
    r = np.hypot(xs - (W - 1) / 2, ys - (H - 1) / 2) / math.hypot(W / 2, H / 2)
    return r <= r_cut


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestPrereg20261016Units(unittest.TestCase):
    """--mask helpers, the literal model-disagreement reading, the residual report, the tilt / decentre
    flags and the cross-group check (docs/pool_calib_prereg_20261016.md)."""

    def test_mask_png_round_trip_and_visible_at(self):
        vis = corner_mask(0.85)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "m.png")
            cv2.imwrite(p, np.where(vis, 255, 0).astype(np.uint8))
            got = pp.load_mask(p)
            self.assertTrue(np.array_equal(got, vis))
            with self.assertRaises(ValueError):
                pp.load_mask(os.path.join(tmp, "missing.png"))
        pts = np.array([[0, 0], [W / 2, H / 2], [W - 1, H - 1], [-5, 10], [W + 3, 10]], float)
        self.assertEqual(pp.visible_at(vis, pts).tolist(), [False, True, False, False, False])

    def test_cell_visible_fraction_matches_corner_assignment(self):
        vis = corner_mask(0.8)
        vf = pp.cell_visible_fraction(vis, (8, 5))
        ys, xs = np.mgrid[0:H, 0:W]
        ix, iy = (xs * 8 // W), (ys * 5 // H)        # coverage_stats' cell of a point at (x, y)
        for r in range(5):
            for c in range(8):
                sel = (iy == r) & (ix == c)
                self.assertAlmostEqual(vf[r, c], vis[sel].mean(), places=12)
        self.assertLess(vf[0, 0], 0.5)              # this vignette takes the corner cells out
        self.assertEqual(vf[2, 3], 1.0)

    def test_extreme_visible_cells(self):
        el = np.ones((5, 8), bool)
        self.assertEqual(pp.extreme_visible_cells(el, (W, H)),
                         {"top-left corner": (0, 0), "top-right corner": (0, 7),
                          "bottom-left corner": (4, 0), "bottom-right corner": (4, 7)})
        el[0, 0] = el[4, 7] = False
        ext = pp.extreme_visible_cells(el, (W, H))
        # cells are 192 x 172.8 px: (1, 0) centre (96, 259) is nearer (0, 0) than (0, 1) centre (288, 86)
        self.assertEqual(ext["top-left corner"], (1, 0))
        self.assertEqual(ext["bottom-right corner"], (3, 7))
        self.assertEqual(ext["top-right corner"], (0, 7))

    def test_nearest_visible_moves_masked_probes_only(self):
        vis = corner_mask(0.9)
        pr = pp.probe_points((W, H), CROP, vis)
        plain = pp.probe_points((W, H), CROP)
        self.assertEqual(pr["crop"], plain["crop"])                     # B5c crop corners never move
        for k in ("top", "bottom", "left", "right"):                    # edge midpoints: r <= 0.87
            self.assertEqual(pr["frame"][k], tuple(map(float, plain["frame"][k])))
        for k in ("TL", "TR", "BL", "BR"):
            x, y = pr["frame"][k]
            self.assertTrue(vis[int(y), int(x)], k)
            x0, y0 = plain["frame"][k]
            ys, xs = np.nonzero(vis)
            self.assertAlmostEqual(math.hypot(x - x0, y - y0), np.sqrt(((xs - x0) ** 2 + (ys - y0) ** 2).min()))

    def test_coverage_counts_only_eligible_cells(self):
        vis = corner_mask(0.8)
        vf = pp.cell_visible_fraction(vis, (8, 5))
        cw, ch = W / 8, H / 5
        pts = np.array([[(c + 0.5) * cw, (r + 0.5) * ch] for r in range(5) for c in range(8)
                        if vf[r, c] >= 0.5 for _ in range(6)])
        plain = pp.coverage_stats(pts, (W, H), (8, 5), 5)
        masked = pp.coverage_stats(pts, (W, H), (8, 5), 5, visible=vis)
        self.assertFalse(plain["all_corner_cells"])                     # true corner cells are empty
        self.assertLess(plain["covered_frac"], 1.0)
        self.assertTrue(masked["all_corner_cells"])                     # extreme visible cells are covered
        self.assertEqual(masked["covered_frac"], 1.0)
        self.assertEqual(masked["mask"]["n_eligible"], int((vf >= 0.5).sum()))
        self.assertEqual(masked["mask"]["covered_frac_all_cells"], plain["covered_frac"])

    def test_literal_reading_sets_b4_and_b5c(self):
        cal = TestNonNativeUnits.fake_cal()
        for p in cal["probes"]:
            p["model_disagreement_raw_px"] = 1.5          # literal: over B5c's 1 px, under B4's 2 px
        bars, cov = pp.PREREG["bars"], TestNonNativeUnits.COV
        _, vf_a, vc_a = pp.evaluate_bars(20, cal, cov, bars)
        rows, vf_l, vc_l = pp.evaluate_bars(20, cal, cov, bars, models_reading="literal")
        self.assertEqual((vf_a, vc_a), ("PASS", "PASS"))
        self.assertEqual((vf_l, vc_l), ("PASS", "FAIL"))
        by = {r["bar"].split()[0]: r for r in rows}
        self.assertIn("LITERAL", by["B5c"]["rule"])
        self.assertEqual(by["B5c"]["value"]["literal_models_px"], 1.5)
        for p in cal["probes"]:
            p["model_disagreement_raw_px"] = 2.5          # now over B4's 2 px as well
        _, vf_l, _ = pp.evaluate_bars(20, cal, cov, bars, models_reading="literal")
        self.assertEqual(vf_l, "FAIL")
        with self.assertRaises(ValueError):
            pp.evaluate_bars(20, cal, cov, bars, models_reading="metric")

    def test_aligned_default_keeps_the_1014_rule_text(self):
        rows, _, _ = pp.evaluate_bars(20, TestNonNativeUnits.fake_cal(), TestNonNativeUnits.COV, pp.PREREG["bars"])
        by = {r["bar"].split()[0]: r for r in rows}
        self.assertTrue(by["B4"]["rule"].endswith("at 4 frame corners + 4 edge midpoints (homography-aligned, amendment A1)"))
        self.assertTrue(by["B5c"]["rule"].endswith("at the 4 crop corners (homography-aligned, amendment A1)"))
        self.assertTrue(by["B3"]["rule"].endswith("of 8x5 cells and all 4 corner cells"))

    def test_residual_report_zones_and_ratio(self):
        r = np.r_[np.full(40, 0.3), np.full(40, 0.9), np.full(10, 0.65)]
        e = np.r_[np.full(40, 0.2), np.full(40, 0.6), np.full(10, 0.4)]
        res = {"r_norm": r, "err_px": e, "dist_m": np.r_[np.full(45, 0.8), np.full(45, 1.7)],
               "view": np.arange(90) // 10}
        rep = pp.residual_report(res)
        self.assertTrue(rep["testable"])
        self.assertAlmostEqual(rep["outer_over_inner"], 3.0)
        self.assertEqual(sum(b["n"] for b in rep["radius_bins"]), 90)
        self.assertEqual(sum(b["n"] for b in rep["distance_bins"]), 90)
        few = {k: v[:60] for k, v in res.items()}                      # outer zone keeps 20 < 30
        self.assertIsNone(pp.residual_report(few)["outer_over_inner"])

    def test_tilt_decentre_flags(self):
        rep = {"outer_over_inner": 1.2}
        K = np.array([[900.0, 0, W / 2 + 0.01 * W], [0, 900.0, H / 2], [0, 0, 1]])
        f = pp.tilt_decentre_flags(K, [-0.1, 0.05, 2e-4, -3e-4, 0.0], (W, H), rep)
        self.assertEqual((f["principal_point"]["flag"], f["tangential"]["flag"], f["outer_inner_residual"]["flag"],
                          f["any"]), (False, False, False, False))
        K[1, 2] = H / 2 - 0.03 * H
        f = pp.tilt_decentre_flags(K, [-0.1, 0.05, 2e-3, 0.0, 0.0], (W, H), {"outer_over_inner": None})
        self.assertEqual((f["principal_point"]["flag"], f["tangential"]["flag"], f["outer_inner_residual"]["flag"],
                          f["any"]), (True, True, None, True))
        self.assertEqual(f["tangential"]["source"], "standard 5-coefficient fit")

    def test_heldout_residuals_reproduce_the_cv_rms(self):
        obj, img, centre = synthetic_views(FULL_POSES)
        cv = pm.cross_validate("standard", obj, img, (W, H), folds=3, seed=0, keep_fold_models=True)
        res = pp.heldout_residuals("standard", obj, img, cv, (W, H), centre)
        self.assertEqual(len(res["err_px"]), sum(len(p) for p in img))
        self.assertAlmostEqual(float(np.sqrt(np.mean(res["err_px"] ** 2))), cv["held_out_rms"], places=9)
        self.assertTrue(np.all((res["r_norm"] >= 0) & (res["r_norm"] <= 1.0)))
        d = {int(v): float(m) for v, m in zip(res["view"], res["dist_m"])}
        self.assertAlmostEqual(d[len(FULL_POSES) - 1], 1.5, delta=0.05)  # the last pose sits at 1500 mm

    def test_cross_group_check(self):
        obj, img, _ = synthetic_views(FULL_POSES)
        cv = pm.cross_validate("standard", obj, img, (W, H), folds=3, seed=0, keep_fold_models=True)
        groups = ["near" if p[5] < 860 else "far" for p in FULL_POSES]          # 12 near, 8 far
        out = pp.cross_group_check("standard", obj, img, (W, H), groups, cv, min_views=8)
        self.assertEqual(out["groups"], {"far": 8, "near": 12})
        for row in out["pairs"]:
            self.assertEqual(row["status"], "ok")
            self.assertLess(row["cross_rms_px"], 0.6)     # one true camera: carries across with 0.15 px noise
        out = pp.cross_group_check("standard", obj, img, (W, H), groups, cv, min_views=10)
        st = {(r["fit_on"], r["score_on"]): r["status"] for r in out["pairs"]}
        self.assertTrue(st[("far", "near")].startswith("not testable"))
        self.assertEqual(st[("near", "far")], "ok")


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestMaskRun(unittest.TestCase):
    """A full pipeline run with --mask, --models-reading literal and --groups on the FULL_POSES set."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cal = os.path.join(cls.tmp.name, "camM", "calibration")
        write_set(cls.cal, FULL_POSES, seed0=500)
        cls.vis = corner_mask(0.8)
        cls.mask = os.path.join(cls.tmp.name, "mask.png")
        cv2.imwrite(cls.mask, np.where(cls.vis, 255, 0).astype(np.uint8))
        cls.groups = os.path.join(cls.tmp.name, "groups.csv")
        with open(cls.groups, "w") as fh:
            fh.write("file,group\n" + "".join(f"cal_{i:04d}.jpg,{'near' if p[5] < 860 else 'far'}\n"
                                              for i, p in enumerate(FULL_POSES, 1)))
        cls.root = os.path.join(cls.tmp.name, "runs")
        cls.rc, cls.out = run_pipeline([f"camM={cls.cal}", "--out-root", cls.root, "--tag", "mask",
                                        "--mask", f"camM={cls.mask}", "--models-reading", "literal",
                                        "--groups", f"camM={cls.groups}"] + COMMON)
        cls.run_dir = only_run_dir(cls.root)
        with open(os.path.join(cls.run_dir, "summary.json")) as f:
            cls.cam = json.load(f)["cameras"][0]
        with open(os.path.join(cls.run_dir, "camM", "frames.csv")) as f:
            cls.rows = list(csv.DictReader(f))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_run_completes(self):
        self.assertIn(self.rc, (0, 3), self.out[-3000:])
        self.assertNotIn("error", self.cam)

    def test_masked_corners_dropped_at_detection(self):
        dropped = sum(int(r["masked_corners"] or 0) for r in self.rows)
        self.assertGreater(dropped, 0)
        cov = self.cam["coverage"]
        vf = np.array(cov["mask"]["cell_visible_frac"])
        counts = np.array(cov["counts"])
        self.assertTrue(np.all(counts[vf == 0] == 0))
        self.assertIn("masked corner observations dropped", self.out)

    def test_b3_b4_follow_the_mask(self):
        cov = self.cam["coverage"]
        self.assertEqual(cov["mask"]["n_eligible"], int((pp.cell_visible_fraction(self.vis, (8, 5)) >= 0.5).sum()))
        for p in self.cam["probes"]:
            if p["group"] == "frame":
                self.assertTrue(self.vis[int(p["y"]), int(p["x"])], p)
        by = {b["bar"].split()[0]: b for b in self.cam["bars"]}
        self.assertIn("visible", by["B3"]["rule"])
        self.assertIn("the visible area's 4 corners", by["B4"]["rule"])
        self.assertIn("LITERAL", by["B5c"]["rule"])

    def test_residuals_flags_and_cross_group_written(self):
        with open(os.path.join(self.run_dir, "camM", "residuals.json")) as f:
            r = json.load(f)
        self.assertEqual(r["models_reading"], "literal")
        self.assertIn("outer_over_inner", r["residual_report"])
        self.assertIn("any", r["flags"])
        self.assertEqual({(p["fit_on"], p["score_on"]) for p in r["cross_group"]["pairs"]},
                         {("near", "far"), ("far", "near")})
        self.assertTrue(os.path.getsize(os.path.join(self.run_dir, "camM", "residuals_vs_radius.csv")) > 0)
        with open(os.path.join(self.run_dir, "run_manifest.json")) as f:
            m = json.load(f)
        self.assertEqual(m["masks"]["camM"]["sha256"], pp.sha256_file(self.mask))
        self.assertEqual(m["models_reading"], "literal")
        with open(os.path.join(self.run_dir, "summary.md")) as f:
            md = f.read()
        for s in ("Mask (`mask.png`", "Tilt / decentre flags", "Cross-group check", "**literal** reading"):
            self.assertIn(s, md)

    def test_mask_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            for extra in (["--mask", "nope=" + self.mask], ["--mask", f"camM={tmp}/missing.png"],
                          ["--groups", f"camM={tmp}/missing.csv"]):
                rc, _ = run_pipeline([f"camM={self.cal}", "--out-root", tmp] + extra + COMMON)
                self.assertEqual(rc, 2, extra)
            bad = os.path.join(tmp, "small.png")
            cv2.imwrite(bad, np.full((10, 10), 255, np.uint8))
            rc, out = run_pipeline([f"camM={self.cal}", "--out-root", os.path.join(tmp, "r"), "--mask",
                                    f"camM={bad}"] + COMMON)
            self.assertEqual(rc, 1, out[-2000:])                           # every frame FAILs on the size check


@unittest.skipUnless(HAVE_DEPS, "opencv-contrib / scipy / matplotlib not installed")
class TestOverlayHullNaN(unittest.TestCase):
    """The after-undistortion hull has NaN where the model folds (pool049 IMX708): those points must not be
    drawn (NaN cast to int drew ~20 stray lines to the top-left corner)."""

    def test_nan_hull_points_are_skipped(self):
        import pool_calib_figures as figs
        img = np.zeros((400, 600, 3), np.uint8)
        hull = np.array([[200, 150], [400, 150], [np.nan, np.nan], [400, 300], [200, 300]], float)
        out = figs._overlay(img, [], "after", None, hull=hull)
        green = (out[:, :, 1] > 150) & (out[:, :, 0] < 120) & (out[:, :, 2] < 120)
        self.assertFalse(green[:100, :150].any())          # nothing drawn towards the top-left corner
        self.assertTrue(green[150, 250:350].any())          # the mappable top edge is still drawn
        self.assertTrue(green[300, 250:350].any())          # and the bottom edge
        self.assertTrue(green[200:250, 200].any())          # and the closing left edge

    def test_finite_hull_unchanged(self):
        import cv2
        import pool_calib_figures as figs
        img = np.zeros((400, 600, 3), np.uint8)
        hull = np.array([[200.7, 150.2], [400.4, 150.9], [400.1, 300.5], [200.2, 300.8]])
        new = figs._overlay(img, [], "after", None, hull=hull)
        old = figs._overlay(img, [], "after", None, hull=None)
        cv2.polylines(old, [np.asarray(hull, np.int32).reshape(-1, 1, 2)], True, (80, 220, 80),
                      max(2, int(round(600 / 1500))))
        self.assertTrue(np.array_equal(new, old))           # pixel-identical to the previous drawing
