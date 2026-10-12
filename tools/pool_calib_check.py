#!/usr/bin/env python3
# filename: pool_calib_check.py
# description: Pool test 2026-10-13 (N4) — IN-AIR ENGINEERING CHECK: ChArUco detection + OpenCV intrinsics from a unit's calibration frames, with the 1600x900 crop intrinsics.
"""
pool_calib_check.py — IN-AIR ENGINEERING CHECK of a unit's ChArUco calibration frames (Mac side).

Purpose
  T-LAND (docs/em/POOL_PLAN_2026-10-13.md): "will Tuesday's calibration frames give usable
  intrinsics?" Point this at a pulled calibration/ folder (tools/pool_pull.py ->
  ~/NereusPool/<date>/<unit>/calibration/). It detects the ChArUco board in every frame,
  calibrates the camera with OpenCV, derives the production 1600x900 crop intrinsics, and
  says PASS / FAIL against the T-LAND bar. This is an engineering check of the capture
  (in air); it is NOT the underwater calibration.

Pass bar (T-LAND): board found in >= 15 DISTINCT usable poses AND RMS reprojection < 1 px.
  Stop rule from the card: < 10 poses -> fix capture settings (exposure / blur) first.

Inputs
  folder                   calibration frames: *.dng and/or *.jpg/*.jpeg (one pose per file stem;
                           a DNG+JPEG pair of the same stem counts once)
  Board defaults = Nick's pool board (label photo, 2026-10-12 12:00 PM): calib.io CCT600400C,
  600x400 mm, ChArUco 9 x 14 squares (9 across the short side), checker 40 mm, marker 30 mm,
  DICT_5X5 (63 markers -> DICT_5X5_100), pattern area 360 x 560 mm.
  --squares-x 9 --squares-y 14 --square-mm 40 --marker-mm 30 --dictionary DICT_5X5_100
  --layout auto|modern|legacy  with an EVEN row count (14) OpenCV >= 4.6's ChArUco layout differs
                           from the legacy one some calib.io boards use (setLegacyPattern). auto
                           (default) tries modern + legacy, each as 9x14 and 14x9, on a sample of
                           frames and keeps the clear winner (all four counts go in the report);
                           no clear winner -> FAIL, nothing is calibrated on a guessed layout.
  --source auto|dng|jpeg   auto = DNG via rawpy if installed, else the JPEG (default auto)
  --crop x,y,w,h           production crop, native px (default 1504,846,1600,900 =
                           BM_Devel_Pi/config_registry.py still.crop; video_geometry.STILLS_ROI_CROP)
  --crop-output-width W    only if the crop is resampled (default: none = native scale, no scaling)
  --min-corners N          ChArUco corners for a frame to be usable (default 12)
  --distinct-deg / --distinct-frac   two usable poses are the same pose unless the board turned
                           >= 5 deg or moved >= 10 % of its distance (defaults)

Outputs (in --out, default <folder>/../calib_check_<YYYYMMDD_HHMMSS>/)
  calib_report.json        board, OpenCV version, per-frame results, calibration (full native
                           frame) + crop intrinsics, pass/fail with reasons
  frames.csv               one row per frame: corners, markers, coverage, blur, used, distinct, error
  contact_sheet.jpg        every frame scaled down (NOT 1:1) with detected corners drawn + labels
  calib_check.log          everything printed

Example
  python3 tools/pool_calib_check.py ~/NereusPool/20261013/bmcam003/calibration      # Nick's board
  python3 tools/pool_calib_check.py <folder> --layout legacy --squares-x 14 --squares-y 9   # pin it

Requirements (Mac): pip install opencv-contrib-python-headless rawpy   (OpenCV >= 4.7 for
  cv2.aruco.CharucoDetector; built + tested with OpenCV 5.0.0 contrib headless, rawpy 0.27.1,
  numpy 2.5.3). rawpy is optional (DNG); without it the JPEGs are used.

Assumptions / limits
  - Crop intrinsics are exact only if the calibration frames are the full native 4608x2592
    frame and the production crop is a pure pixel crop (stills: rc_progressive_jpeg in-process
    crop; nrjxl RAW crop). Then cx, cy shift by the crop offset; fx, fy and the distortion
    coefficients are unchanged. Other frame sizes: crop derivation is skipped (loud).
  - DNG goes through rawpy demosaic (camera WB, 8-bit); JPEG through the Pi ISP. Same pixel grid.
  - Distinct-pose counting uses the per-view board pose from the calibration itself.
  - In-air intrinsics. Behind the flat port in water the focal length scales (~1.33x) and the
    distortion changes; this check says the frames are usable, not what the underwater K is.
"""
import argparse
import csv
import glob
import json
import math
import os
import platform
import sys
import time
from datetime import datetime

