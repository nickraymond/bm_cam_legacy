#!/usr/bin/env python3
# filename: test_focus_sweep_sheet.py
# description: Pool test 2026-10-13 — tools/focus_sweep_sheet.py on synthetic focus sweeps (board render + known blur per LensPosition).
"""
tools/focus_sweep_sheet.py gate on synthetic sweeps: Nick's board (14x9 modern, as the rig detected
it) is drawn into a 2304x1296 frame and blurred with sigma = 0.5 + 9 * |lp - true_focus|, so the
sharpest frame is known.

  1. unit A, TWO repeat sweeps (true focus 0.8, Pi manifest schema bmcam_focus_sweep_v1) -> PASS,
     best lp 0.81 (applied) in both, spread 0, Pi best_lp carried; ONE common region for all 20
     frames (QE #165) with corners_detected per frame (0 when defocused -> frame FAIL);
  2. unit B (true focus 1.8 = the sweep edge, NO manifest: lens from the file names) -> WARN
     "peak at the sweep edge";
  3. outputs: one cut sheet per unit (1:1 crop column), the overlay plot, CSV rows = frames,
     JSON verdicts; exit 0;
  4. explicit --sweep-dir x2 with the board moved in the second -> WARN "board box moved";
  5. a sweep without any board -> FAIL, exit 1; bad --unit / --sweep-dir / none -> exit 2.

Needs opencv-contrib-python-headless (>= 4.7); skipped otherwise.
Run (repo root):  python3 -m pytest -q tests/test_focus_sweep_sheet.py
"""
import contextlib
import csv
import io
import json
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

W, H = 2304, 1296
LENS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8]


def render(lp, focus, seed, board_present=True, shift=(0, 0)):
    rng = np.random.default_rng(seed)
    frame = np.full((H, W), 110, np.uint8)
    if board_present:
        d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
        board = cv2.aruco.CharucoBoard((14, 9), 40.0, 30.0, d)
        img = board.generateImage((14 * 40, 9 * 40), marginSize=0, borderBits=1)
        pad = 24
        card = np.full((img.shape[0] + 2 * pad, img.shape[1] + 2 * pad), 255, np.uint8)
        card[pad:pad + img.shape[0], pad:pad + img.shape[1]] = img
        src = np.float32([[0, 0], [card.shape[1], 0], [card.shape[1], card.shape[0]], [0, card.shape[0]]])
        dst = np.float32([[760, 330], [1560, 360], [1540, 900], [780, 930]]) + np.float32(shift)  # mild perspective
        M = cv2.getPerspectiveTransform(src, dst)
        frame = cv2.warpPerspective(card, M, (W, H), dst=frame, borderMode=cv2.BORDER_TRANSPARENT)
    sigma = 0.5 + 9.0 * abs(lp - focus)
    frame = cv2.GaussianBlur(frame, (0, 0), sigma)
    noise = rng.normal(0, 2, frame.shape)
    return np.clip(frame + noise, 0, 255).astype(np.uint8)


