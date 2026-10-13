#!/usr/bin/env python3
# filename: focus_sweep_sheet.py
# description: Pool test 2026-10-13 — focus-sweep review (Mac): per-unit cut sheet + metric-vs-LensPosition plot + CSV/JSON from the Pi `sweep` frames.
"""
focus_sweep_sheet.py — which LensPosition is sharpest on the ChArUco board, per unit?

Purpose (Nick 2026-10-13 11 AM): the Pi `sweep` verb captures full-native 4608x2592 JPEG (+ DNG)
frames at a list of LensPositions into <run>/sweep/<HHMMSS>/ (with sweep_manifest.json). After
`pool_pull.py --sweep`, this tool scores each frame ON THE BOARD and shows the evidence.

Per unit:
  - board region: ChArUco detection (tools/pool_calib_check.py detector + layout; default
    Nick's calib.io CCT600400C 9x14 40/30 mm DICT_5X5_100, --layout auto). A frame where the board
    is not found (defocus) uses the union region of the frames where it was found (the board does
    not move during a sweep, only the lens does); no detection at all -> the central third of the
    frame. The source is labelled per frame (board / board(other frames) / frame centre).
  - metrics on that region, computed here from the JPEG (grayscale, float):
      lapvar_norm    var(Laplacian 3x3) / mean(I)^2   (normalised by mean intensity, squared so
                     an exposure change k*I cancels: var scales with k^2)
      tenengrad_norm mean(Gx^2 + Gy^2) / mean(I)^2    (Sobel 3x3, same normalisation)
      corners        ChArUco corners found (of (sx-1)*(sy-1) = 104 on Nick's board)
    plus the on-Pi lapvar from sweep_manifest.json (schema bmcam_focus_sweep_v1: on a CENTRED
    1024x1024 crop, not on the board; reported, not used for "best").
  - best LensPosition = argmax lapvar_norm, with a 3-point parabola refinement when the peak is
    interior; tenengrad's argmax and the corner count are shown next to it.

Status legend (CLAUDE.md §13):
  frame  PASS  board found, >= 50 % of the board's corners
         WARN  board found, fewer corners
         FAIL  board not found in this frame (metric on the fallback region)
  unit   PASS  board found at the best LensPosition, peak interior (not the first/last lens of the
               sweep), peak distinct (best >= 1.15 x the median), lapvar/tenengrad agree within one step
         WARN  any of those fails (e.g. the peak sits on the sweep edge: true focus may be outside)
         FAIL  no frames, or the board was never found

Inputs
  --unit NAME=PATH   repeatable; PATH = a sweep folder (with *.jpg), or a run / unit folder that
                     contains sweep/<HHMMSS>/ (the newest is used). e.g.
                     --unit bmcam003=~/NereusPool/20261013/bmcam003
  --board-distance-m D   board distance for the title (not used in maths); "not given" otherwise
  --medium in-air|underwater (default in-air; the pool sweep is underwater: say so)
  --run-tag TAG      title + file names (default: the sweep folder name)
  board flags as pool_calib_check.py (--squares-x/-y, --square-mm, --marker-mm, --dictionary, --layout)
Outputs (in --out, default ./runs/focus_sweep_<UTC>/)
  focus_sheet_<unit>.jpg  one row per LensPosition: full frame (SCALED to 480 px wide, region box
                          drawn) | 100 % crop (1:1 native pixels, 480x270, centred on the board) |
                          metrics + status; the best row is marked
  focus_plot.png          lapvar_norm and tenengrad_norm (each normalised to that unit's peak = 1)
                          and corner count vs LensPosition, both units overlaid, best marked
  focus_sweep.csv         one row per frame
  focus_sweep.json        settings, software versions, per-unit best + verdict, per-frame records
  focus_sweep.log         everything printed
Exit 0 = every unit PASS or WARN; 1 = a unit FAILs; 2 = usage.

Example (Mac, repo root, after `python3 tools/pool_pull.py --sweep ...`):
  python3 tools/focus_sweep_sheet.py --unit bmcam003=~/NereusPool/20261013/bmcam003 \\
      --unit bmcam004=~/NereusPool/20261013/bmcam004 --board-distance-m 1.0 --medium underwater

Requirements: opencv-contrib-python-headless (>= 4.7; tested 5.0.0) + numpy, as pool_calib_check.py.
Limits: JPEG (ISP-sharpened) metrics, not RAW; a single board distance per sweep; LensPosition is
  the manifest's applied value when present, else the requested one (else parsed from _lp<x> in
  the file name); metrics compare frames WITHIN a unit (normalised plots compare curve shapes).
"""
import argparse
import csv
import glob
import json
import math
import os
import platform
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool_calib_check as pcc  # noqa: E402