NATIVE = (4608, 2592)                      # IMX708 native (rc_jpeg_encoder.NATIVE_SIZE)
DEFAULT_CROP = (1504, 846, 1600, 900)      # config_registry still.crop default
PASS_POSES, PASS_RMS, STOP_POSES = 15, 1.0, 10
JPEG_EXT = (".jpg", ".jpeg")
DNG_EXT = (".dng",)


def need_cv2():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        sys.exit(f"[calib] REFUSED: {exc}. Install: pip install opencv-contrib-python-headless numpy")
    if not hasattr(cv2, "aruco") or not hasattr(cv2.aruco, "CharucoDetector"):
        sys.exit(f"[calib] REFUSED: OpenCV {cv2.__version__} lacks cv2.aruco.CharucoDetector (needs >= 4.7). "
                 f"pip install -U opencv-contrib-python-headless")
    return cv2, np


class Log:
    def __init__(self):
        self.f = None
        self.buf = []

    def open(self, path):
        self.f = open(path, "a")
        for line in self.buf:
            self.f.write(line + "\n")
        self.f.flush()

    def __call__(self, msg):
        line = f"{datetime.now().strftime('%I:%M:%S %p')} {msg}"
        print(line, flush=True)
        if self.f:
            self.f.write(line + "\n")
            self.f.flush()
        else:
            self.buf.append(line)


# ----------------------------------------------------------------------------- inputs

def find_frames(folder, source, have_rawpy):
    """-> sorted [(stem, path, kind)] one per stem. auto prefers DNG when rawpy is present."""
    by_stem = {}
    for p in sorted(glob.glob(os.path.join(folder, "*"))):
        ext = os.path.splitext(p)[1].lower()
        if ext in JPEG_EXT + DNG_EXT:
            by_stem.setdefault(os.path.splitext(os.path.basename(p))[0], {})[
                "dng" if ext in DNG_EXT else "jpeg"] = p
    out = []
    for stem, kinds in sorted(by_stem.items()):
        if source == "dng" or (source == "auto" and have_rawpy and "dng" in kinds):
            pick = "dng" if "dng" in kinds else None
        else:
            pick = "jpeg" if "jpeg" in kinds else None
        if pick is None and source == "auto":
            pick = "jpeg" if "jpeg" in kinds else None     # DNG-only stem without rawpy
        if pick:
            out.append((stem, kinds[pick], pick))
    return out


def load_gray(path, kind, cv2, np):
    if kind == "dng":
        import rawpy
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("cv2.imread failed (not a readable JPEG)")
    return img


# ----------------------------------------------------------------------------- detection

BOARD_SPEC = dict(squares_x=9, squares_y=14, square_mm=40.0, marker_mm=30.0, dictionary="DICT_5X5_100")


def make_board(sx, sy, a, cv2, legacy):
    d = getattr(cv2.aruco, a.dictionary, None)
    if d is None or not a.dictionary.startswith("DICT_"):
        sys.exit(f"[calib] REFUSED: unknown --dictionary {a.dictionary!r} (e.g. DICT_4X4_50, DICT_5X5_100, DICT_6X6_250)")
    n_markers = (sx * sy) // 2
    if n_markers > cv2.aruco.getPredefinedDictionary(d).bytesList.shape[0]:
        sys.exit(f"[calib] REFUSED: a {sx}x{sy} board needs {n_markers} markers; {a.dictionary} has fewer")
    board = cv2.aruco.CharucoBoard((sx, sy), a.square_mm, a.marker_mm, cv2.aruco.getPredefinedDictionary(d))
    board.setLegacyPattern(bool(legacy))
    return board


