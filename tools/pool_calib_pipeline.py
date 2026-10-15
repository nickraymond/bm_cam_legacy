#!/usr/bin/env python3
# filename: pool_calib_pipeline.py
# description: Repeatable underwater camera calibration from pool ChArUco frames: screen -> coverage -> 3-model calibration (held-out selection, bootstrap) -> full-frame + 1600x900 crop intrinsics -> before/after, scored against pre-registered bars.
"""
pool_calib_pipeline.py — repeatable UNDERWATER calibration of a bmcam (IMX708 Wide behind a flat
port) from ChArUco frames, Mac side. One command, one timestamped run folder.

Purpose
  Turn a pool session's calibration frames into intrinsics we can trust (or a clear "not yet,
  here is how to capture it"), scored against the bars pre-registered in
  docs/pool_calib_prereg_20261014.md BEFORE the 10/14 data was calibrated.

Stages (per camera)
  1. screen    decode, ChArUco detect (auto layout, from pool_calib_check.py), board-ROI blur
               (contrast-normalised Laplacian), exposure / clipping, motion-blur anisotropy,
               near-duplicate pose removal, board coverage -> PASS / WARN / FAIL + reasons
  2. coverage  heatmap of every used corner over the full frame, 8x5 grid incl. the 4 corner
               cells, radial reach, distance from each frame edge to the nearest corner
  3. calibrate standard-5 / rational-8 / fisheye-4; outlier views dropped (pre-registered rule);
               model chosen by 5-fold HELD-OUT reprojection RMS (not training RMS); bootstrap
               (views resampled) -> parameter spread and per-pixel ray uncertainty; model
               disagreement at the frame edges; underwater fx vs the in-air nominal;
               intrinsics for the full 4608x2592 frame AND the production 1600x900 crop
  4. pictures  before/after undistortion of (a) a calibration frame, undistorted with the fold
               model that did NOT see it (honest straight-line check on its board rows), and
               (b) a v3-card frame from the same session; full frame (SCALED) + production
               crop (1:1)
  5. bars      B1..B6 -> full-frame verdict, production-crop verdict, capture recipe on FAIL

Inputs
  cameras      NAME=PATH, PATH = a folder of *.jpg/*.jpeg/*.dng/*.bayer or a glob (quote it), e.g.
               bmcam003=~/NereusPool/20261014/bmcam003_20261014_175926/calibration
               nereus002='~/NereusPool/.../pool007_*/2026-10-14/exp_20261014T20[23]*_pool/captures/imx708/*.jpg'
               n6='~/NereusPool/.../exp_*_pool/captures/openmv_n6/openmv_n6_raw_*.bayer'
               Sidecars <stem>.metadata.json / <file>.rpicam.json / <stem>.json are read when present
               (ScalerCrop, LensPosition, ExposureTime, AnalogueGain, Lux; exposure_us, gain_db).
               *.bayer = OpenMV raw (nereus002 N6 / AE3): 8-bit mosaic, geometry from <stem>.json
               (width, height, cfa, bits, black_level, white_level). It bypasses the board ISP, so it
               is the preferred source for those cameras; demosaiced with OpenCV for detection.
  --card NAME=JPG   v3-card frame for the before/after (repeatable). Default for a folder input:
               the first <folder>/../loop/*_loop_01_still.jpg if it exists.
  --native-size WxH   the IMX708 full frame (default 4608x2592). Leave it at the default for any
               other camera: a frame of another size is what marks a run as non-IMX708 (B5c / crop
               verdict N/A, B6 N/A without --nominal-fx, "not pre-registered" label, generic recipe).
               Setting it to e.g. the N6's 1280x800 would score the N6 against the IMX708 bars unlabelled.
  --nominal-fx NAME=PX   in-air nominal fx (px of that camera's frames) for the B6 sanity bar
               (repeatable). Default: the IMX708 1964 px, but only for --native-size frames; any
               other frame size (e.g. the OpenMV N6 1280x800) gets B6 = N/A.
  Board defaults = calib.io CCT600400C (9x14, 40/30 mm, DICT_5X5_100), as pool_calib_check.py.

Outputs  <out-root>/<YYYYMMDD_HHMMSS>_<tag>/
  run_manifest.json  command, versions, git sha, prereg sha256, inputs, outputs + sizes
  pipeline.log       everything printed
  prereg_bars.json   the bars this run was scored against
  summary.json / summary.md   all cameras: bars, verdicts, intrinsics, recipe
  <camera>/frames.csv            one row per frame: metrics, PASS/WARN/FAIL, reasons, in_set
           screen_sheet.jpg      every frame, SCALED thumbnails, status colour + reason
           coverage.png / coverage.json
           models.json, per_view_errors.csv, models.png
           bootstrap.csv, bootstrap.png, uncertainty.png (ray uncertainty map + observations)
           intrinsics_full_<W>x<H>.json, intrinsics_crop_<w>x<h>.json   (OpenCV conventions)
           before_after/*.jpg   + before_after.json (straightness numbers)
           verdict.png          one-page picture for review

Example
  .venv/bin/python tools/pool_calib_pipeline.py \\
      bmcam003=~/NereusPool/20261014/bmcam003_20261014_175926/calibration \\
      bmcam004=~/NereusPool/20261014/bmcam004_20261014_190821/calibration \\
      --out-root ~/NereusPool/20261014_CALIB --tag pool1014

Exit codes
  0 every camera passes the full-frame bars; 3 the run completed but a bar FAILED (see summary);
  1 pipeline failure (no frames, nothing calibratable, missing outputs); 2 refused arguments.

Requirements (Mac): opencv-contrib-python-headless >= 4.7 (built/tested on 5.0.0), numpy, scipy,
  matplotlib; rawpy only for DNG input. Use the repo's main .venv (it has all of them).

Assumptions / known limitations
  - JPEG = Pi ISP output (same pixel grid as the DNG). The ISP applies no geometric correction
    on the IMX708 pipeline we use; if that ever changes, calibrate from DNG.
  - Crop intrinsics assume a pure pixel crop of the native frame (cx, cy shift by the offset;
    fx, fy and distortion unchanged). Frames that are not --native-size skip the crop (loud):
    B5c and the production-crop verdict are then N/A, not FAIL.
  - Pinhole + distortion behind a flat port is an approximation (refraction is non-central); it
    is valid near the working range it was calibrated at, in fresh water. Seawater (n ~1.340 vs
    1.333) scales the effective focal length by ~+0.5 %.
  - In-air nominal fx = 2.75 mm / 1.4 um = 1964 px (Camera Module 3 Wide datasheet), an
    ASSUMPTION: no in-air ChArUco set exists for these units. It is a 4608x2592 IMX708 focal
    length, so it is applied to --native-size frames only (other cameras: --nominal-fx).
  - Blur / motion thresholds are heuristics relative to the set median (see the prereg doc).
"""
import argparse
import csv
import glob
import hashlib
import json
import math
import os
import platform
import shlex
import subprocess
import sys
import time
from datetime import datetime
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool_calib_check as pcc  # noqa: E402  (board, layout auto-detect, Log, crop maths)
import pool_calib_models as pm  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREREG_DOC = os.path.join(REPO, "docs", "pool_calib_prereg_20261014.md")
NATIVE = (4608, 2592)
DEFAULT_CROP = (1504, 846, 1600, 900)
NOMINAL_FX_AIR = 2.75e-3 / 1.4e-6          # 1964.3 px, datasheet values (assumption)
NOMINAL_FX_SOURCE = "datasheet assumption"  # the pre-registered IMX708 value (B6 / summary.md wording)
IMAGE_EXT = (".jpg", ".jpeg", ".dng", ".bayer")
# Sensor CFA (top-left 2x2, row-major) -> OpenCV conversion. OpenCV names its Bayer codes by a
# different pixel, so a BGGR sensor is COLOR_BayerRG*; checked on synthetic mosaics in
# tests/test_pool_calib_pipeline.py::TestBayer.
BAYER_CV = {"BGGR": "RG", "GBRG": "GR", "RGGB": "BG", "GRBG": "GB"}

# Pre-registered bars and screen thresholds (docs/pool_calib_prereg_20261014.md). Changing a value
# here changes the bar: record why in the doc first.
PREREG = {
    "doc": "docs/pool_calib_prereg_20261014.md",
    "screen": {"min_corners": 12, "warn_corners": 30, "blur_fail_rel": 0.25, "blur_warn_rel": 0.5,
               "clip_fail": 0.10, "clip_warn": 0.01, "mean_lo": 40, "mean_hi": 220,
               "aniso_fail": 2.5, "aniso_warn": 1.5, "dup_deg": 5.0, "dup_frac": 0.10},
    "calib": {"folds": 5, "seed": 0, "bootstrap": 100, "tie_frac": 0.02, "outlier_rounds": 3},
    "bars": {"B1_min_poses": 15, "B2_heldout_rms_px": 1.0, "B3_grid": [8, 5], "B3_min_obs_per_cell": 5,
             "B3_min_cover": 0.60, "B4_edge_ray_px": 2.0, "B4_edge_models_px": 2.0,
             "B5_rel_std_f": 0.005, "B5_std_c_px": 10.0, "B5c_crop_ray_px": 0.5, "B5c_crop_models_px": 1.0,
             "B6_ratio": [1.25, 1.42], "B6_nominal_fx_air_px": NOMINAL_FX_AIR},
}

STATUS_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}


# ============================================================================ helpers

def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if not math.isfinite(f) else f
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(jsonable(obj), f, indent=1)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_info():
    try:
        sha = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", REPO, "status", "--porcelain", "--", "tools", "docs"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return {"sha": sha or None, "tools_or_docs_dirty": bool(dirty)}
    except Exception as exc:
        return {"sha": None, "error": str(exc)}


def board_ns(spec):
    return SimpleNamespace(dictionary=spec["dictionary"], square_mm=spec["square_mm"], marker_mm=spec["marker_mm"])


def build_board(spec):
    import cv2
    return pcc.make_board(spec["squares_x"], spec["squares_y"], board_ns(spec), cv2, spec["legacy"])


# ============================================================================ inputs