def write_sweep(d, focus, manifest, seed0=0, board_present=True, shift=(0, 0)):
    os.makedirs(d, exist_ok=True)
    frames = []
    for i, lp in enumerate(LENS):
        name = f"20261013T180000Z_sweep_lp{lp:.2f}.jpg"
        g = render(lp, focus, seed0 + i, board_present, shift)
        cv2.imwrite(os.path.join(d, name), cv2.cvtColor(g, cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
        frames.append({"i": i, "lens_requested": lp, "jpg": name, "dng": name[:-4] + ".dng",
                       "metadata": {"LensPosition": round(lp + 0.01, 3)},
                       "focus": {"lapvar": 100.0 + i, "tenengrad": 50.0 + i, "crop_xywh": [1792, 784, 1024, 1024]}})
    if manifest:
        with open(os.path.join(d, "sweep_manifest.json"), "w") as f:
            json.dump({"schema": "bmcam_focus_sweep_v1", "lens_list": LENS, "frames": frames, "n": len(frames),
                       "best_lp": focus, "best_lp_tenengrad": focus}, f)


@unittest.skipUnless(HAVE_CV2, "opencv-contrib-python-headless >= 4.7 not installed")
class TestFocusSweep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import focus_sweep_sheet as fss
        cls.fss = fss
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        # unit A: TWO repeat sweeps (unit-folder form: every sweep/<x>/ is used); unit B via --sweep-dir
        write_sweep(os.path.join(t, "A", "sweep", "180000"), focus=0.8, manifest=True)
        write_sweep(os.path.join(t, "A", "sweep", "181500"), focus=0.8, manifest=True, seed0=20)
        write_sweep(os.path.join(t, "B_sweep"), focus=1.8, manifest=False, seed0=50)
        cls.out = os.path.join(t, "out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cls.rc = fss.main(["--unit", f"bmcam003={os.path.join(t, 'A')}", "--sweep-dir", f"bmcam004={os.path.join(t, 'B_sweep')}",
                               "--board-distance-m", "1.0", "--run-tag", "synthetic", "--out", cls.out])
        cls.log = buf.getvalue()
        with open(os.path.join(cls.out, "focus_sweep.json")) as f:
            cls.rep = json.load(f)
        cls.units = {u["unit"]: u for u in cls.rep["units"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_unit_a_pass_two_sweeps_zero_spread(self):
        self.assertEqual(self.rc, 0, self.log)
        a = self.units["bmcam003"]
        self.assertEqual(a["verdict"], "PASS", a["reasons"])
        self.assertEqual([s["sweep"] for s in a["sweeps"]], ["180000", "181500"])
        self.assertEqual(a["best_lp_per_sweep"], [0.81, 0.81])            # applied lens (manifest)
        self.assertEqual(a["best_lp_spread"], 0)
        for s in a["sweeps"]:
            self.assertEqual(s["verdict"], "PASS", s["reasons"])
            self.assertAlmostEqual(s["best"]["lp_requested"], 0.8)
            self.assertAlmostEqual(s["best"]["tenengrad_best_lp"], 0.81)
            self.assertAlmostEqual(s["pi"]["best_lp"], 0.8)               # the Pi's own answer, carried
            self.assertGreater(s["box_iou_vs_common"], 0.9)
        self.assertEqual(a["layout"]["chosen"]["squares_x"], 14)
        self.assertTrue(a["lens_spread"] and all(x["n"] == 2 for x in a["lens_spread"]))

    def test_one_common_region_for_every_frame(self):
        """QE #165: every frame is scored on the SAME pixels; each frame's own detection is its own column."""
        a = self.units["bmcam003"]
        self.assertEqual({tuple(f["roi"]) for f in a["frames"]}, {tuple(a["common_roi"])})
        self.assertIn("common board box", a["common_roi_source"])
        det = [f["corners_detected"] for f in a["frames"]]
        self.assertEqual(max(det), 104)
        self.assertEqual(min(det), 0)                                     # defocused frames: not detected
        self.assertTrue(all(f["status"] == "FAIL" for f in a["frames"] if f["corners_detected"] < 4))
        with open(os.path.join(self.out, "focus_sweep.csv")) as f:
            rows = list(csv.DictReader(f))
        self.assertIn("corners_detected", rows[0])
        self.assertEqual(len({r["roi"] for r in rows if r["unit"] == "bmcam003"}), 1)

    def test_unit_b_warn_peak_at_edge(self):
        b = self.units["bmcam004"]
        self.assertEqual(b["verdict"], "WARN")
        self.assertAlmostEqual(b["sweeps"][0]["best"]["lp"], 1.8)         # from the file name
        self.assertTrue(any("sweep edge" in r for r in b["reasons"]), b["reasons"])
        self.assertTrue(all(f["lp_applied"] is None for f in b["frames"]))

    def test_outputs(self):
        names = set(os.listdir(self.out))
        for n in ("focus_sheet_bmcam003.jpg", "focus_sheet_bmcam004.jpg", "focus_plot.png",
                  "focus_sweep.csv", "focus_sweep.json", "focus_sweep.log"):
            self.assertIn(n, names)
        with open(os.path.join(self.out, "focus_sweep.csv")) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 3 * len(LENS))
        cell = self.fss.CROP_W + self.fss.TEXT_W + 8
        sa = cv2.imread(os.path.join(self.out, "focus_sheet_bmcam003.jpg"))
        sb = cv2.imread(os.path.join(self.out, "focus_sheet_bmcam004.jpg"))
        self.assertEqual(sa.shape[1], 4 + self.fss.ROW_W + 8 + 2 * cell)     # two sweeps side by side
        self.assertEqual(sb.shape[1], 4 + self.fss.ROW_W + 8 + 1 * cell)
        self.assertGreater(sa.shape[0], len(LENS) * self.fss.CROP_H)
        self.assertIsNotNone(cv2.imread(os.path.join(self.out, "focus_plot.png")))
        self.assertEqual(self.rep["medium"], "in-air")
        self.assertEqual(self.rep["board_distance_m"], 1.0)

    def test_metric_peaks_at_focus(self):
        fr = sorted([f for f in self.units["bmcam003"]["frames"] if f["sweep"] == "180000"], key=lambda f: f["lp_req"])
        lv = [f["lapvar_norm"] for f in fr]
        self.assertEqual(max(range(len(lv)), key=lambda i: lv[i]), LENS.index(0.8))
        self.assertGreater(lv[LENS.index(0.8)], 3 * lv[0])               # clear peak

    def test_explicit_sweep_dir_and_board_moved(self):
        with tempfile.TemporaryDirectory() as d:
            s1, s2 = os.path.join(d, "s1"), os.path.join(d, "s2")
            write_sweep(s1, focus=0.8, manifest=True)
            write_sweep(s2, focus=0.8, manifest=True, seed0=30, shift=(500, 250))   # board moved
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = self.fss.main(["--sweep-dir", f"bmcam003={s1}", "--sweep-dir", f"bmcam003={s2}",
                                    "--out", os.path.join(d, "o")])
            with open(os.path.join(d, "o", "focus_sweep.json")) as f:
                u = json.load(f)["units"][0]
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertEqual([s["sweep"] for s in u["sweeps"]], ["s1", "s2"])
        self.assertEqual(u["verdict"], "WARN")
        self.assertTrue(any("board box moved" in r for r in u["reasons"]), u["reasons"])

    def test_no_board_fails(self):
        with tempfile.TemporaryDirectory() as d:
            write_sweep(os.path.join(d, "s"), focus=0.8, manifest=False, board_present=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = self.fss.main(["--unit", f"bmcam003={os.path.join(d, 's')}", "--out", os.path.join(d, "o"),
                                    "--layout", "modern", "--squares-x", "14", "--squares-y", "9"])
            with open(os.path.join(d, "o", "focus_sweep.json")) as f:
                u = json.load(f)["units"][0]
        self.assertEqual(rc, 1)
        self.assertEqual(u["verdict"], "FAIL")
        self.assertTrue(all(fr["roi_source"].startswith("frame centre") for fr in u["frames"]))

    def test_bad_unit_arg(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.fss.main(["--unit", "bmcam003"]), 2)
            self.assertEqual(self.fss.main(["--unit", "bmcam003=/nonexistent"]), 2)
            self.assertEqual(self.fss.main(["--sweep-dir", "bmcam003=/nonexistent"]), 2)
            self.assertEqual(self.fss.main([]), 2)


if __name__ == "__main__":
    unittest.main()