def layout_candidates(a):
    """(squares_x, squares_y, legacy) to try, in tie-break order (the spec as given first)."""
    legs = {"auto": (False, True), "modern": (False,), "legacy": (True,)}[a.layout]
    dims = [(a.squares_x, a.squares_y)]
    if a.layout == "auto" and a.squares_x != a.squares_y:
        dims.append((a.squares_y, a.squares_x))
    return [(sx, sy, lg) for lg in legs for (sx, sy) in dims]


def choose_layout(grays, a, cv2):
    """Run every candidate layout on sample frames -> (winner or None, trials, why).
    Winner = most ChArUco corners in total, and clearly ahead (>= 1.5x the runner-up)."""
    trials = []
    for sx, sy, lg in layout_candidates(a):
        det = cv2.aruco.CharucoDetector(make_board(sx, sy, a, cv2, lg))
        corners = markers = 0
        for g in grays:
            cc, ci, mc, mi = det.detectBoard(g)
            corners += 0 if ci is None else len(ci)
            markers += 0 if mi is None else len(mi)
        trials.append({"squares_x": sx, "squares_y": sy, "legacy": lg, "corners": corners, "markers": markers})
    ranked = sorted(trials, key=lambda t: -t["corners"])
    best = ranked[0]
    if best["corners"] == 0:
        return None, trials, "no layout found any ChArUco corner in the sample"
    if len(ranked) > 1 and best["corners"] < 1.5 * ranked[1]["corners"]:
        return None, trials, (f"no clear layout winner ({best['corners']} vs {ranked[1]['corners']} corners): "
                              f"pin it with --layout and --squares-x/--squares-y")
    return best, trials, None


def hull_coverage(pts, w, h, cv2, np):
    if pts is None or len(pts) < 3:
        return 0.0
    hull = cv2.convexHull(pts.reshape(-1, 1, 2).astype(np.float32))
    return float(cv2.contourArea(hull)) / float(w * h)


def detect(gray, board, detector, a, cv2, np):
    ch_corners, ch_ids, mk_corners, mk_ids = detector.detectBoard(gray)
    n_ch = 0 if ch_ids is None else len(ch_ids)
    n_mk = 0 if mk_ids is None else len(mk_ids)
    h, w = gray.shape[:2]
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    lap_board = None
    if n_ch >= 4:
        x, y, bw, bh = cv2.boundingRect(ch_corners.reshape(-1, 1, 2).astype(np.float32))
        roi = gray[max(0, y):y + bh, max(0, x):x + bw]
        if roi.size:
            lap_board = float(cv2.Laplacian(roi, cv2.CV_64F).var())
    collinear = bool(n_ch >= 4 and board.checkCharucoCornersCollinear(ch_ids))
    return {
        "w": w, "h": h, "markers": n_mk, "corners": n_ch,
        "ids": [int(i) for i in ch_ids.flatten()] if n_ch else [],
        "coverage": hull_coverage(ch_corners, w, h, cv2, np) if n_ch else 0.0,
        "lapvar_frame": lap, "lapvar_board": lap_board, "collinear": collinear,
        "_corners": ch_corners, "_ids": ch_ids, "_mk": mk_corners,
    }


# ----------------------------------------------------------------------------- poses

def distinct_flags(rvecs, tvecs, deg, frac, cv2, np):
    """Greedy: a view is distinct unless it is within `deg` rotation AND `frac` * distance
    translation of an already-kept view."""
    kept, flags = [], []
    for rv, tv in zip(rvecs, tvecs):
        R, _ = cv2.Rodrigues(rv)
        t = tv.reshape(3)
        same = False
        for R2, t2 in kept:
            ang = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R @ R2.T) - 1) / 2))))
            if ang < deg and np.linalg.norm(t - t2) < frac * max(np.linalg.norm(t), 1e-9):
                same = True
                break
        flags.append(not same)
        if not same:
            kept.append((R, t))
    return flags