def resolve_frames(path):
    """NAME=PATH -> sorted list of image paths. A folder is read flat; a glob is expanded."""
    p = os.path.expanduser(path)
    if any(ch in p for ch in "*?["):
        files = glob.glob(p, recursive=True)
    elif os.path.isdir(p):
        files = [os.path.join(p, f) for f in os.listdir(p)]
    else:
        files = [p] if os.path.isfile(p) else []
    files = [f for f in files if f.lower().endswith(IMAGE_EXT) and "_compressed" not in os.path.basename(f)]
    by_stem = {}
    for f in sorted(files):
        stem = os.path.splitext(os.path.basename(f))[0]
        # one pose per stem; a JPEG beats its DNG twin unless only the DNG exists
        if stem not in by_stem or by_stem[stem].lower().endswith(".dng"):
            by_stem[stem] = f
    return [by_stem[s] for s in sorted(by_stem)]


def load_bayer(path, color=False):
    """OpenMV raw (*.bayer + <stem>.json) -> 8-bit gray (or BGR). Raises ValueError on a bad file."""
    import cv2
    meta_path = os.path.splitext(path)[0] + ".json"
    with open(meta_path) as f:
        meta = json.load(f)
    w, h = int(meta["width"]), int(meta["height"])
    cfa, bits = str(meta.get("cfa", "")).upper(), int(meta.get("bits", 8))
    if cfa not in BAYER_CV:
        raise ValueError(f"unknown cfa {cfa!r} in {os.path.basename(meta_path)}")
    if bits != 8:
        raise ValueError(f"{bits}-bit bayer not supported (8-bit only)")
    raw = np.fromfile(path, np.uint8)
    if raw.size != w * h:
        raise ValueError(f"{raw.size} bytes, expected {w}x{h} = {w * h}")
    raw = raw.reshape(h, w)
    black, white = float(meta.get("black_level", 0)), float(meta.get("white_level", 255))
    if (black, white) != (0.0, 255.0):
        raw = np.clip((raw.astype(np.float32) - black) * 255.0 / max(white - black, 1.0), 0, 255).astype(np.uint8)
    code = getattr(cv2, f"COLOR_Bayer{BAYER_CV[cfa]}2{'BGR' if color else 'GRAY'}")
    return cv2.cvtColor(raw, code)


def load_image(path, color=False):
    """Any supported input -> 8-bit gray (or BGR); None if OpenCV cannot decode it."""
    import cv2
    low = path.lower()
    if low.endswith(".bayer"):
        return load_bayer(path, color)
    if low.endswith(".dng"):
        g = pcc.load_gray(path, "dng", cv2, np)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR) if color else g
    return cv2.imread(path, cv2.IMREAD_COLOR if color else cv2.IMREAD_GRAYSCALE)


def read_sidecar(path):
    stem = os.path.splitext(path)[0]
    for cand in (stem + ".metadata.json", path + ".rpicam.json", stem + ".json"):
        if os.path.isfile(cand):
            try:
                with open(cand) as f:
                    d = json.load(f)
                if isinstance(d, list) and d:
                    d = d[0]
                return {k: d.get(k) for k in ("ScalerCrop", "LensPosition", "ExposureTime", "AnalogueGain",
                                              "Lux", "FocusFoM", "exposure_us", "gain_db")}, os.path.basename(cand)
            except Exception as exc:
                return {"sidecar_error": str(exc)}, os.path.basename(cand)
    return {}, None


def default_card(path):
    p = os.path.expanduser(path)
    if not os.path.isdir(p):
        return None
    hits = sorted(glob.glob(os.path.join(os.path.dirname(p.rstrip("/")), "loop", "*_loop_01_still.jpg")))
    return hits[0] if hits else None


# ============================================================================ stage 1: screen

def square_px_and_axes(pts, ids, inner_cols):
    """Median checker square size (px) and the board's u (along a row) / v (down a column) unit
    axes in the image, from neighbouring ChArUco corners."""
    pos = {int(i): p for i, p in zip(ids, pts)}
    du, dv = [], []
    for i, p in pos.items():
        if i % inner_cols != inner_cols - 1 and (i + 1) in pos:
            du.append(pos[i + 1] - p)
        if (i + inner_cols) in pos:
            dv.append(pos[i + inner_cols] - p)
    lens = [np.linalg.norm(d) for d in du + dv]
    sq = float(np.median(lens)) if lens else None

    def unit(ds):
        if not ds:
            return None
        m = np.mean(ds, axis=0)
        n = np.linalg.norm(m)
        return m / n if n > 1e-9 else None
    return sq, unit(du), unit(dv)


def board_metrics(gray, pts, ids, inner_cols):
    """Board-ROI metrics: blur (contrast-normalised Laplacian variance), exposure, clipping,
    motion anisotropy (p99 edge gradient along the two board axes), coverage."""
    import cv2
    h, w = gray.shape
    sq, u, v = square_px_and_axes(pts, ids, inner_cols)
    hull = cv2.convexHull(pts.astype(np.float32).reshape(-1, 1, 2))
    pad = int(round(0.5 * sq)) if sq else 8
    x, y, bw, bh = cv2.boundingRect(hull)
    x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(w, x + bw + pad), min(h, y + bh + pad)
    roi = gray[y0:y1, x0:x1]
    mask = np.zeros(roi.shape, np.uint8)
    cv2.fillConvexPoly(mask, (hull.reshape(-1, 2) - [x0, y0]).astype(np.int32), 255)
    if pad:
        mask = cv2.dilate(mask, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8))
    m = mask > 0
    vals = roi[m].astype(np.float64)
    lap = cv2.Laplacian(roi, cv2.CV_64F)[m]
    var = float(vals.var())
    out = {"board_mean": float(vals.mean()), "clip_hi": float(np.mean(vals >= 250)),
           "clip_lo": float(np.mean(vals <= 5)), "lap_board": float(lap.var()),
           "lap_norm": float(lap.var() / var) if var > 1e-6 else 0.0, "square_px": sq,
           "coverage": float(cv2.contourArea(hull)) / float(w * h),
           "centroid_x": float(pts[:, 0].mean()), "centroid_y": float(pts[:, 1].mean()),
           "aniso": None}
    if u is not None and v is not None:
        gx = cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=3)[m]
        gy = cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=3)[m]
        nu, nv = (-u[1], u[0]), (-v[1], v[0])        # normals of the edges that run along u / v
        eu = float(np.percentile(np.abs(gx * nu[0] + gy * nu[1]), 99))
        ev = float(np.percentile(np.abs(gx * nv[0] + gy * nv[1]), 99))
        out.update({"edge_u": eu, "edge_v": ev, "aniso": max(eu, ev) / max(min(eu, ev), 1e-9)})
    return out


def screen_frame(job):
    """Worker: one file -> detection + metrics. Never raises (errors become a FAIL reason)."""
    path, spec, thumb_w = job
    import cv2
    cv2.setNumThreads(1)
    out = {"file": os.path.basename(path), "path": path, "stem": os.path.splitext(os.path.basename(path))[0],
           "corners": 0, "markers": 0}
    try:
        side, side_name = read_sidecar(path)
        out.update(side)
        out["sidecar"] = side_name
        gray = load_image(path)
        if gray is None:
            out["error"] = "decode failed"
            return out
        h, w = gray.shape
        out.update({"w": w, "h": h, "frame_mean": float(gray.mean())})
        s = thumb_w / float(w)
        out["_thumb"] = cv2.resize(gray, (thumb_w, int(round(h * s))), interpolation=cv2.INTER_AREA)
        out["_scale"] = s
        board = build_board(spec)
        det = cv2.aruco.CharucoDetector(board)
        cc, ci, mc, mi = det.detectBoard(gray)
        out["corners"] = 0 if ci is None else int(len(ci))
        out["markers"] = 0 if mi is None else int(len(mi))
        if out["corners"]:
            out["_corners"] = cc.reshape(-1, 2).astype(np.float64)
            out["_ids"] = ci.ravel().astype(int)
        if out["corners"] >= 4:
            out["collinear"] = bool(board.checkCharucoCornersCollinear(ci))
            out.update(board_metrics(gray, out["_corners"], out["_ids"], spec["squares_x"] - 1))
    except Exception as exc:          # one bad file must not kill the run
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def run_screen_workers(paths, spec, workers, thumb_w, log):
    jobs = [(p, spec, thumb_w) for p in paths]
    t0 = time.time()
    res = []
    if workers <= 1:
        for i, j in enumerate(jobs, 1):
            res.append(screen_frame(j))
            if i % 20 == 0 or i == len(jobs):
                log(f"[screen]   {i}/{len(jobs)} frames ({time.time() - t0:.0f}s)")
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, r in enumerate(ex.map(screen_frame, jobs, chunksize=2), 1):
                res.append(r)
                if i % 20 == 0 or i == len(jobs):
                    log(f"[screen]   {i}/{len(jobs)} frames ({time.time() - t0:.0f}s)")
    return res


def classify_frames(frames, sc):
    """Per-frame PASS/WARN/FAIL from the pre-registered thresholds (relative blur vs the set median)."""
    det = [f["lap_norm"] for f in frames if f.get("corners", 0) >= sc["min_corners"] and f.get("lap_norm")]
    med_lap = float(np.median(det)) if det else None
    for f in frames:
        fails, warns = [], []
        if f.get("error"):
            fails.append(f"read: {f['error']}")
        elif f["corners"] < sc["min_corners"]:
            fails.append(f"{f['corners']} ChArUco corners < {sc['min_corners']}" +
                         (" (markers seen)" if f["markers"] else " (no board)"))
        elif f.get("collinear"):
            fails.append("corners collinear")
        else:
            if f["corners"] < sc["warn_corners"]:
                warns.append(f"{f['corners']} corners < {sc['warn_corners']}")
            rel = f["lap_norm"] / med_lap if med_lap else None
            f["blur_rel"] = rel
            if rel is not None and rel < sc["blur_fail_rel"]:
                fails.append(f"blurred: board sharpness {rel:.2f}x the set median")
            elif rel is not None and rel < sc["blur_warn_rel"]:
                warns.append(f"soft: board sharpness {rel:.2f}x the set median")
            if f["clip_hi"] > sc["clip_fail"]:
                fails.append(f"clipped: {100 * f['clip_hi']:.1f}% of board pixels >= 250")
            elif f["clip_hi"] > sc["clip_warn"]:
                warns.append(f"clipping {100 * f['clip_hi']:.1f}% of board pixels")
            if f["board_mean"] < sc["mean_lo"] or f["board_mean"] > sc["mean_hi"]:
                warns.append(f"board mean {f['board_mean']:.0f} outside {sc['mean_lo']}-{sc['mean_hi']}")
            a = f.get("aniso")
            if a is not None and a > sc["aniso_fail"]:
                fails.append(f"motion blur: edge anisotropy {a:.2f}")
            elif a is not None and a > sc["aniso_warn"]:
                warns.append(f"possible motion: edge anisotropy {a:.2f}")
            sc_crop = str(f.get("ScalerCrop") or "")
            if sc_crop and "/" in sc_crop and not sc_crop.startswith("(0, 0)/"):
                warns.append(f"ScalerCrop {sc_crop} is not the full sensor")
        f["status"] = "FAIL" if fails else ("WARN" if warns else "PASS")
        f["reasons"] = fails + warns
    return med_lap