ROW_W, CROP_W, CROP_H, TEXT_W = 480, 480, 270, 380
UNIT_COLORS = [(60, 140, 255), (80, 200, 80), (200, 120, 255), (0, 200, 220)]   # BGR
RE_LP = re.compile(r"_lp(\d+(?:\.\d+)?)")


# ----------------------------------------------------------------------------- inputs

def find_sweep_dir(path):
    """A folder with *.jpg -> itself; else the newest */sweep/<x>/ or sweep/<x>/ below it."""
    path = os.path.abspath(os.path.expanduser(path))
    if glob.glob(os.path.join(path, "*.jpg")):
        return path
    cands = [d for d in glob.glob(os.path.join(path, "sweep", "*")) + glob.glob(os.path.join(path, "*", "sweep", "*"))
             if os.path.isdir(d) and glob.glob(os.path.join(d, "*.jpg"))]
    return sorted(cands, key=lambda d: (os.path.basename(d), d))[-1] if cands else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_frames(sweep_dir):
    """-> sorted list of {file, path, lp_req, lp_applied, lp, pi_metric}.
    Manifest = schema "bmcam_focus_sweep_v1" (Pi builder, 2026-10-13): frames[] with jpg (file name),
    lens_requested, metadata.LensPosition (= applied), focus.lapvar (on a CENTRED 1024x1024 crop,
    not on the board). Without a manifest the file name's _lp<x> gives the requested lens."""
    man = {}
    mp = os.path.join(sweep_dir, "sweep_manifest.json")
    manifest = None
    if os.path.exists(mp):
        with open(mp) as f:
            manifest = json.load(f)
        for fr in manifest.get("frames", []) if isinstance(manifest, dict) else []:
            name = os.path.basename(str(fr.get("jpg") or fr.get("file") or fr.get("jpeg") or ""))
            if name:
                man[name] = fr
    frames = []
    for p in sorted(glob.glob(os.path.join(sweep_dir, "*.jpg"))):
        name = os.path.basename(p)
        if name.endswith("_preview.jpg"):
            continue
        fr = man.get(name, {})
        m = RE_LP.search(name)
        lp_req = _num(fr.get("lens_requested"))
        if lp_req is None and m:
            lp_req = float(m.group(1))
        lp_app = _num((fr.get("metadata") or {}).get("LensPosition"))
        pi_metric = _num((fr.get("focus") or {}).get("lapvar"))
        frames.append({"file": name, "path": p, "lp_req": lp_req, "lp_applied": lp_app,
                       "lp": lp_app if lp_app is not None else lp_req, "pi_metric": pi_metric,
                       "pi_error": fr.get("error") or fr.get("metric_error")})
    frames.sort(key=lambda r: (r["lp"] is None, r["lp"] if r["lp"] is not None else 0, r["file"]))
    return frames, manifest


# ----------------------------------------------------------------------------- metrics