def union_coverage(used, w, h, np, gx=12, gy=8):
    hit = np.zeros((gy, gx), bool)
    for f in used:
        for x, y in f["_corners"].reshape(-1, 2):
            hit[min(gy - 1, max(0, int(y * gy / h))), min(gx - 1, max(0, int(x * gx / w)))] = True
    return float(hit.mean())


# ----------------------------------------------------------------------------- crop intrinsics

def crop_intrinsics(K, frame_wh, crop, out_width=None):
    """Full-frame K -> K for the crop [x, y, w, h] (native px). Pure pixel crop: cx -= x, cy -= y.
    If the crop is resampled to out_width: scale with the pixel-centre convention."""
    x, y, w, h = crop
    if tuple(frame_wh) != NATIVE:
        return None, f"frames are {frame_wh[0]}x{frame_wh[1]}, not native {NATIVE[0]}x{NATIVE[1]}: crop derivation skipped"
    if x + w > NATIVE[0] or y + h > NATIVE[1]:
        return None, f"crop {crop} exceeds native {NATIVE}"
    fx, fy, cx, cy = K[0][0], K[1][1], K[0][2] - x, K[1][2] - y
    size = [w, h]
    note = "pure pixel crop: cx,cy shifted by the crop offset; fx, fy, distortion unchanged"
    if out_width and out_width != w:
        s = out_width / float(w)
        fx, fy = fx * s, fy * s
        cx, cy = (cx + 0.5) * s - 0.5, (cy + 0.5) * s - 0.5
        size = [out_width, round(out_width * h / w)]
        note = f"crop then resample x{s:.4f} (pixel-centre convention); distortion unchanged"
    return {"crop_native_xywh": list(crop), "image_size": size,
            "camera_matrix": [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]],
            "fx": fx, "fy": fy, "cx": cx, "cy": cy, "note": note}, None


# ----------------------------------------------------------------------------- contact sheet

THUMB_W = 480


def thumbnail(gray, cv2):
    """Keep only a THUMB_W-wide copy per frame (120 full 12 MP frames would be ~1.4 GB)."""
    s = THUMB_W / gray.shape[1]
    return cv2.resize(gray, (THUMB_W, int(round(gray.shape[0] * s))), interpolation=cv2.INTER_AREA), s