def board_points(board, frames):
    obj, img = [], []
    for f in frames:
        o, p = board.matchImagePoints(f["_corners"].reshape(-1, 1, 2).astype(np.float32),
                                      f["_ids"].reshape(-1, 1).astype(np.int32))
        obj.append(o.reshape(-1, 3).astype(np.float64))
        img.append(p.reshape(-1, 2).astype(np.float64))
    return obj, img


def pose_summary(rvec, tvec, board_center):
    import cv2
    R, _ = cv2.Rodrigues(np.asarray(rvec, float).reshape(3, 1))
    c = R @ board_center + np.asarray(tvec, float).reshape(3)
    tilt = math.degrees(math.acos(min(1.0, abs(float(R[2, 2])))))
    return R, c, float(np.linalg.norm(c)), tilt


def dedupe(frames, rvecs, tvecs, board_center, deg, frac):
    """Greedy near-duplicate removal; the frame with more corners (then sharper) is kept."""
    info = []
    for f, rv, tv in zip(frames, rvecs, tvecs):
        R, c, dist, tilt = pose_summary(rv, tv, board_center)
        f["distance_mm"], f["tilt_deg"] = dist, tilt
        info.append((f, R, c))
    order = sorted(info, key=lambda t: (-t[0]["corners"], -(t[0].get("lap_norm") or 0)))
    kept = []
    for f, R, c in order:
        dup = None
        for g, R2, c2 in kept:
            ang = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R @ R2.T) - 1) / 2))))
            if ang < deg and np.linalg.norm(c - c2) < frac * max(np.linalg.norm(c), 1e-9):
                dup = g
                break
        if dup is None:
            kept.append((f, R, c))
        else:
            f["dup_of"] = dup["stem"]
            f["reasons"].append(f"near-duplicate pose of {dup['stem']} (excluded)")
            if f["status"] == "PASS":
                f["status"] = "WARN"
    return [k[0] for k in kept]


# ============================================================================ stage 2: coverage

CELL_NAMES = {(0, 0): "top-left corner", (0, -1): "top-right corner", (-1, 0): "bottom-left corner",
              (-1, -1): "bottom-right corner"}


def coverage_stats(pts, size, grid=(8, 5), min_obs=5, crop=None):
    """Corner observations (N,2) native px -> grid counts, covered cells, corner cells, radial
    reach and the gap from each frame edge to the nearest observation."""
    w, h = size
    gx, gy = grid
    pts = np.asarray(pts, float).reshape(-1, 2)
    counts = np.zeros((gy, gx), int)
    if len(pts):
        ix = np.clip((pts[:, 0] * gx / w).astype(int), 0, gx - 1)
        iy = np.clip((pts[:, 1] * gy / h).astype(int), 0, gy - 1)
        np.add.at(counts, (iy, ix), 1)
    covered = counts >= min_obs
    corners = {name: bool(covered[r, c]) for (r, c), name in CELL_NAMES.items()}
    c0 = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    R = math.hypot(w / 2.0, h / 2.0)
    rn = np.linalg.norm(pts - c0, axis=1) / R if len(pts) else np.zeros(0)
    out = {"grid": [gx, gy], "min_obs_per_cell": min_obs, "counts": counts.tolist(),
           "covered": covered.tolist(), "covered_frac": float(covered.mean()),
           "corner_cells_covered": corners, "all_corner_cells": all(corners.values()),
           "n_obs": int(len(pts)),
           "radial_norm": {"note": "distance from the frame centre / half-diagonal (frame corner = 1.0)",
                           "max": float(rn.max()) if len(rn) else 0.0,
                           "p95": float(np.percentile(rn, 95)) if len(rn) else 0.0,
                           "frac_gt_0.6": float(np.mean(rn > 0.6)) if len(rn) else 0.0,
                           "frac_gt_0.8": float(np.mean(rn > 0.8)) if len(rn) else 0.0}}
    if len(pts):
        out["edge_gap_px"] = {"left": float(pts[:, 0].min()), "right": float(w - 1 - pts[:, 0].max()),
                              "top": float(pts[:, 1].min()), "bottom": float(h - 1 - pts[:, 1].max())}
        fc = {"top-left": (0, 0), "top-right": (w - 1, 0), "bottom-left": (0, h - 1), "bottom-right": (w - 1, h - 1)}
        out["corner_gap_px"] = {k: float(np.min(np.linalg.norm(pts - np.array(v, float), axis=1)))
                                for k, v in fc.items()}
    if crop:
        x, y, cw, ch = crop
        cr = math.hypot(cw / 2.0, ch / 2.0) / R
        inside = (pts[:, 0] >= x) & (pts[:, 0] < x + cw) & (pts[:, 1] >= y) & (pts[:, 1] < y + ch) if len(pts) else []
        out["crop"] = {"xywh": list(crop), "corner_radial_norm": float(cr),
                       "obs_inside": int(np.sum(inside)) if len(pts) else 0}
    return out


# ============================================================================ stage 3: calibrate

def probe_points(size, crop):
    w, h = size
    frame = {"TL": (0, 0), "TR": (w - 1, 0), "BL": (0, h - 1), "BR": (w - 1, h - 1),
             "top": ((w - 1) / 2, 0), "bottom": ((w - 1) / 2, h - 1), "left": (0, (h - 1) / 2),
             "right": (w - 1, (h - 1) / 2)}
    out = {"frame": frame}
    if crop:
        x, y, cw, ch = crop
        out["crop"] = {"crop_TL": (x, y), "crop_TR": (x + cw - 1, y), "crop_BL": (x, y + ch - 1),
                       "crop_BR": (x + cw - 1, y + ch - 1)}
    return out


def calibrate_camera(obj, img, size, cfg, workers, log, crop):
    """All three models + held-out CV + bootstrap of the chosen model. -> dict."""
    fits, cvs, errors = {}, {}, {}
    t0 = time.time()
    base, _, _ = pm.fit_robust("standard", obj, img, size, rounds=cfg["outlier_rounds"])
    K0 = base["K"]
    for model in pm.MODELS:
        try:
            f, kept, dropped = pm.fit_robust(model, obj, img, size, K0=K0, rounds=cfg["outlier_rounds"])
            f["kept"], f["dropped"] = kept, dropped
            fits[model] = f
            log(f"[calib]   {model:9s} train RMS {f['rms']:.3f} px on {len(kept)} views "
                f"({len(dropped)} outlier view(s) dropped) fx {f['K'][0, 0]:.1f} fy {f['K'][1, 1]:.1f} "
                f"cx {f['K'][0, 2]:.1f} cy {f['K'][1, 2]:.1f}")
        except Exception as exc:
            errors[model] = f"fit: {type(exc).__name__}: {exc}"
            log(f"[calib]   {model:9s} FIT FAILED: {exc}")
            continue
        try:
            cvs[model] = pm.cross_validate(model, obj, img, size, folds=cfg["folds"], seed=cfg["seed"],
                                           keep_fold_models=True)
            log(f"[calib]   {model:9s} HELD-OUT RMS {cvs[model]['held_out_rms']:.3f} px "
                f"({cfg['folds']}-fold, median view {cvs[model]['held_out_median_view']:.3f} px)")
        except Exception as exc:
            errors[model] = f"cv: {type(exc).__name__}: {exc}"
            log(f"[calib]   {model:9s} CV FAILED: {exc}")
    chosen, why = pm.choose_model(cvs, cfg["tie_frac"])
    log(f"[calib]   chosen: {chosen} ({why}); fits + CV {time.time() - t0:.0f}s")
    if chosen is None:
        return {"fits": fits, "cv": cvs, "errors": errors, "chosen": None, "why": why}
    final = fits[chosen]
    kept = final["kept"]
    t1 = time.time()
    boots = pm.bootstrap(chosen, [obj[i] for i in kept], [img[i] for i in kept], size,
                         B=cfg["bootstrap"], seed=cfg["seed"], K0=final["K"], workers=workers)
    good = [b for b in boots if "error" not in b]
    log(f"[calib]   bootstrap {len(good)}/{len(boots)} replicates OK ({time.time() - t1:.0f}s)")
    f_ref = 0.5 * (final["K"][0, 0] + final["K"][1, 1])
    Ks, Ds = [b["K"] for b in good], [b["D"] for b in good]
    n_failed = len(boots) - len(good)                 # failed replicates count as inf (QE #176)
    probes = probe_points(size, crop)
    others = [fits[m] for m in pm.MODELS if m in fits and m != chosen]
    gx, gy = 64, 36
    xs = np.linspace(0, size[0] - 1, gx)
    ys = np.linspace(0, size[1] - 1, gy)
    grid = np.stack(np.meshgrid(xs, ys), axis=-1).reshape(-1, 2)
    fit_px = observed_region_points(np.concatenate([img[i] for i in kept], axis=0), grid)
    log(f"[calib]   alignment region (amendment A1): {len(fit_px)} grid points inside the observed hull")
    probe_rows = []
    for group, pts in probes.items():
        names = list(pts)
        arr = np.array([pts[n] for n in names], float)
        none = np.full(len(arr), np.inf)
        raw = pm.ray_spread_px(chosen, Ks, Ds, arr, f_ref, n_failed=n_failed) if Ks else none
        al = (pm.aligned_ray_spread_px(chosen, Ks, Ds, final["K"], final["D"], fit_px, arr, f_ref, n_failed=n_failed)
              if Ks else none)
        dis_raw = pm.model_disagreement_px([final] + others, arr, f_ref) if others else np.zeros(len(arr))
        dis_al = pm.aligned_model_disagreement_px([final] + others, fit_px, arr, f_ref) if others else np.zeros(len(arr))
        disp = np.linalg.norm(pm.pinhole_px(final["K"], pm.rays(chosen, final["K"], final["D"], arr)) - arr, axis=1)
        for n, p, a_, r_, da, dr, dp in zip(names, arr, al, raw, dis_al, dis_raw, disp):
            probe_rows.append({"group": group, "name": n, "x": float(p[0]), "y": float(p[1]),
                               "ray_uncertainty_px": float(a_), "model_disagreement_px": float(da),
                               "ray_uncertainty_raw_px": float(r_), "model_disagreement_raw_px": float(dr),
                               "distortion_px": float(dp) if np.isfinite(dp) else float("inf")})
    # uncertainty map for the picture (coarse grid), aligned (A1)
    umap = (pm.aligned_ray_spread_px(chosen, Ks, Ds, final["K"], final["D"], fit_px, grid, f_ref,
                                     n_failed=n_failed).reshape(gy, gx) if Ks else None)
    dmap = np.linalg.norm(pm.pinhole_px(final["K"], pm.rays(chosen, final["K"], final["D"], grid)) - grid,
                          axis=1).reshape(gy, gx)
    stats = {"replicates_ok": len(good), "replicates": len(boots)}
    if good:
        P = np.array([[b["K"][0, 0], b["K"][1, 1], b["K"][0, 2], b["K"][1, 2]] for b in good])
        for i, k in enumerate(("fx", "fy", "cx", "cy")):
            stats[k] = {"mean": float(P[:, i].mean()), "std": float(P[:, i].std(ddof=1)) if len(P) > 1 else None,
                        "p2.5": float(np.percentile(P[:, i], 2.5)), "p97.5": float(np.percentile(P[:, i], 97.5))}
        Dm = np.array([b["D"] for b in good])
        stats["dist"] = {"mean": Dm.mean(axis=0).tolist(), "std": Dm.std(axis=0, ddof=1).tolist() if len(Dm) > 1 else None}
    return {"fits": fits, "cv": cvs, "errors": errors, "chosen": chosen, "why": why, "final": final,
            "bootstrap": boots, "boot_stats": stats, "f_ref": f_ref, "probes": probe_rows,
            "cross_model_K": pm.cross_model_K([final] + others),
            "uncertainty_map": {"xs": xs, "ys": ys, "spread_px": umap, "distortion_px": dmap},
            "alignment_region_points": int(len(fit_px))}