def focus_metrics(gray, roi, cv2, np):
    """gray uint8 full frame, roi (x, y, w, h) -> dict of metrics on that region."""
    x, y, w, h = roi
    r = gray[y:y + h, x:x + w].astype(np.float64)
    mean = float(r.mean()) if r.size else 0.0
    if r.size == 0 or mean <= 1e-6:
        return {"mean_intensity": mean, "lapvar": 0.0, "lapvar_norm": 0.0, "tenengrad": 0.0, "tenengrad_norm": 0.0}
    lap = cv2.Laplacian(r, cv2.CV_64F, ksize=3)
    gx = cv2.Sobel(r, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(r, cv2.CV_64F, 0, 1, ksize=3)
    lapvar = float(lap.var())
    ten = float(np.mean(gx * gx + gy * gy))
    return {"mean_intensity": mean, "lapvar": lapvar, "lapvar_norm": lapvar / mean ** 2,
            "tenengrad": ten, "tenengrad_norm": ten / mean ** 2}


def roi_from_corners(corners, w, h, cv2, np, pad=0.05):
    x, y, bw, bh = cv2.boundingRect(corners.reshape(-1, 1, 2).astype(np.float32))
    px, py = int(bw * pad), int(bh * pad)
    x0, y0 = max(0, x - px), max(0, y - py)
    x1, y1 = min(w, x + bw + px), min(h, y + bh + py)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def union_roi(rois):
    x0 = min(r[0] for r in rois)
    y0 = min(r[1] for r in rois)
    x1 = max(r[0] + r[2] for r in rois)
    y1 = max(r[1] + r[3] for r in rois)
    return (x0, y0, x1 - x0, y1 - y0)


def parabola_peak(xs, ys, i):
    """3-point parabola through (i-1, i, i+1) -> vertex x, or None."""
    if i <= 0 or i >= len(xs) - 1:
        return None
    (x0, x1, x2), (y0, y1, y2) = xs[i - 1:i + 2], ys[i - 1:i + 2]
    den = (x0 - x1) * (x0 - x2) * (x1 - x2)
    if den == 0:
        return None
    a = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / den
    b = (x2 * x2 * (y0 - y1) + x1 * x1 * (y2 - y0) + x0 * x0 * (y1 - y2)) / den
    if a >= 0:
        return None
    v = -b / (2 * a)
    return v if min(x0, x2) <= v <= max(x0, x2) else None


def analyse_unit(name, sweep_dir, a, cv2, np, log):
    frames, manifest = load_frames(sweep_dir)
    res = {"unit": name, "sweep_dir": sweep_dir, "frames": frames, "manifest_found": manifest is not None,
           # the Pi's own answer (centred 1024x1024 crop, not the board), shown for comparison only
           "pi_best_lp": _num((manifest or {}).get("best_lp")) if isinstance(manifest, dict) else None,
           "pi_best_lp_tenengrad": _num((manifest or {}).get("best_lp_tenengrad")) if isinstance(manifest, dict) else None}
    if not frames:
        res.update(verdict="FAIL", reasons=["no sweep JPEGs"], best=None)
        log(f"[focus] {name}: FAIL no JPEGs in {sweep_dir}")
        return res
    log(f"[focus] {name}: {len(frames)} frames in {sweep_dir} (manifest {'yes' if manifest else 'NO, lens from file names'})")
    grays = [cv2.imread(f["path"], cv2.IMREAD_GRAYSCALE) for f in frames]
    if a.layout == "auto":
        layout, trials, why = pcc.choose_layout([g for g in grays if g is not None], a, cv2)
    else:
        c = pcc.layout_candidates(a)[0]
        layout, trials, why = {"squares_x": c[0], "squares_y": c[1], "legacy": c[2]}, [], None
    res["layout"] = {"chosen": layout, "trials": trials, "why_none": why}
    board = pcc.make_board(layout["squares_x"], layout["squares_y"], a, cv2, layout["legacy"]) if layout else None
    total_corners = (layout["squares_x"] - 1) * (layout["squares_y"] - 1) if layout else 0
    det = cv2.aruco.CharucoDetector(board) if board else None
    rois = []
    for f, g in zip(frames, grays):
        f["w"], f["h"] = (g.shape[1], g.shape[0]) if g is not None else (None, None)
        f["corners"] = f["markers"] = 0
        f["_corners"] = None
        if g is None:
            f["error"] = "unreadable JPEG"
            continue
        if det is not None:
            d = pcc.detect(g, board, det, a, cv2, np)
            f["corners"], f["markers"], f["_corners"] = d["corners"], d["markers"], d["_corners"]
            if d["corners"] >= 4:
                f["roi"] = roi_from_corners(d["_corners"], f["w"], f["h"], cv2, np)
                f["roi_source"] = "board"
                rois.append(f["roi"])
    fallback = union_roi(rois) if rois else None
    for f, g in zip(frames, grays):
        if g is None:
            continue
        if "roi" not in f:
            if fallback:
                f["roi"], f["roi_source"] = fallback, "board (other frames)"
            else:
                W, H = f["w"], f["h"]
                f["roi"], f["roi_source"] = (W // 3, H // 3, W // 3, H // 3), "frame centre"
        f.update(focus_metrics(g, f["roi"], cv2, np))
        f["status"] = ("FAIL" if f["roi_source"] != "board" else
                       "PASS" if total_corners and f["corners"] >= 0.5 * total_corners else "WARN")
    del grays
    ok = [f for f in frames if "lapvar_norm" in f and f["lp"] is not None]
    if not ok:
        res.update(verdict="FAIL", reasons=["no readable frames with a LensPosition"], best=None)
        return res
    xs = [f["lp"] for f in ok]
    lv = [f["lapvar_norm"] for f in ok]
    tv = [f["tenengrad_norm"] for f in ok]
    i = max(range(len(ok)), key=lambda k: lv[k])
    j = max(range(len(ok)), key=lambda k: tv[k])
    best = ok[i]
    for f in frames:
        f["best"] = f is best
    fit = parabola_peak(xs, lv, i)
    med = sorted(lv)[len(lv) // 2]
    reasons = []
    if not rois:
        verdict = "FAIL"
        reasons.append("board never detected: metrics are on the frame centre")
    else:
        if best["roi_source"] != "board":
            reasons.append("board not detected at the best LensPosition")
        if i in (0, len(ok) - 1):
            reasons.append(f"peak at the sweep edge (lp {xs[i]:g}): true focus may lie outside {min(xs):g}..{max(xs):g}")
        if med > 0 and lv[i] < 1.15 * med:
            reasons.append(f"peak not distinct (best {lv[i]:.4g} < 1.15 x median {med:.4g})")
        if abs(i - j) > 1:
            reasons.append(f"lapvar best lp {xs[i]:g} vs tenengrad best lp {xs[j]:g} differ by > 1 step")
        verdict = "WARN" if reasons else "PASS"
    res.update(verdict=verdict, reasons=reasons, best={
        "lp": best["lp"], "lp_requested": best["lp_req"], "lp_applied": best["lp_applied"],
        "lp_parabola": fit, "lapvar_norm": lv[i], "tenengrad_best_lp": xs[j],
        "corners": best["corners"], "corners_max_lp": max(ok, key=lambda f: f["corners"])["lp"],
        "file": best["file"]}, total_corners=total_corners)
    log(f"[focus] {name}: {verdict} best lp {best['lp']:g}"
        f"{f' (parabola {fit:.3f})' if fit is not None else ''} lapvar_norm {lv[i]:.4g}; "
        f"tenengrad best {xs[j]:g}; corners {best['corners']}/{total_corners}"
        f"{'; ' + '; '.join(reasons) if reasons else ''}")
    return res


# ----------------------------------------------------------------------------- drawing

def put(img, text, xy, cv2, scale=0.5, color=(235, 235, 235), thick=1):
    cv2.putText(img, text, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


STATUS_BGR = {"PASS": (80, 200, 80), "WARN": (0, 190, 255), "FAIL": (60, 60, 240)}


def unit_sheet(res, path, header, cv2, np):
    rows = []
    for f in res["frames"]:
        img = cv2.imread(f["path"], cv2.IMREAD_COLOR)
        if img is None:
            continue
        H, W = img.shape[:2]
        s = ROW_W / W
        th = cv2.resize(img, (ROW_W, int(round(H * s))), interpolation=cv2.INTER_AREA)
        x, y, w, h = f["roi"]
        cv2.rectangle(th, (int(x * s), int(y * s)), (int((x + w) * s), int((y + h) * s)),
                      STATUS_BGR.get(f["status"], (200, 200, 200)), 2)
        cx, cy = x + w // 2, y + h // 2                      # 1:1 crop centred on the region
        x0 = min(max(0, cx - CROP_W // 2), max(0, W - CROP_W))
        y0 = min(max(0, cy - CROP_H // 2), max(0, H - CROP_H))
        crop = img[y0:y0 + CROP_H, x0:x0 + CROP_W].copy()
        crop = cv2.copyMakeBorder(crop, 0, CROP_H - crop.shape[0], 0, CROP_W - crop.shape[1], cv2.BORDER_CONSTANT)
        rh = max(th.shape[0], CROP_H)
        row = np.full((rh + 8, ROW_W + CROP_W + TEXT_W + 16, 3), 24, np.uint8)
        row[4:4 + th.shape[0], 4:4 + ROW_W] = th
        row[4:4 + CROP_H, ROW_W + 8:ROW_W + 8 + CROP_W] = crop
        tx = ROW_W + CROP_W + 16
        lines = [(f"lp {f['lp']:g} {'applied' if f['lp_applied'] is not None else 'requested'}" + (" BEST" if f.get("best") else ""),
                  0.72, (255, 255, 255) if not f.get("best") else (80, 220, 255), 2),
                 (f"req {f['lp_req']} / applied {f['lp_applied']}", 0.48, None, 1),
                 (f"lapvar_norm {f['lapvar_norm']:.4g}", 0.55, None, 1),
                 (f"tenengrad_norm {f['tenengrad_norm']:.4g}", 0.55, None, 1),
                 (f"corners {f['corners']}/{res.get('total_corners', 0)}  markers {f['markers']}", 0.5, None, 1),
                 (f"pi lapvar (centre 1024px) {f['pi_metric']:.4g}" if f["pi_metric"] is not None else "pi lapvar -", 0.45, None, 1),
                 (f"region: {f['roi_source']}", 0.45, None, 1),
                 (f"{f['status']}", 0.7, STATUS_BGR.get(f["status"]), 2)]
        yy = 28
        for text, sc, col, tk in lines:
            put(row, text, (tx, yy), cv2, sc, col or (225, 225, 225), tk)
            yy += 30 if sc >= 0.7 else 26
        if f.get("best"):
            cv2.rectangle(row, (1, 1), (row.shape[1] - 2, row.shape[0] - 2), (80, 220, 255), 3)
        rows.append(row)
    width = ROW_W + CROP_W + TEXT_W + 16
    head = np.full((30 * (len(header) + 1) + 10, width, 3), 0, np.uint8)
    for k, (text, col) in enumerate(header):
        put(head, text, (10, 28 + 30 * k), cv2, 0.62, col)
    cols = np.full((26, width, 3), 40, np.uint8)
    put(cols, "full frame (SCALED to 480 px wide; box = metric region)", (8, 18), cv2, 0.45)
    put(cols, "100% crop: 1:1 native pixels, 480x270, centred on the region", (ROW_W + 12, 18), cv2, 0.45)
    put(cols, "metrics (on the region)", (ROW_W + CROP_W + 20, 18), cv2, 0.45)
    cv2.imwrite(path, np.vstack([head, cols] + rows), [cv2.IMWRITE_JPEG_QUALITY, 88])


def plot(results, path, title, cv2, np):
    """Three panels (lapvar_norm, tenengrad_norm normalised per unit; corners), units overlaid."""
    Wd, ph, ml, mr, mt = 1200, 300, 90, 30, 70
    xs_all = [f["lp"] for r in results for f in r["frames"] if f.get("lp") is not None and "lapvar_norm" in f]
    if not xs_all:
        return
    xlo, xhi = min(xs_all), max(xs_all)
    if xhi == xlo:
        xlo, xhi = xlo - 0.1, xhi + 0.1
    pad = 0.04 * (xhi - xlo)
    xlo, xhi = xlo - pad, xhi + pad
    panels = [("lapvar_norm (each unit / its peak)", "lapvar_norm", True),
              ("tenengrad_norm (each unit / its peak)", "tenengrad_norm", True),
              ("ChArUco corners", "corners", False)]
    img = np.full((mt + len(panels) * (ph + 40) + 40, Wd, 3), 255, np.uint8)
    put(img, title, (ml, 30), cv2, 0.62, (20, 20, 20), 1)
    legend_x = ml
    for k, r in enumerate(results):
        col = UNIT_COLORS[k % len(UNIT_COLORS)]
        put(img, f"{r['unit']} ({r['verdict']}, best lp {r['best']['lp']:g})" if r.get("best") else f"{r['unit']} ({r['verdict']})",
            (legend_x + 22, 56), cv2, 0.5, (20, 20, 20))
        cv2.line(img, (legend_x, 51), (legend_x + 16, 51), col, 3)
        legend_x += 330
    for p, (label, key, norm) in enumerate(panels):
        top = mt + p * (ph + 40) + 20
        x0, x1, y0, y1 = ml, Wd - mr, top, top + ph
        cv2.rectangle(img, (x0, y0), (x1, y1), (120, 120, 120), 1)
        put(img, label, (x0 + 6, y0 - 6), cv2, 0.5, (20, 20, 20))
        vals = [f.get(key, 0) or 0 for r in results for f in r["frames"] if "lapvar_norm" in f]
        ymax = 1.0 if norm else max(1, max(vals or [1]))
        for t in (0, 0.5, 1.0):
            yy = int(y1 - t * (y1 - y0))
            cv2.line(img, (x0, yy), (x1, yy), (225, 225, 225), 1)
            put(img, f"{t * ymax:.0f}" if not norm else f"{t:.1f}", (x0 - 50, yy + 5), cv2, 0.45, (60, 60, 60))
        for lp in sorted(set(xs_all)):
            xx = int(x0 + (lp - xlo) / (xhi - xlo) * (x1 - x0))
            cv2.line(img, (xx, y1), (xx, y1 + 5), (60, 60, 60), 1)
            if p == len(panels) - 1:
                put(img, f"{lp:g}", (xx - 14, y1 + 22), cv2, 0.42, (60, 60, 60))
        for k, r in enumerate(results):
            col = UNIT_COLORS[k % len(UNIT_COLORS)]
            fs = [f for f in r["frames"] if f.get("lp") is not None and key in f]
            if not fs:
                continue
            peak = max(f[key] for f in fs) or 1
            pts = [(int(x0 + (f["lp"] - xlo) / (xhi - xlo) * (x1 - x0)),
                    int(y1 - (f[key] / peak if norm else f[key] / ymax) * (y1 - y0))) for f in fs]
            for a_, b_ in zip(pts, pts[1:]):
                cv2.line(img, a_, b_, col, 2, cv2.LINE_AA)
            for pt in pts:
                cv2.circle(img, pt, 4, col, -1)
            if r.get("best"):
                bf = [f for f in fs if f.get("best")]
                if bf:
                    bp = pts[fs.index(bf[0])]
                    cv2.circle(img, bp, 10, col, 2)
                    put(img, f"best {bf[0]['lp']:g}", (bp[0] + 12, bp[1] + 4 + 14 * k), cv2, 0.45, col, 1)
    put(img, "LensPosition (applied when the manifest has it, else requested)", (ml, img.shape[0] - 8), cv2, 0.45, (60, 60, 60))
    cv2.imwrite(path, img)


# ----------------------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(description="Focus-sweep review: per-unit cut sheet + metric plot + CSV/JSON.",
                                 epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", action="append", required=True, help="NAME=PATH (sweep folder or run/unit folder)")
    ap.add_argument("--board-distance-m", type=float, default=None)
    ap.add_argument("--medium", choices=("in-air", "underwater"), default="in-air")
    ap.add_argument("--run-tag", default=None)
    ap.add_argument("--out", default=None)
    for flag, default in (("--squares-x", pcc.BOARD_SPEC["squares_x"]), ("--squares-y", pcc.BOARD_SPEC["squares_y"])):
        ap.add_argument(flag, type=int, default=default)
    ap.add_argument("--square-mm", type=float, default=pcc.BOARD_SPEC["square_mm"])
    ap.add_argument("--marker-mm", type=float, default=pcc.BOARD_SPEC["marker_mm"])
    ap.add_argument("--dictionary", default=pcc.BOARD_SPEC["dictionary"])
    ap.add_argument("--layout", choices=("auto", "modern", "legacy"), default="auto")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    cv2, np = pcc.need_cv2()
    units = []
    for spec in a.unit:
        name, sep, path = spec.partition("=")
        d = find_sweep_dir(path) if sep else None
        if not sep or not name:
            print(f"[focus] --unit {spec!r}: use NAME=PATH", file=sys.stderr)
            return 2
        if not d:
            print(f"[focus] --unit {spec!r}: no sweep JPEGs under {path} (pool_pull.py --sweep first?)", file=sys.stderr)
            return 2
        units.append((name, d))
    tag = a.run_tag or os.path.basename(units[0][1])
    out = a.out or os.path.join("runs", f"focus_sweep_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    os.makedirs(out, exist_ok=True)
    logf = open(os.path.join(out, "focus_sweep.log"), "a")

    def log(msg):
        line = f"{datetime.now().strftime('%I:%M:%S %p')} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    dist = f"{a.board_distance_m:g} m" if a.board_distance_m is not None else "not given"
    log(f"[focus] {a.medium.upper()} focus sweep {tag}: units {', '.join(n for n, _ in units)}; board distance {dist}; "
        f"OpenCV {cv2.__version__}; out {out}")
    results = [analyse_unit(n, d, a, cv2, np, log) for n, d in units]

    title = f"FOCUS SWEEP {a.medium.upper()}  {tag}  units {', '.join(r['unit'] for r in results)}  board distance {dist}"
    legend = ["frame: PASS = board found, >= 50% of corners | WARN = fewer corners | FAIL = board not found (metric on the fallback region)",
              "unit: PASS = board at the best lens + peak interior + peak distinct + lapvar/tenengrad agree | WARN = any fails | FAIL = no board"]
    for r in results:
        if not r["frames"]:
            continue
        b = r.get("best") or {}
        header = [(f"{title}", (255, 255, 255)),
                  (f"{r['unit']}: {r['verdict']}  best lp {b.get('lp', float('nan')):g}"
                   + (f" (parabola {b['lp_parabola']:.3f})" if b.get("lp_parabola") is not None else "")
                   + f"  tenengrad best {b.get('tenengrad_best_lp', float('nan')):g}  "
                   f"layout {r['layout']['chosen']['squares_x']}x{r['layout']['chosen']['squares_y']}"
                   f"{' legacy' if r['layout']['chosen'] and r['layout']['chosen']['legacy'] else ''}"
                   if r.get("layout", {}).get("chosen") else f"{r['unit']}: {r['verdict']} (no board layout)",
                   STATUS_BGR.get(r["verdict"], (255, 255, 255))),
                  ((("; ".join(r["reasons"]) or "no warnings")[:120]
                    + (f"   | Pi (centre crop) best lp {r['pi_best_lp']:g}" if r.get("pi_best_lp") is not None else "")),
                   (220, 220, 220)),
                  (legend[0], (180, 180, 180)),
                  (legend[1], (180, 180, 180)),
                  (f"sweep {r['sweep_dir'][-90:]}", (150, 150, 150))]
        unit_sheet(r, os.path.join(out, f"focus_sheet_{r['unit']}.jpg"), header, cv2, np)
    plot(results, os.path.join(out, "focus_plot.png"), title, cv2, np)

    cols = ["unit", "file", "lp_req", "lp_applied", "lp", "status", "best", "roi_source", "roi", "corners", "markers",
            "mean_intensity", "lapvar", "lapvar_norm", "tenengrad", "tenengrad_norm", "pi_metric", "pi_error", "error"]
    with open(os.path.join(out, "focus_sweep.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in results:
            for fr in r["frames"]:
                w.writerow({**{k: fr.get(k) for k in cols}, "unit": r["unit"],
                            "roi": "x{0} y{1} w{2} h{3}".format(*fr["roi"]) if fr.get("roi") else ""})
    summary = {
        "check": f"focus sweep, {a.medium}", "created": datetime.now().isoformat(timespec="seconds"),
        "run_tag": tag, "board_distance_m": a.board_distance_m, "medium": a.medium,
        "command": " ".join([os.path.basename(sys.argv[0])] + (argv if argv is not None else sys.argv[1:])),
        "software": {"opencv": cv2.__version__, "numpy": np.__version__, "python": platform.python_version()},
        "board": {"squares_x": a.squares_x, "squares_y": a.squares_y, "square_mm": a.square_mm,
                  "marker_mm": a.marker_mm, "dictionary": a.dictionary, "layout_mode": a.layout},
        "metrics": {"lapvar_norm": "var(Laplacian 3x3) / mean(I)^2 on the region",
                    "tenengrad_norm": "mean(Sobel Gx^2 + Gy^2) / mean(I)^2 on the region",
                    "best": "argmax lapvar_norm (+ 3-point parabola when interior)"},
        "units": [{k: v for k, v in r.items() if k != "frames"} | {
            "frames": [{k: v for k, v in fr.items() if not k.startswith("_") and k != "path"} for fr in r["frames"]]}
            for r in results],
    }
    with open(os.path.join(out, "focus_sweep.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    log(f"[focus] wrote {out}/: " + ", ".join(sorted(os.listdir(out))))
    for r in results:
        b = r.get("best") or {}
        log(f"[focus] VERDICT {r['unit']}: {r['verdict']}"
            + (f"  best lp {b['lp']:g}" if b else "") + (f"  ({'; '.join(r['reasons'])})" if r.get("reasons") else ""))
    return 1 if any(r["verdict"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