def contact_sheet(frames, path, header, cv2, np, thumb_w=THUMB_W, cols=5):
    tiles = []
    for fr in frames:
        img, s = fr.get("_thumb"), fr.get("_scale")
        if img is None:
            tile = np.full((int(thumb_w * 9 / 16), thumb_w, 3), 40, np.uint8)
        else:
            tile = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            if fr.get("_corners") is not None and fr["corners"]:
                for p in fr["_corners"].reshape(-1, 2) * s:
                    cv2.circle(tile, (int(p[0]), int(p[1])), 3, (0, 255, 0), -1)
            if fr.get("_mk"):
                for m in fr["_mk"]:
                    cv2.polylines(tile, [(m.reshape(-1, 2) * s).astype(np.int32)], True, (255, 160, 0), 1)
        state = ("DISTINCT" if fr.get("distinct") else "USED dup") if fr.get("used") else f"REJ {fr.get('reject', '')}"
        color = (80, 220, 80) if fr.get("distinct") else ((80, 200, 230) if fr.get("used") else (60, 60, 240))
        cv2.rectangle(tile, (0, 0), (tile.shape[1], 40), (0, 0, 0), -1)
        cv2.putText(tile, f"{fr['stem']} [{fr['kind']}] {state}", (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        err = f" err {fr['reproj_px']:.2f}px" if fr.get("reproj_px") is not None else ""
        lb = f"{fr['lapvar_board']:.0f}" if fr.get("lapvar_board") is not None else "-"
        cv2.putText(tile, f"corners {fr['corners']} mk {fr['markers']} cov {100 * fr['coverage']:.0f}% "
                          f"lap {fr['lapvar_frame']:.0f}/{lb}{err}",
                    (6, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (230, 230, 230), 1)
        tiles.append(tile)
    if not tiles:
        return
    th = max(t.shape[0] for t in tiles)
    tiles = [np.vstack([t, np.zeros((th - t.shape[0], thumb_w, 3), np.uint8)]) if t.shape[0] < th else t for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros((th, thumb_w, 3), np.uint8))
    grid = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    head = np.zeros((26 * (len(header) + 1), grid.shape[1], 3), np.uint8)
    for i, line in enumerate(header):
        cv2.putText(head, line, (10, 22 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (80, 220, 80) if "PASS" in line and i == 1 else ((60, 60, 240) if "FAIL" in line and i == 1 else (240, 240, 240)), 1)
    cv2.imwrite(path, np.vstack([head, grid]), [cv2.IMWRITE_JPEG_QUALITY, 85])


# ----------------------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(
        description="IN-AIR ENGINEERING CHECK: ChArUco detection + OpenCV intrinsics from pool calibration frames.",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="calibration frames folder (*.dng / *.jpg)")
    ap.add_argument("--squares-x", type=int, default=BOARD_SPEC["squares_x"], help="board squares across (default 9)")
    ap.add_argument("--squares-y", type=int, default=BOARD_SPEC["squares_y"], help="board squares down (default 14)")
    ap.add_argument("--square-mm", type=float, default=BOARD_SPEC["square_mm"], help="checker side mm (default 40)")
    ap.add_argument("--marker-mm", type=float, default=BOARD_SPEC["marker_mm"], help="marker side mm (default 30)")
    ap.add_argument("--dictionary", default=BOARD_SPEC["dictionary"], help="ArUco dictionary (default DICT_5X5_100)")
    ap.add_argument("--layout", choices=("auto", "modern", "legacy"), default="auto",
                    help="ChArUco layout; auto tries modern/legacy x both orientations (default)")
    ap.add_argument("--layout-sample", type=int, default=8, help="frames used to choose the layout (auto)")
    ap.add_argument("--source", choices=("auto", "dng", "jpeg"), default="auto")
    ap.add_argument("--crop", default=",".join(map(str, DEFAULT_CROP)), help="production crop x,y,w,h native px")
    ap.add_argument("--crop-output-width", type=int, help="only if the crop is resampled (default: none)")
    ap.add_argument("--min-corners", type=int, default=12)
    ap.add_argument("--distinct-deg", type=float, default=5.0)
    ap.add_argument("--distinct-frac", type=float, default=0.10)
    ap.add_argument("--out", help="output folder (default <folder>/../calib_check_<ts>)")
    ap.add_argument("--no-sheet", action="store_true", help="skip the contact sheet")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    missing = [f"--{n.replace('_', '-')}" for n in ("squares_x", "squares_y", "square_mm", "marker_mm", "dictionary")
               if getattr(a, n) in (None, "")]
    if missing:
        print(f"[calib] REFUSED: board spec missing {' '.join(missing)} (a wrong board spec gives a confident "
              f"wrong calibration; defaults = calib.io CCT600400C)", file=sys.stderr)
        return 2
    if a.marker_mm >= a.square_mm or min(a.squares_x, a.squares_y) < 3:
        print("[calib] REFUSED: need marker_mm < square_mm and >= 3x3 squares", file=sys.stderr)
        return 2
    try:
        crop = tuple(int(v) for v in a.crop.split(","))
        assert len(crop) == 4
    except (ValueError, AssertionError):
        print(f"[calib] REFUSED: --crop {a.crop!r} must be x,y,w,h", file=sys.stderr)
        return 2
    if not os.path.isdir(a.folder):
        print(f"[calib] REFUSED: {a.folder} is not a folder", file=sys.stderr)
        return 2

    cv2, np = need_cv2()
    try:
        import rawpy  # noqa: F401
        have_rawpy, rawpy_ver = True, rawpy.__version__
    except ImportError:
        have_rawpy, rawpy_ver = False, None
    if a.source == "dng" and not have_rawpy:
        print("[calib] REFUSED: --source dng needs rawpy (pip install rawpy)", file=sys.stderr)
        return 2

    log = Log()
    folder = os.path.abspath(a.folder)
    out = a.out or os.path.join(os.path.dirname(folder.rstrip("/")),
                                f"calib_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(out, exist_ok=True)
    log.open(os.path.join(out, "calib_check.log"))
    log("[calib] IN-AIR ENGINEERING CHECK (not the underwater calibration)")
    log(f"[calib] input {folder}  out {out}")
    log(f"[calib] OpenCV {cv2.__version__} (cv2.aruco.CharucoDetector API), numpy {np.__version__}, "
        f"rawpy {rawpy_ver or 'not installed -> JPEG only'}, python {platform.python_version()}")
    spec_default = all(getattr(a, k) == v for k, v in BOARD_SPEC.items())
    log(f"[calib] board {a.squares_x}x{a.squares_y} squares, checker {a.square_mm} mm, marker {a.marker_mm} mm, "
        f"{a.dictionary}{' (calib.io CCT600400C default)' if spec_default else ' (NOT the default board)'}; "
        f"layout {a.layout}; usable >= {a.min_corners} corners")

    frames_in = find_frames(folder, a.source, have_rawpy)
    if not frames_in:
        log(f"[calib] FAIL: no *.dng / *.jpg frames in {folder}")
        return 1
    log(f"[calib] {len(frames_in)} pose(s) to read ({sum(1 for f in frames_in if f[2] == 'dng')} DNG, "
        f"{sum(1 for f in frames_in if f[2] == 'jpeg')} JPEG; one per file stem)")

    # layout: try the candidates on an evenly spread sample of frames
    cands = layout_candidates(a)
    if len(cands) == 1:
        layout, trials, why = {"squares_x": cands[0][0], "squares_y": cands[0][1], "legacy": cands[0][2]}, [], None
    else:
        n = min(a.layout_sample, len(frames_in))
        pick = sorted({round(i * (len(frames_in) - 1) / max(n - 1, 1)) for i in range(n)})
        sample = []
        for i in pick:
            stem, path, kind = frames_in[i]
            try:
                sample.append(load_gray(path, kind, cv2, np))
            except Exception as exc:
                log(f"[calib] WARN layout sample {os.path.basename(path)} unreadable: {exc}")
        layout, trials, why = choose_layout(sample, a, cv2)
        for t in trials:
            log(f"[calib] layout try {t['squares_x']}x{t['squares_y']} {'legacy' if t['legacy'] else 'modern'}: "
                f"{t['corners']} corners, {t['markers']} markers on {len(sample)} sample frames")
    if layout is None:
        log(f"[calib] FAIL: {why}")
        with open(os.path.join(out, "calib_report.json"), "w") as f:
            json.dump({"check": "IN-AIR ENGINEERING CHECK (T-LAND)", "verdict": "FAIL", "reasons": [why],
                       "layout_trials": trials, "input": folder}, f, indent=1)
        return 1
    board = make_board(layout["squares_x"], layout["squares_y"], a, cv2, layout["legacy"])
    log(f"[calib] layout {layout['squares_x']}x{layout['squares_y']} "
        f"{'LEGACY' if layout['legacy'] else 'modern'} ({'chosen by auto' if trials else 'pinned'})")
    detector = cv2.aruco.CharucoDetector(board)

    frames, t0 = [], time.time()
    for i, (stem, path, kind) in enumerate(frames_in, 1):
        fr = {"stem": stem, "file": os.path.basename(path), "kind": kind}
        try:
            gray = load_gray(path, kind, cv2, np)
            fr.update(detect(gray, board, detector, a, cv2, np))
            if not a.no_sheet:
                fr["_thumb"], fr["_scale"] = thumbnail(gray, cv2)
            del gray
        except Exception as exc:          # one bad file must not kill the run
            fr.update({"w": None, "h": None, "markers": 0, "corners": 0, "ids": [], "coverage": 0.0,
                       "lapvar_frame": 0.0, "lapvar_board": None, "collinear": False, "error": str(exc)})
        if fr.get("error"):
            fr["reject"] = "read error"
        elif fr["corners"] < a.min_corners:
            fr["reject"] = f"<{a.min_corners} corners"
        elif fr["collinear"]:
            fr["reject"] = "collinear"
        fr["used"] = "reject" not in fr
        frames.append(fr)
        lb = f"{fr['lapvar_board']:.0f}" if fr.get("lapvar_board") is not None else "-"
        log(f"[calib] {i:3d}/{len(frames_in)} {fr['file']}: corners {fr['corners']:3d} markers {fr['markers']:3d} "
            f"cov {100 * fr['coverage']:4.1f}% lap {fr['lapvar_frame']:.0f}/{lb} "
            f"{'USED' if fr['used'] else 'REJECTED ' + fr['reject']}{' ' + fr['error'] if fr.get('error') else ''}")
    log(f"[calib] detection done in {time.time() - t0:.1f}s")

    used = [f for f in frames if f["used"]]
    sizes = sorted({(f["w"], f["h"]) for f in used})
    report = {
        "check": "IN-AIR ENGINEERING CHECK (T-LAND), not the underwater calibration",
        "created": datetime.now().isoformat(timespec="seconds"), "input": folder, "out": out,
        "software": {"opencv": cv2.__version__, "api": "cv2.aruco.CharucoDetector + board.matchImagePoints + "
                     "cv2.calibrateCameraExtended", "numpy": np.__version__, "rawpy": rawpy_ver,
                     "python": platform.python_version()},
        "board": {"squares_x": a.squares_x, "squares_y": a.squares_y, "square_mm": a.square_mm,
                  "marker_mm": a.marker_mm, "dictionary": a.dictionary, "spec_is_default": spec_default,
                  "default_spec": "calib.io CCT600400C 600x400 mm, ChArUco 9x14, 40/30 mm, DICT_5X5 (63 markers)"},
        "layout": {"mode": a.layout, "chosen": layout, "trials": trials},
        "settings": {"source": a.source, "min_corners": a.min_corners, "distinct_deg": a.distinct_deg,
                     "distinct_frac": a.distinct_frac, "crop": list(crop), "crop_output_width": a.crop_output_width},
        "pass_bar": {"distinct_poses_min": PASS_POSES, "rms_px_max": PASS_RMS, "stop_below_poses": STOP_POSES},
        "frames_total": len(frames), "frames_used": len(used), "calibration": None, "crop_intrinsics": None,
    }
    reasons = []
    if not used and sum(f["markers"] for f in frames) > 0:
        log("[calib] HINT: markers were found but no usable ChArUco corners: check --layout, "
            "--squares-x/--squares-y and the dictionary")
    if len(sizes) > 1:
        reasons.append(f"mixed frame sizes {sizes}")
        log(f"[calib] FAIL: used frames have mixed sizes {sizes}; use --source to pick one kind")
    elif len(used) < 4:
        reasons.append(f"only {len(used)} usable frames (calibration needs >= 4; bar {PASS_POSES} distinct)")
        log(f"[calib] FAIL: only {len(used)} usable frame(s); no calibration")
    else:
        w, h = sizes[0]
        obj, img = [], []
        for f in used:
            o, p = board.matchImagePoints(f["_corners"], f["_ids"])
            obj.append(o.astype(np.float32))
            img.append(p.astype(np.float32))
        rms, K, dist, rvecs, tvecs, _, _, per_view = cv2.calibrateCameraExtended(obj, img, (w, h), None, None)
        flags = distinct_flags(rvecs, tvecs, a.distinct_deg, a.distinct_frac, cv2, np)
        for f, d, e, tv in zip(used, flags, per_view.reshape(-1), tvecs):
            f["distinct"], f["reproj_px"], f["distance_mm"] = bool(d), float(e), float(np.linalg.norm(tv))
        n_distinct = sum(flags)
        Kl = K.tolist()
        fov_h = 2 * math.degrees(math.atan(w / (2 * K[0][0])))
        fov_v = 2 * math.degrees(math.atan(h / (2 * K[1][1])))
        report["calibration"] = {
            "image_size": [w, h], "rms_px": float(rms), "camera_matrix": Kl,
            "fx": K[0][0], "fy": K[1][1], "cx": K[0][2], "cy": K[1][2],
            "dist_coeffs_k1k2p1p2k3": dist.reshape(-1).tolist(),
            "fov_deg_h_v": [fov_h, fov_v],
            "principal_point_offset_px": [K[0][2] - (w - 1) / 2.0, K[1][2] - (h - 1) / 2.0],
            "distinct_poses": n_distinct, "views_used": len(used),
            "coverage_union_cells": union_coverage(used, w, h, np),
            "coverage_union_note": "fraction of a 12x8 grid over the frame with >= 1 used corner (edges/corners "
                                   "of the frame constrain distortion); reported, not part of the pass bar",
        }
        log(f"[calib] calibration {w}x{h}: RMS {rms:.3f} px over {len(used)} views; distinct poses {n_distinct}; "
            f"frame grid covered {100 * report['calibration']['coverage_union_cells']:.0f}%")
        log(f"[calib]   fx {K[0][0]:.1f} fy {K[1][1]:.1f} cx {K[0][2]:.1f} cy {K[1][2]:.1f}  "
            f"FOV {fov_h:.1f} x {fov_v:.1f} deg  dist {np.array2string(dist.reshape(-1), precision=4)}")
        ck, why = crop_intrinsics(Kl, (w, h), crop, a.crop_output_width)
        if ck:
            ck["dist_coeffs_k1k2p1p2k3"] = dist.reshape(-1).tolist()
            report["crop_intrinsics"] = ck
            log(f"[calib] crop {crop} -> {ck['image_size'][0]}x{ck['image_size'][1]}: fx {ck['fx']:.1f} "
                f"fy {ck['fy']:.1f} cx {ck['cx']:.1f} cy {ck['cy']:.1f} ({ck['note']})")
        else:
            report["crop_intrinsics_skipped"] = why
            log(f"[calib] WARN crop intrinsics skipped: {why}")
        if n_distinct < PASS_POSES:
            reasons.append(f"distinct poses {n_distinct} < {PASS_POSES}")
        if rms >= PASS_RMS:
            reasons.append(f"RMS {rms:.3f} px >= {PASS_RMS}")
    n_distinct = report["calibration"]["distinct_poses"] if report["calibration"] else 0
    verdict = "PASS" if not reasons else "FAIL"
    report["verdict"] = verdict
    report["reasons"] = reasons
    if n_distinct < STOP_POSES:
        report["stop_rule"] = (f"distinct poses {n_distinct} < {STOP_POSES}: T-LAND stop rule -> fix capture "
                               f"settings (exposure / blur) before anything else")
        log(f"[calib] STOP RULE: {report['stop_rule']}")
    report["frames"] = [{k: v for k, v in f.items() if not k.startswith("_")} for f in frames]

    with open(os.path.join(out, "calib_report.json"), "w") as f:
        json.dump(report, f, indent=1)
    cols = ["stem", "file", "kind", "w", "h", "corners", "markers", "coverage", "lapvar_frame", "lapvar_board",
            "used", "reject", "distinct", "reproj_px", "distance_mm", "error"]
    with open(os.path.join(out, "frames.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for fr in report["frames"]:
            wr.writerow(fr)
    if not a.no_sheet:
        cal = report["calibration"]
        header = [f"IN-AIR ENGINEERING CHECK  {os.path.basename(os.path.dirname(folder.rstrip('/')))}/"
                  f"{os.path.basename(folder.rstrip('/'))}  board {layout['squares_x']}x{layout['squares_y']} "
                  f"{'legacy' if layout['legacy'] else 'modern'} {a.square_mm:g}/{a.marker_mm:g} mm "
                  f"{a.dictionary}  OpenCV {cv2.__version__}",
                  f"{verdict}: distinct poses {n_distinct} (bar >= {PASS_POSES}), RMS "
                  f"{cal['rms_px']:.3f} px (bar < {PASS_RMS})" if cal else f"{verdict}: {'; '.join(reasons)}",
                  f"used {len(used)}/{len(frames)} frames; thumbnails SCALED to 480 px wide (not 1:1); "
                  f"green = ChArUco corners, orange = markers; lap = Laplacian var frame/board"]
        contact_sheet(frames, os.path.join(out, "contact_sheet.jpg"), header, cv2, np)
    log(f"[calib] VERDICT {verdict}{': ' + '; '.join(reasons) if reasons else ''}")
    log(f"[calib] wrote {out}/calib_report.json, frames.csv{', contact_sheet.jpg' if not a.no_sheet else ''}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