def observed_region_points(obs_px, grid_px):
    """Grid points inside the convex hull of the corner observations (where the data constrain
    the distortion); used to fit the A1 alignment homographies."""
    import cv2
    hull = cv2.convexHull(np.asarray(obs_px, np.float32).reshape(-1, 1, 2))
    inside = [cv2.pointPolygonTest(hull, (float(x), float(y)), False) >= 0 for x, y in grid_px]
    return np.asarray(grid_px, float)[np.array(inside, bool)]


def crop_K(K, size, crop, native):
    """Full-frame K -> K of the production crop (pure pixel crop: cx -= x, cy -= y; fx, fy and the
    distortion unchanged). Same maths as pool_calib_check.crop_intrinsics, with the native size as a
    parameter (that one is fixed to 4608x2592)."""
    x, y, w, h = crop
    if tuple(size) != tuple(native):
        return None, f"frames are {size[0]}x{size[1]}, not native {native[0]}x{native[1]}: crop derivation skipped"
    if x < 0 or y < 0 or x + w > native[0] or y + h > native[1]:
        return None, f"crop {crop} exceeds native {native}"
    Kc = np.array(K, float).copy()
    Kc[0, 2] -= x
    Kc[1, 2] -= y
    return {"camera_matrix": Kc.tolist(), "image_size": [w, h],
            "note": "pure pixel crop: cx, cy shifted by the crop offset; fx, fy, distortion unchanged"}, None


def intrinsics_doc(model, K, D, size, extra=None):
    d = {"model": model, "opencv_api": ("cv2.fisheye.*" if model == "fisheye" else
                                        "cv2.undistort / cv2.initUndistortRectifyMap"),
         "dist_coeffs_order": {"standard": "k1 k2 p1 p2 k3", "rational": "k1 k2 p1 p2 k3 k4 k5 k6",
                               "fisheye": "k1 k2 k3 k4"}[model],
         "image_size": list(size), "camera_matrix": np.asarray(K).tolist(),
         "fx": float(K[0][0]), "fy": float(K[1][1]), "cx": float(K[0][2]), "cy": float(K[1][2]),
         "dist_coeffs": np.asarray(D).ravel().tolist(),
         "coordinates": "native sensor pixels of the frame given by image_size; pixel centres at integers"}
    if extra:
        d.update(extra)
    return d


# ============================================================================ stage 5: bars

def nominal_fx_for(name, size, native, given):
    """In-air nominal fx for B6 -> {"fx_px": float or None, "source": str}. --nominal-fx NAME=PX
    wins; else the pre-registered IMX708 value, but only for --native-size frames (it is a focal
    length in 4608x2592 IMX708 pixels: meaningless for another sensor, e.g. the N6) -> else None."""
    if name in given:
        return {"fx_px": float(given[name]), "source": "--nominal-fx"}
    if tuple(size) == tuple(native):
        return {"fx_px": PREREG["bars"]["B6_nominal_fx_air_px"], "source": NOMINAL_FX_SOURCE}
    return {"fx_px": None,
            "source": f"the IMX708 {NOMINAL_FX_AIR:.0f} px is for {native[0]}x{native[1]} frames only; "
                      f"pass --nominal-fx {name}=PX"}


def evaluate_bars(n_poses, cal, cov, bars, crop_skip=None, nominal=None):
    """B1..B6 -> (rows, full-frame verdict, production-crop verdict).
    crop_skip  None, or why the production crop was not derived (frames not --native-size): B5c
               and the crop verdict are then N/A (nothing to score), not FAIL.
    nominal    nominal_fx_for(...) for B6; default = the pre-registered IMX708 value. fx_px None ->
               B6 N/A. N/A rows carry "pass": None and never count toward a verdict."""
    out = []
    nominal = nominal or {"fx_px": bars["B6_nominal_fx_air_px"], "source": NOMINAL_FX_SOURCE}

    def add(bar, value, rule, ok, severity="FAIL"):
        out.append({"bar": bar, "value": value, "rule": rule, "pass": bool(ok),
                    "status": "PASS" if ok else severity})

    def not_applicable(bar, why, rule):
        out.append({"bar": bar, "value": why, "rule": rule, "pass": None, "status": "N/A"})
    add("B1 poses", n_poses, f">= {bars['B1_min_poses']} distinct usable poses", n_poses >= bars["B1_min_poses"])
    ch = cal.get("chosen")
    ho = cal["cv"][ch]["held_out_rms"] if ch else float("nan")
    add("B2 held-out RMS", ho, f"< {bars['B2_heldout_rms_px']} px ({ch})", ch is not None and ho < bars["B2_heldout_rms_px"])
    add("B3 coverage", {"covered_frac": cov["covered_frac"], "corner_cells": cov["corner_cells_covered"]},
        f">= {100 * bars['B3_min_cover']:.0f}% of {bars['B3_grid'][0]}x{bars['B3_grid'][1]} cells and all 4 corner cells",
        cov["covered_frac"] >= bars["B3_min_cover"] and cov["all_corner_cells"])
    fr = [p for p in cal.get("probes", []) if p["group"] == "frame"]
    cr = [p for p in cal.get("probes", []) if p["group"] == "crop"]
    worst_r = max((p["ray_uncertainty_px"] for p in fr), default=float("inf"))
    worst_m = max((p["model_disagreement_px"] for p in fr), default=float("inf"))
    lit = lambda rows, k: max((p[k] for p in rows), default=float("inf"))  # noqa: E731
    add("B4 edges constrained", {"worst_ray_uncertainty_px": worst_r, "worst_model_disagreement_px": worst_m,
                                 "literal_ray_px": lit(fr, "ray_uncertainty_raw_px"),
                                 "literal_models_px": lit(fr, "model_disagreement_raw_px")},
        f"ray uncertainty <= {bars['B4_edge_ray_px']} px and model disagreement <= {bars['B4_edge_models_px']} px "
        f"at 4 frame corners + 4 edge midpoints (homography-aligned, amendment A1)",
        bool(fr) and worst_r <= bars["B4_edge_ray_px"] and worst_m <= bars["B4_edge_models_px"])
    st = cal.get("boot_stats") or {}
    n_ok, n_b = st.get("replicates_ok", 0), st.get("replicates", 0)
    if st.get("fx") and st["fx"]["std"] is not None:
        rf = max(st["fx"]["std"] / st["fx"]["mean"], st["fy"]["std"] / st["fy"]["mean"])
        sc = max(st["cx"]["std"], st["cy"]["std"])
        # a resample whose fit fails counts as "unknown": more than 5 % failures = not stable (QE #176)
        ok5 = rf <= bars["B5_rel_std_f"] and sc <= bars["B5_std_c_px"] and n_ok >= 0.95 * n_b
    else:
        rf = sc = None
        ok5 = False
    add("B5 stable", {"rel_std_f": rf, "std_c_px": sc, "replicates_ok": f"{n_ok}/{n_b}"},
        f"bootstrap std(f)/f <= {100 * bars['B5_rel_std_f']:.1f}% and std(cx, cy) <= {bars['B5_std_c_px']} px "
        f"(>= 95% of replicates fit)", ok5)
    crop_rule = (f"ray uncertainty <= {bars['B5c_crop_ray_px']} px and model disagreement <= "
                 f"{bars['B5c_crop_models_px']} px at the 4 crop corners (homography-aligned, amendment A1)")
    if crop_skip:
        not_applicable("B5c production crop", crop_skip, crop_rule)
    else:
        wr = max((p["ray_uncertainty_px"] for p in cr), default=float("inf"))
        wm = max((p["model_disagreement_px"] for p in cr), default=float("inf"))
        add("B5c production crop", {"worst_ray_uncertainty_px": wr, "worst_model_disagreement_px": wm,
                                    "literal_ray_px": lit(cr, "ray_uncertainty_raw_px"),
                                    "literal_models_px": lit(cr, "model_disagreement_raw_px")},
            crop_rule, bool(cr) and wr <= bars["B5c_crop_ray_px"] and wm <= bars["B5c_crop_models_px"])
    if ch:
        lo, hi = bars["B6_ratio"]
        nom = nominal["fx_px"]
        if nom:
            ratio = float(cal["final"]["K"][0, 0]) / nom
            src = "" if nominal["source"] == NOMINAL_FX_SOURCE else f", {nominal['source']}"
            add("B6 focal sanity", ratio, f"fx / {nom:.0f} px (in-air nominal{src}) in [{lo}, {hi}]",
                lo <= ratio <= hi, severity="WARN")
        else:
            not_applicable("B6 focal sanity", nominal["source"], f"fx / in-air nominal in [{lo}, {hi}]")
    by = {b["bar"].split()[0]: b["pass"] for b in out}
    verdict_full = all(by.get(k, False) for k in ("B1", "B2", "B3", "B4", "B5"))
    verdict_crop = all(by.get(k, False) for k in ("B1", "B2", "B5", "B5c"))
    return (out, ("PASS" if verdict_full else "FAIL"),
            "N/A" if crop_skip else ("PASS" if verdict_crop else "FAIL"))


def capture_recipe(cov, frames_used, fx, size, square_mm, squares, bars_out, all_frames=(), native_frames=True):
    """Data-driven capture advice for the next dive. Distances use the pinhole approximation.
    native_frames False (not --native-size, e.g. the N6): the bmcam-only lines (1600x900 crop-only
    pool preview, production LensPosition 0.8) are replaced by camera-agnostic wording."""
    w, h = size
    long_mm = max(squares) * square_mm
    dists = [f["distance_mm"] for f in frames_used if f.get("distance_mm")]
    med_d = float(np.median(dists)) if dists else None
    frame_w_at = (lambda d: w * d / fx) if fx else None
    lines = []
    failed = [b["bar"] for b in bars_out if b["status"] == "FAIL"]
    if med_d and frame_w_at:
        fw = frame_w_at(med_d)
        lines.append(f"This session: board at a median {med_d / 1000:.2f} m (range {min(dists) / 1000:.2f}-"
                     f"{max(dists) / 1000:.2f} m); there the frame is {fw / 1000:.2f} m wide, so the "
                     f"{long_mm:.0f} mm pattern filled only {100 * long_mm / fw:.0f}% of the width.")
    gaps = cov.get("edge_gap_px")
    if gaps:
        lines.append("Closest corner to each frame edge: " + ", ".join(
            f"{k} {v:.0f} px ({100 * v / (w if k in ('left', 'right') else h):.0f}%)" for k, v in gaps.items()) + ".")
    miss = [k for k, v in cov["corner_cells_covered"].items() if not v]
    if miss:
        lines.append(f"Empty corner cells: {', '.join(miss)}.")
    if fx:
        d_rec = long_mm * fx / (0.45 * w)
        lines.append(f"Next dive: hold the board at ~{d_rec / 1000:.1f}-{1.3 * d_rec / 1000:.1f} m (pattern ~35-45% of "
                     f"the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, "
                     f"with a board corner touching the frame corner/edge each time.")
    lines.append("At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), "
                 "plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).")
    lines.append("Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, "
                 "so the frame edges were invisible while posing." if native_frames else
                 "Frame with a FULL-FRAME preview, so the frame edges are visible while posing.")
    n_clip = sum(1 for f in all_frames if any(r.startswith("clipped") for r in f.get("reasons", [])))
    if n_clip:
        lines.append(f"{n_clip}/{len(all_frames)} frames failed on clipping (white squares at 255): lock exposure "
                     f"~1 stop darker for the calibration block, or tilt the board out of the sun glint.")
    lines.append("Keep the production lens position (0.8) for calibration frames: focus changes the focal length "
                 "slightly, so calibrate at the focus you ship." if native_frames else
                 "Calibrate at the focus you ship: focus changes the focal length slightly.")
    return {"failed_bars": failed, "lines": lines,
            "recommended_distance_m": (long_mm * fx / (0.45 * w) / 1000) if fx else None,
            "median_distance_m": med_d / 1000 if med_d else None}


# ============================================================================ stage 4: pictures

def undistort_maps(model, K, D, size):
    import cv2
    K = np.asarray(K, float)
    D = np.asarray(D, float).ravel()
    if model == "fisheye":
        return cv2.fisheye.initUndistortRectifyMap(K, D.reshape(4, 1), np.eye(3), K, tuple(size), cv2.CV_32FC1)
    return cv2.initUndistortRectifyMap(K, D, None, K, tuple(size), cv2.CV_32FC1)


def straightness_report(model, K, D, ids, pts, inner_cols, inner_rows):
    lines = pm.board_lines(ids, pts, inner_cols, inner_rows)
    rows = []
    for kind, idx, p in lines:
        before = pm.line_straightness(p)
        after_pts = pm.pinhole_px(K, pm.rays(model, K, D, p))
        after = pm.line_straightness(after_pts)
        if before and after:
            rows.append({"line": f"{kind} {idx}", "n": len(p), "before_rms_px": before[0], "before_max_px": before[1],
                         "after_rms_px": after[0], "after_max_px": after[1], "_before_pts": p, "_after_pts": after_pts})
    agg = {}
    if rows:
        for k in ("before_rms_px", "after_rms_px", "before_max_px", "after_max_px"):
            agg[k] = {"median": float(np.median([r[k] for r in rows])), "max": float(np.max([r[k] for r in rows]))}
    return rows, agg


# ============================================================================ per camera

def process_camera(name, paths, spec, args, out_dir, crop, native, log, figs):
    os.makedirs(out_dir, exist_ok=True)
    sc, cfg, bars = PREREG["screen"], dict(PREREG["calib"]), PREREG["bars"]
    cfg["bootstrap"], cfg["folds"] = args.bootstrap, args.folds
    log(f"[{name}] {len(paths)} frame(s) -> {out_dir}")
    frames = run_screen_workers(paths, spec, args.workers, args.thumb_width, log)
    med_lap = classify_frames(frames, sc)
    sizes = sorted({(f["w"], f["h"]) for f in frames if f.get("w")})
    result = {"camera": name, "n_frames": len(frames), "median_lap_norm": med_lap, "frame_sizes": sizes}
    board = build_board(spec)
    cand = [f for f in frames if f["status"] != "FAIL"]
    if len(sizes) > 1:
        size = max(sizes, key=lambda s: sum(1 for f in cand if (f.get("w"), f.get("h")) == s))
        for f in cand:
            if (f["w"], f["h"]) != size:
                f["status"], f["reasons"] = "FAIL", f["reasons"] + [f"frame size {f['w']}x{f['h']} != {size}"]
        cand = [f for f in cand if f["status"] != "FAIL"]
        log(f"[{name}] WARN mixed frame sizes {sizes}: calibrating the {size} frames only")
    size = (cand[0]["w"], cand[0]["h"]) if cand else (sizes[0] if sizes else None)
    result["image_size"] = size
    n_status = {s: sum(1 for f in frames if f["status"] == s) for s in ("PASS", "WARN", "FAIL")}
    log(f"[{name}] screen (before dedupe): {n_status}; median board sharpness {med_lap}")
    if len(cand) < 4:
        result["error"] = f"only {len(cand)} frame(s) passed the screen; nothing to calibrate"
        log(f"[{name}] FAIL: {result['error']}")
        write_frames_csv(os.path.join(out_dir, "frames.csv"), frames)
        figs.screen_sheet(frames, os.path.join(out_dir, "screen_sheet.jpg"), name, args.thumb_width)
        return result
    # poses for dedupe from a first standard fit on every non-FAIL frame
    obj_all, img_all = board_points(board, cand)
    first = pm.fit("standard", obj_all, img_all, size)
    centre = np.array([spec["squares_x"] * spec["square_mm"] / 2.0, spec["squares_y"] * spec["square_mm"] / 2.0, 0.0])
    in_set = dedupe(cand, first["rvecs"], first["tvecs"], centre, sc["dup_deg"], sc["dup_frac"])
    in_set.sort(key=lambda f: f["stem"])
    set_ids = {id(f) for f in in_set}
    for f in frames:
        f["in_set"] = id(f) in set_ids
    n_status = {s: sum(1 for f in frames if f["status"] == s) for s in ("PASS", "WARN", "FAIL")}
    result["screen"] = {"status_counts": n_status, "distinct_set": len(in_set),
                        "duplicates_removed": sum(1 for f in frames if f.get("dup_of")),
                        "first_pass_standard_rms_px": first["rms"]}
    log(f"[{name}] screen: {n_status}; {len(in_set)} distinct poses in the calibration set "
        f"({result['screen']['duplicates_removed']} near-duplicates removed)")

    # stage 3
    obj, img = board_points(board, in_set)
    log(f"[{name}] calibrating {len(in_set)} views, {sum(len(o) for o in obj)} corners, image {size[0]}x{size[1]}")
    cal = calibrate_camera(obj, img, size, cfg, args.workers, log,
                           crop if tuple(size) == tuple(native) else None)
    result["calibration_error"] = cal.get("errors")
    if not cal.get("chosen"):
        result["error"] = cal["why"]
        write_frames_csv(os.path.join(out_dir, "frames.csv"), frames)
        return result
    final, ch = cal["final"], cal["chosen"]
    used = [in_set[i] for i in final["kept"]]
    for i, f in enumerate(in_set):
        f["calib_index"] = i
        f["outlier"] = i not in final["kept"]
        f["train_rms_px"] = None
        f["heldout_rms_px"] = float(cal["cv"][ch]["held_out_per_view"][i])
        if f["outlier"]:
            f["reasons"].append(f"outlier view in the {ch} fit (dropped)")
    for j, i in enumerate(final["kept"]):
        in_set[i]["train_rms_px"] = float(final["per_view"][j])
        R, c, dist, tilt = pose_summary(final["rvecs"][j], final["tvecs"][j], centre)
        in_set[i]["distance_mm"], in_set[i]["tilt_deg"] = dist, tilt

    # stage 2 (on the views that actually constrain the chosen fit)
    pts = np.concatenate([in_set[i]["_corners"] for i in final["kept"]], axis=0)
    cov = coverage_stats(pts, size, tuple(bars["B3_grid"]), bars["B3_min_obs_per_cell"],
                         crop if tuple(size) == tuple(native) else None)
    import cv2
    cov["observed_hull_px"] = cv2.convexHull(pts.astype(np.float32).reshape(-1, 1, 2)).reshape(-1, 2).tolist()
    write_json(os.path.join(out_dir, "coverage.json"), cov)
    log(f"[{name}] coverage: {100 * cov['covered_frac']:.0f}% of {bars['B3_grid'][0]}x{bars['B3_grid'][1]} cells; "
        f"corner cells {cov['corner_cells_covered']}; radial reach max {cov['radial_norm']['max']:.2f} "
        f"(frame corner = 1.00); edge gaps {({k: round(v) for k, v in cov.get('edge_gap_px', {}).items()})} px")

    # intrinsics
    K, D = final["K"], final["D"]
    hfov, vfov = pm.fov_deg(ch, K, D, size)
    nominal = nominal_fx_for(name, size, native, args.nominal_fxs)
    prereg_note = None
    if tuple(size) != tuple(native):
        prereg_note = (f"IMX708-pixel bars, not pre-registered for this sensor: the B1-B6 pixel numbers "
                       f"({PREREG['doc']}) are for {native[0]}x{native[1]} frames, applied unchanged to "
                       f"{size[0]}x{size[1]} frames")
        log(f"[{name}] WARN {prereg_note}")
    extra = {"rms_px_train": final["rms"], "heldout_rms_px": cal["cv"][ch]["held_out_rms"],
             "views": len(final["kept"]), "fov_deg_h_v_from_edge_rays": [hfov, vfov],
             "fx_over_nominal_in_air": float(K[0, 0]) / nominal["fx_px"] if nominal["fx_px"] else None,
             "nominal_fx_in_air_px": nominal["fx_px"], "nominal_fx_in_air_source": nominal["source"],
             "medium": "fresh water (pool), flat acrylic port",
             "bootstrap": cal["boot_stats"], "opencv_std_intrinsics": final.get("std_intrinsics")}
    full_doc = intrinsics_doc(ch, K, D, size, extra)
    write_json(os.path.join(out_dir, f"intrinsics_full_{size[0]}x{size[1]}.json"), full_doc)
    crop_doc = None
    ck, why = crop_K(K, size, crop, native)
    if ck:
        crop_doc = intrinsics_doc(ch, np.array(ck["camera_matrix"]), D, ck["image_size"],
                                  {"crop_native_xywh": list(crop), "note": ck["note"],
                                   "derived_from": f"intrinsics_full_{size[0]}x{size[1]}.json"})
        write_json(os.path.join(out_dir, f"intrinsics_crop_{crop[2]}x{crop[3]}.json"), crop_doc)
    else:
        log(f"[{name}] WARN crop intrinsics skipped: {why} (B5c and the production-crop verdict N/A)")
    if nominal["source"] != NOMINAL_FX_SOURCE:
        log(f"[{name}] B6 in-air nominal fx: " + (f"{nominal['fx_px']:.1f} px from --nominal-fx" if nominal["fx_px"]
                                                  else f"N/A, {nominal['source']}"))
    ratio_txt = f"{K[0, 0] / nominal['fx_px']:.3f}" if nominal["fx_px"] else "n/a"
    log(f"[{name}] {ch}: fx {K[0, 0]:.1f} fy {K[1, 1]:.1f} cx {K[0, 2]:.1f} cy {K[1, 2]:.1f}; "
        f"fx/nominal-air {ratio_txt}; FOV {fmt_fov(hfov, vfov)}")

    # models / errors outputs
    models_out = {}
    for m, f in cal["fits"].items():
        models_out[m] = {"train_rms_px": f["rms"], "views": len(f["kept"]), "dropped": f["dropped"],
                         "K": f["K"], "D": f["D"], "n_params_dist": pm.MODEL_PARAMS[m],
                         "heldout_rms_px": cal["cv"].get(m, {}).get("held_out_rms"),
                         "heldout_median_view_px": cal["cv"].get(m, {}).get("held_out_median_view"),
                         "outlier_threshold_px": f.get("outlier_threshold_px")}
    write_json(os.path.join(out_dir, "models.json"),
               {"chosen": ch, "why": cal["why"], "models": models_out, "errors": cal["errors"],
                "probes": cal["probes"], "f_ref_px": cal["f_ref"], "folds": cfg["folds"], "seed": cfg["seed"],
                "cross_model_K": cal["cross_model_K"],
                "cross_model_K_note": "INFORMATION, not a bar: the aligned (shape-only) B4/B5c model disagreement "
                                      "absorbs these K differences; the literal reading includes them. Which "
                                      "reading governs is the A2 decision (prereg doc).",
                "bootstrap_replicates_ok": f"{cal['boot_stats']['replicates_ok']}/{cal['boot_stats']['replicates']}"})
    with open(os.path.join(out_dir, "per_view_errors.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["stem", "calib_index", "outlier", "distance_mm", "tilt_deg", f"train_rms_px_{ch}"] +
                    [f"heldout_rms_px_{m}" for m in cal["cv"]])
        for i, f in enumerate(in_set):
            wr.writerow([f["stem"], i, f["outlier"], f.get("distance_mm"), f.get("tilt_deg"), f.get("train_rms_px")] +
                        [cal["cv"][m]["held_out_per_view"][i] for m in cal["cv"]])
    with open(os.path.join(out_dir, "bootstrap.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["replicate", "fx", "fy", "cx", "cy", "rms_px", "dist_coeffs", "error"])
        for i, b in enumerate(cal["bootstrap"]):
            if "error" in b:
                wr.writerow([i, "", "", "", "", "", "", b["error"]])
            else:
                wr.writerow([i, b["K"][0, 0], b["K"][1, 1], b["K"][0, 2], b["K"][1, 2], b["rms"],
                             " ".join(f"{v:.6g}" for v in b["D"]), ""])

    # bars
    bars_out, v_full, v_crop = evaluate_bars(len(final["kept"]), cal, cov, bars,
                                             crop_skip=None if ck else why, nominal=nominal)
    recipe = capture_recipe(cov, used, float(K[0, 0]), size, spec["square_mm"],
                            (spec["squares_x"], spec["squares_y"]), bars_out, frames,
                            native_frames=tuple(size) == tuple(native))
    for b in bars_out:
        log(f"[{name}]   {b['status']:4s} {b['bar']}: {fmt_value(b['value'])}  (bar: {b['rule']})")
    log(f"[{name}] VERDICT full-frame intrinsics {v_full}; production-crop intrinsics {v_crop}")

    # stage 4 pictures
    ba = {}
    try:
        ba = before_after(name, in_set, final, cal, ch, size, crop if tuple(size) == tuple(native) else None,
                          spec, args.cards.get(name), out_dir, figs, log)
    except Exception as exc:
        log(f"[{name}] WARN before/after failed: {type(exc).__name__}: {exc}")
        ba = {"error": str(exc)}
    write_json(os.path.join(out_dir, "before_after.json"), ba)

    # figures + sheets
    write_frames_csv(os.path.join(out_dir, "frames.csv"), frames)
    figs.screen_sheet(frames, os.path.join(out_dir, "screen_sheet.jpg"), name, args.thumb_width)
    figs.coverage_figure(pts, cov, size, os.path.join(out_dir, "coverage.png"), name)
    figs.models_figure(cal, in_set, os.path.join(out_dir, "models.png"), name)
    figs.bootstrap_figure(cal, os.path.join(out_dir, "bootstrap.png"), name)
    figs.uncertainty_figure(cal, pts, size, crop if tuple(size) == tuple(native) else None,
                            os.path.join(out_dir, "uncertainty.png"), name, PREREG["bars"])
    figs.verdict_sheet(name, pts, cov, cal, bars_out, v_full, v_crop, size,
                       crop if tuple(size) == tuple(native) else None, os.path.join(out_dir, "verdict.png"), PREREG["bars"])

    result.update({
        "chosen_model": ch, "why": cal["why"], "views_used": len(final["kept"]),
        "outlier_views": [in_set[d["index"]]["stem"] for d in final["dropped"]],
        "train_rms_px": final["rms"], "heldout_rms_px": {m: c["held_out_rms"] for m, c in cal["cv"].items()},
        "intrinsics_full": full_doc, "intrinsics_crop": crop_doc, "coverage": cov, "bars": bars_out,
        "verdict_full_frame": v_full, "verdict_production_crop": v_crop, "prereg_note": prereg_note, "recipe": recipe,
        "before_after": ba, "probes": cal["probes"], "bootstrap_stats": cal["boot_stats"],
        "cross_model_K": cal["cross_model_K"],
        "distance_m": {"median": recipe["median_distance_m"],
                       "min": min(f["distance_mm"] for f in used) / 1000, "max": max(f["distance_mm"] for f in used) / 1000},
        "lens_position": sorted({f.get("LensPosition") for f in frames if f.get("LensPosition") is not None}),
        "scaler_crop": sorted({str(f.get("ScalerCrop")) for f in frames if f.get("ScalerCrop")}),
    })
    return result


def before_after(name, in_set, final, cal, ch, size, crop, spec, card_path, out_dir, figs, log):
    """(a) the most off-centre calibration view, undistorted with the fold model that did NOT
    train on it; (b) the v3-card frame, undistorted with the final model."""
    import cv2
    bdir = os.path.join(out_dir, "before_after")
    obs = np.concatenate([in_set[i]["_corners"] for i in final["kept"]], axis=0)
    hull = cv2.convexHull(obs.astype(np.float32).reshape(-1, 1, 2)).reshape(-1, 2).astype(float)
    os.makedirs(bdir, exist_ok=True)
    w, h = size
    c0 = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    R = math.hypot(w / 2.0, h / 2.0)
    kept = final["kept"]
    best = max(kept, key=lambda i: float(np.max(np.linalg.norm(in_set[i]["_corners"] - c0, axis=1))) / R)
    f = in_set[best]
    fold = cal["cv"][ch]["fold_of"][best]
    fm = cal["cv"][ch]["fold_models"].get(fold)
    Kf, Df = (fm["K"], fm["D"]) if fm else (final["K"], final["D"])
    out = {"calibration_frame": {"stem": f["stem"], "file": f["path"], "model": ch,
                                 "model_note": (f"fold {fold} model: fitted WITHOUT this frame" if fm else
                                                "final model (fold model unavailable)")}}
    rows, agg = straightness_report(ch, Kf, Df, f["_ids"], f["_corners"], spec["squares_x"] - 1, spec["squares_y"] - 1)
    out["calibration_frame"]["straightness"] = agg
    out["calibration_frame"]["lines"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    img = load_image(f["path"], color=True)
    m1, m2 = undistort_maps(ch, Kf, Df, size)
    und = cv2.remap(img, m1, m2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    out["calibration_frame"]["files"] = figs.before_after_images(
        img, und, rows, crop, bdir, f"{name}_calframe_{f['stem']}",
        title=f"{name} calibration frame {f['stem']} ({ch}, fold model without this frame)",
        straight=agg, hull=hull, hull_after=_hull_after(ch, Kf, Df, hull))
    log(f"[{name}] before/after calibration frame {f['stem']}: board-line straightness RMS median "
        f"{agg.get('before_rms_px', {}).get('median', float('nan')):.2f} px -> "
        f"{agg.get('after_rms_px', {}).get('median', float('nan')):.2f} px (held-out model)")
    if card_path and os.path.isfile(card_path):
        cimg = load_image(card_path, color=True)
        if cimg is not None and (cimg.shape[1], cimg.shape[0]) == tuple(size):
            m1, m2 = undistort_maps(ch, final["K"], final["D"], size)
            cund = cv2.remap(cimg, m1, m2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            stem = os.path.splitext(os.path.basename(card_path))[0]
            out["card_frame"] = {"file": card_path, "model": ch, "files": figs.before_after_images(
                cimg, cund, [], crop, bdir, f"{name}_card_{stem}",
                title=f"{name} v3-card frame {stem} ({ch}, final model; frame not used in calibration)",
                hull=hull, hull_after=_hull_after(ch, final["K"], final["D"], hull))}
            log(f"[{name}] before/after card frame {stem}")
        else:
            out["card_frame"] = {"file": card_path, "skipped": "unreadable or not the calibration frame size"}
            log(f"[{name}] WARN card frame {card_path} skipped (unreadable or size mismatch)")
    else:
        out["card_frame"] = {"skipped": "no --card for this camera and no loop/*_loop_01_still.jpg next to it"}
    return out


def _hull_after(model, K, D, hull):
    """The observed-region outline in undistorted (newK = K) pixels, densified so it bends right."""
    dense = []
    for a, b in zip(hull, np.roll(hull, -1, axis=0)):
        for t in np.linspace(0, 1, 12, endpoint=False):
            dense.append(a + t * (b - a))
    return pm.pinhole_px(K, pm.rays(model, K, D, np.array(dense)))


def fmt_fov(h, v):
    f = lambda a: "n/a (model folds before the edge: extrapolating)" if a is None else f"{a:.1f} deg"  # noqa: E731
    return f"H {f(h)}, V {f(v)} (from the edge-midpoint rays)"


def fmt_value(v):
    if isinstance(v, dict):
        return ", ".join(f"{k} {fmt_value(x)}" for k, x in v.items())
    if isinstance(v, float):
        return "inf" if math.isinf(v) else f"{v:.3g}"
    return str(v)


def write_frames_csv(path, frames):
    cols = ["stem", "file", "status", "reasons", "in_set", "dup_of", "outlier", "corners", "markers", "coverage",
            "square_px", "distance_mm", "tilt_deg", "lap_norm", "blur_rel", "aniso", "board_mean", "clip_hi", "clip_lo",
            "centroid_x", "centroid_y", "train_rms_px", "heldout_rms_px", "ExposureTime", "AnalogueGain", "Lux",
            "LensPosition", "ScalerCrop", "exposure_us", "gain_db", "sidecar", "w", "h", "error"]
    with open(path, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for f in frames:
            row = dict(f)
            row["reasons"] = "; ".join(f.get("reasons", []))
            wr.writerow(row)


# ============================================================================ summary

def write_summary(run_dir, results, args, layout):
    write_json(os.path.join(run_dir, "summary.json"), {"cameras": results, "layout": layout, "prereg": PREREG})
    native = tuple(int(v) for v in args.native_size.lower().split("x"))
    sizes = {tuple(r["image_size"]) for r in results if r.get("image_size")}
    px = (f"native {native[0]}×{native[1]} sensor px" if sizes <= {native} else
          "each camera's own full-frame px (size given per camera)")
    L = [f"# Underwater calibration — {args.tag}", "",
         f"Pre-registered bars: `{PREREG['doc']}`. Pixels = {px} unless labelled.", ""]
    if PREREG.get("NON_PREREG_overrides"):
        L += [f"**NON-PREREG SENSITIVITY RUN** — screen thresholds overridden: {PREREG['NON_PREREG_overrides']}", ""]
    for r in results:
        L.append(f"## {r['camera']}")
        if r.get("error"):
            L += [f"**FAILED:** {r['error']}", ""]
            continue
        k = r["intrinsics_full"]
        L.append(f"- Verdict: full-frame intrinsics **{r['verdict_full_frame']}**, production-crop intrinsics "
                 f"**{r['verdict_production_crop']}**")
        if r.get("prereg_note"):
            L.append(f"- **{r['prereg_note']}**")
        L.append(f"- Frames {r['n_frames']}: screen {r['screen']['status_counts']}, {r['screen']['distinct_set']} "
                 f"distinct poses, {r['views_used']} used after outlier rejection; board at "
                 f"{r['distance_m']['min']:.2f}–{r['distance_m']['max']:.2f} m (median {r['distance_m']['median']:.2f} m)")
        L.append(f"- Model **{r['chosen_model']}** ({r['why']}). Held-out RMS: " +
                 ", ".join(f"{m} {v:.3f} px" for m, v in r["heldout_rms_px"].items()) +
                 f"; training RMS {r['train_rms_px']:.3f} px")
        L.append(f"- Full frame {k['image_size'][0]}×{k['image_size'][1]}: fx {k['fx']:.1f}, fy {k['fy']:.1f}, "
                 f"cx {k['cx']:.1f}, cy {k['cy']:.1f}, dist [{', '.join(f'{v:.5g}' for v in k['dist_coeffs'])}]")
        if r.get("intrinsics_crop"):
            c = r["intrinsics_crop"]
            L.append(f"- Production crop {c['image_size'][0]}×{c['image_size'][1]} at {c['crop_native_xywh']}: "
                     f"fx {c['fx']:.1f}, fy {c['fy']:.1f}, cx {c['cx']:.1f}, cy {c['cy']:.1f} (same distortion)")
        if k.get("nominal_fx_in_air_px"):
            nom = (f"Underwater fx / in-air nominal ({k['nominal_fx_in_air_px']:.0f} px, "
                   f"{k['nominal_fx_in_air_source']}) = {k['fx_over_nominal_in_air']:.3f}")
        else:
            nom = f"Underwater fx / in-air nominal: n/a ({k['nominal_fx_in_air_source']})"
        L.append(f"- {nom}; FOV {fmt_fov(*k['fov_deg_h_v_from_edge_rays'])}")
        L.append("")
        L.append("| bar | status | value | rule |")
        L.append("|---|---|---|---|")
        for b in r["bars"]:
            L.append(f"| {b['bar']} | {b['status']} | {fmt_value(b['value'])} | {b['rule']} |")
        L.append("")
        L.append("Cross-model intrinsics (information, not a bar; the shape-only reading of B4/B5c absorbs these):")
        L.append("")
        L.append("| models | Δfx | Δfy | Δcx | Δcy |")
        L.append("|---|---|---|---|---|")
        for c in r.get("cross_model_K", []):
            L.append(f"| {c['pair']} | {c['dfx_pct']:+.2f} % | {c['dfy_pct']:+.2f} % | {c['dcx_px']:+.0f} px | "
                     f"{c['dcy_px']:+.0f} px |")
        L.append("")
        L.append("Capture recipe / notes:")
        for line in r["recipe"]["lines"]:
            L.append(f"- {line}")
        L.append("")
    with open(os.path.join(run_dir, "summary.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


# ============================================================================ main

def parse_kv(items, what):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"{what} {it!r} must be NAME=PATH")
        k, v = it.split("=", 1)
        if not k:
            raise ValueError(f"{what} {it!r} has an empty NAME")
        out[k] = os.path.expanduser(v)
    return out


def parse_nominal_fx(items, cams):
    """--nominal-fx NAME=PX (repeatable) -> {camera: px}; NAME must be one of the cameras."""
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep or k not in cams:
            raise ValueError(f"--nominal-fx {it!r} must be NAME=PX with NAME one of {sorted(cams)}")
        try:
            px = float(v)
        except ValueError:
            px = float("nan")
        if not (math.isfinite(px) and px > 0):
            raise ValueError(f"--nominal-fx {it!r}: PX must be a positive number of pixels")
        out[k] = px
    return out


def build_parser():
    ap = argparse.ArgumentParser(description="Repeatable underwater ChArUco calibration (screen, coverage, 3 models, "
                                             "held-out selection, bootstrap, before/after) against pre-registered bars.",
                                 epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cameras", nargs="+", help="NAME=FOLDER_OR_GLOB, one per camera")
    ap.add_argument("--card", action="append", default=[], help="NAME=JPG v3-card frame for the before/after")
    ap.add_argument("--out-root", default=os.path.expanduser("~/NereusPool/calib_runs"))
    ap.add_argument("--tag", default="pool_calib")
    ap.add_argument("--squares-x", type=int, default=pcc.BOARD_SPEC["squares_x"])
    ap.add_argument("--squares-y", type=int, default=pcc.BOARD_SPEC["squares_y"])
    ap.add_argument("--square-mm", type=float, default=pcc.BOARD_SPEC["square_mm"])
    ap.add_argument("--marker-mm", type=float, default=pcc.BOARD_SPEC["marker_mm"])
    ap.add_argument("--dictionary", default=pcc.BOARD_SPEC["dictionary"])
    ap.add_argument("--layout", choices=("auto", "modern", "legacy"), default="auto")
    ap.add_argument("--layout-sample", type=int, default=6, help="frames per camera used to pick the layout (auto)")
    ap.add_argument("--crop", default=",".join(map(str, DEFAULT_CROP)), help="production crop x,y,w,h native px")
    ap.add_argument("--native-size", default=f"{NATIVE[0]}x{NATIVE[1]}",
                    help="the IMX708 full frame WxH (crop maths, and which frames count as IMX708 for the "
                         "pre-registered bars). Leave it at the default for any other camera: its frames then "
                         "get B5c / crop verdict N/A, B6 N/A without --nominal-fx and the not-pre-registered label")
    ap.add_argument("--nominal-fx", action="append", default=[], metavar="NAME=PX",
                    help=f"in-air nominal fx (px of that camera's frames) for the B6 sanity bar. Default: the IMX708 "
                         f"{NOMINAL_FX_AIR:.0f} px for --native-size frames only; other frame sizes -> B6 N/A")
    ap.add_argument("--folds", type=int, default=PREREG["calib"]["folds"])
    ap.add_argument("--bootstrap", type=int, default=PREREG["calib"]["bootstrap"])
    ap.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--thumb-width", type=int, default=480)
    ap.add_argument("--screen-override", action="append", default=[], metavar="KEY=VALUE",
                    help="SENSITIVITY ONLY: change a pre-registered screen threshold (e.g. clip_fail=1.01). "
                         "The run is labelled NON-PREREG everywhere.")
    return ap


def main(argv=None):
    import copy
    saved = copy.deepcopy(PREREG)
    try:
        return _main(argv)
    finally:                      # a --screen-override must not leak into the next call (tests, imports)
        PREREG.clear()
        PREREG.update(saved)


def _main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        cams = parse_kv(a.cameras, "camera")
        a.cards = parse_kv(a.card, "--card")
        a.nominal_fxs = parse_nominal_fx(a.nominal_fx, cams)
        crop = tuple(int(v) for v in a.crop.split(","))
        native = tuple(int(v) for v in a.native_size.lower().split("x"))
        assert len(crop) == 4 and len(native) == 2
        x, y, cw, ch = crop
        if x < 0 or y < 0 or cw <= 0 or ch <= 0 or x + cw > native[0] or y + ch > native[1]:
            # refused here (QE #178) so that a skipped crop always means "frames not --native-size",
            # never a typo'd --crop silently turning B5c into N/A
            raise ValueError(f"--crop {a.crop} is not inside --native-size {a.native_size}")
    except (ValueError, AssertionError) as exc:
        print(f"[pipeline] REFUSED: {exc or 'bad --crop / --native-size'}", file=sys.stderr)
        return 2
    overrides = {}
    for it in a.screen_override:
        k, _, v = it.partition("=")
        if k not in PREREG["screen"] or not v:
            print(f"[pipeline] REFUSED: --screen-override {it!r}: key must be one of {sorted(PREREG['screen'])}",
                  file=sys.stderr)
            return 2
        overrides[k] = float(v)
    if overrides:
        PREREG["screen"].update(overrides)
        PREREG["NON_PREREG_overrides"] = overrides
        a.tag = a.tag + "_NONPREREG"
    if a.marker_mm >= a.square_mm or min(a.squares_x, a.squares_y) < 3 or a.folds < 2 or a.bootstrap < 2:
        print("[pipeline] REFUSED: need marker_mm < square_mm, >= 3x3 squares, --folds >= 2, --bootstrap >= 2",
              file=sys.stderr)
        return 2
    inputs = {n: resolve_frames(p) for n, p in cams.items()}
    empty = [n for n, v in inputs.items() if not v]
    if empty:
        print(f"[pipeline] REFUSED: no image files for {', '.join(empty)} ({', '.join(cams[n] for n in empty)})",
              file=sys.stderr)
        return 2
    for n, p in cams.items():
        if n not in a.cards:
            dc = default_card(p)
            if dc:
                a.cards[n] = dc
    try:
        import matplotlib
        import scipy
    except ImportError as exc:
        print(f"[pipeline] REFUSED: {exc}. Use the repo's main .venv (opencv-contrib, scipy, matplotlib).",
              file=sys.stderr)
        return 2
    cv2, _ = pcc.need_cv2()
    import pool_calib_figures as figs

    t_start = time.time()
    run_dir = os.path.join(os.path.expanduser(a.out_root), f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{a.tag}")
    os.makedirs(run_dir, exist_ok=True)
    log = pcc.Log()
    log.open(os.path.join(run_dir, "pipeline.log"))
    log(f"[pipeline] underwater calibration pipeline -> {run_dir}")
    log(f"[pipeline] host {platform.node()}  python {platform.python_version()}  OpenCV {cv2.__version__}  "
        f"numpy {np.__version__}  scipy {scipy.__version__}  matplotlib {matplotlib.__version__}  workers {a.workers}")
    log(f"[pipeline] pre-registered bars: {PREREG['doc']} (also in prereg_bars.json)")
    if PREREG.get("NON_PREREG_overrides"):
        log(f"[pipeline] *** NON-PREREG SENSITIVITY RUN: screen thresholds overridden {PREREG['NON_PREREG_overrides']} ***")
    for n, v in inputs.items():
        log(f"[pipeline] camera {n}: {len(v)} file(s) from {cams[n]}; card frame {a.cards.get(n) or 'none'}")
    write_json(os.path.join(run_dir, "prereg_bars.json"), PREREG)
    gi = git_info()
    manifest = {"tool": "tools/pool_calib_pipeline.py", "created": datetime.now().isoformat(timespec="seconds"),
                "command": " ".join(shlex.quote(x) for x in [sys.executable] + sys.argv), "cwd": os.getcwd(),
                "host": platform.node(), "git": gi, "args": {k: v for k, v in vars(a).items()},
                "prereg_doc": PREREG["doc"],
                "prereg_sha256": sha256_file(PREREG_DOC) if os.path.isfile(PREREG_DOC) else None,
                "software": {"python": platform.python_version(), "opencv": cv2.__version__, "numpy": np.__version__,
                             "scipy": scipy.__version__, "matplotlib": matplotlib.__version__},
                "inputs": {n: {"source": cams[n], "n_files": len(v),
                               "files": {x: sha256_file(x) for x in v},
                               "card": a.cards.get(n),
                               "card_sha256": sha256_file(a.cards[n]) if a.cards.get(n) else None}
                           for n, v in inputs.items()},
                "coordinates": "native sensor px of the full frame; crop = pure pixel crop [x, y, w, h]"}
    write_json(os.path.join(run_dir, "run_manifest.json"), manifest)

    # board layout: pooled sample across cameras (same physical board)
    spec = {"squares_x": a.squares_x, "squares_y": a.squares_y, "square_mm": a.square_mm,
            "marker_mm": a.marker_mm, "dictionary": a.dictionary, "legacy": a.layout == "legacy"}
    cands = pcc.layout_candidates(a)
    if len(cands) == 1:
        layout = {"squares_x": cands[0][0], "squares_y": cands[0][1], "legacy": cands[0][2], "trials": []}
    else:
        sample = []
        for n, v in inputs.items():
            k = min(a.layout_sample, len(v))
            for i in sorted({round(j * (len(v) - 1) / max(k - 1, 1)) for j in range(k)}):
                try:
                    g = None if v[i].lower().endswith(".dng") else load_image(v[i])   # DNG decode is slow
                except Exception as exc:
                    log(f"[pipeline] WARN layout sample {os.path.basename(v[i])} unreadable: {exc}")
                    g = None
                if g is not None:
                    sample.append(g)
        best, trials, why = pcc.choose_layout(sample, a, cv2)
        for t in trials:
            log(f"[pipeline] layout try {t['squares_x']}x{t['squares_y']} {'legacy' if t['legacy'] else 'modern'}: "
                f"{t.get('corners', 'same as ' + str(t.get('same_as')))} corners")
        if best is None:
            log(f"[pipeline] FAIL: {why}")
            return 1
        layout = {"squares_x": best["squares_x"], "squares_y": best["squares_y"], "legacy": best["legacy"], "trials": trials}
    spec.update({"squares_x": layout["squares_x"], "squares_y": layout["squares_y"], "legacy": layout["legacy"]})
    log(f"[pipeline] board layout {spec['squares_x']}x{spec['squares_y']} {'LEGACY' if spec['legacy'] else 'modern'}, "
        f"{spec['square_mm']}/{spec['marker_mm']} mm {spec['dictionary']}")

    results = []
    for n, v in inputs.items():
        t0 = time.time()
        try:
            r = process_camera(n, v, spec, a, os.path.join(run_dir, n), crop, native, log, figs)
        except Exception as exc:
            import traceback
            log(f"[{n}] PIPELINE ERROR {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            r = {"camera": n, "error": f"{type(exc).__name__}: {exc}"}
        r["seconds"] = round(time.time() - t0, 1)
        results.append(r)
    write_summary(run_dir, results, a, layout)

    # output sanity (CLAUDE.md §8: a run that finishes with empty folders is a failure)
    outputs, missing = {}, []
    for root, _, files in os.walk(run_dir):
        for fn in files:
            p = os.path.join(root, fn)
            outputs[os.path.relpath(p, run_dir)] = os.path.getsize(p)
    for r in results:
        if r.get("error"):
            continue
        for need in ("frames.csv", "coverage.png", "models.json", "verdict.png", "uncertainty.png",
                     f"intrinsics_full_{r['image_size'][0]}x{r['image_size'][1]}.json"):
            rel = os.path.join(r["camera"], need)
            if outputs.get(rel, 0) == 0:
                missing.append(rel)
    manifest.update({"finished": datetime.now().isoformat(timespec="seconds"),
                     "seconds": round(time.time() - t_start, 1), "outputs": outputs, "missing_outputs": missing,
                     "verdicts": {r["camera"]: {"full_frame": r.get("verdict_full_frame"),
                                                "production_crop": r.get("verdict_production_crop"),
                                                "error": r.get("error")} for r in results}})
    write_json(os.path.join(run_dir, "run_manifest.json"), manifest)
    log(f"[pipeline] wrote {len(outputs)} files in {run_dir} ({time.time() - t_start:.0f}s)")
    for r in results:
        log(f"[pipeline] {r['camera']}: " + (f"ERROR {r['error']}" if r.get("error") else
            f"full-frame {r['verdict_full_frame']}, production crop {r['verdict_production_crop']}, "
            f"model {r['chosen_model']}, held-out {r['heldout_rms_px'][r['chosen_model']]:.3f} px"))
    if missing:
        log(f"[pipeline] FAIL: missing/empty outputs {missing}")
        return 1
    if any(r.get("error") for r in results):
        return 1
    return 0 if all(r["verdict_full_frame"] == "PASS" for r in results) else 3


if __name__ == "__main__":
    sys.exit(main())
