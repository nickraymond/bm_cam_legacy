#!/usr/bin/env python3
# filename: focus_sweep_sheet.py
# description: Pool test 2026-10-13 — focus-sweep review (Mac): per-unit cut sheet + metric-vs-LensPosition plot + CSV/JSON from the Pi `sweep` frames.
"""
focus_sweep_sheet.py — which LensPosition is sharpest on the ChArUco board, per unit, and how
repeatable is it across sweeps?

Purpose (Nick 2026-10-13 11 AM): the Pi `sweep` verb captures full-native 4608x2592 JPEG (+ DNG)
frames at a list of LensPositions into <run>/sweep/<HHMMSS>/ (with sweep_manifest.json). After
`pool_pull.py --sweep`, this tool scores every frame ON THE BOARD and shows the evidence.

Per unit (ALL its sweep folders by default, or the --sweep-dir ones):
  - ONE COMMON metric region for every frame of every sweep of the unit (QE #165): the ChArUco
    board box of the best-detected frame (most corners; tools/pool_calib_check.py detector +
    layout; default Nick's calib.io CCT600400C 9x14 40/30 mm DICT_5X5_100, --layout auto), so
    each lens position is scored on the SAME pixels. Each frame's own detection is kept as
    corners_detected (it sets the frame status, not the region). No detection anywhere -> the
    central third of the frame. A sweep whose own best box overlaps the common box by
    IoU < 0.7 is flagged (the board moved between sweeps).
  - metrics on that region, computed here from the JPEG (grayscale, float):
      lapvar_norm    var(Laplacian 3x3) / mean(I)^2   (normalised by mean intensity, squared so
                     an exposure change k*I cancels: var scales with k^2)
      tenengrad_norm mean(Gx^2 + Gy^2) / mean(I)^2    (Sobel 3x3, same normalisation)
      corners_detected  ChArUco corners found in that frame (of (sx-1)*(sy-1) = 104)
    plus the on-Pi lapvar + best_lp from sweep_manifest.json (schema bmcam_focus_sweep_v1: on a
    CENTRED 1024x1024 crop, not on the board; reported, not used for "best").
  - per sweep: best LensPosition = argmax lapvar_norm (+ 3-point parabola when interior);
    tenengrad's argmax shown next to it.
  - run-to-run: best lp per sweep and its spread (max - min); per lens position, the relative
    spread of lapvar_norm across sweeps ((max - min) / mean).

Status legend (CLAUDE.md §13):
  frame  PASS  board found in this frame, >= 50 % of its corners
         WARN  board found, fewer corners
         FAIL  board not found in this frame (still scored on the common region)
  sweep  PASS  board found at the best lp, peak interior (not the first/last lens), peak distinct
               (best >= 1.15 x the median), lapvar/tenengrad agree within one step; else WARN
  unit   PASS  every sweep PASS, best lp spread <= one lens step, board box stable (IoU >= 0.7)
         WARN  any of those fails
         FAIL  no frames, or the board was never found

Inputs
  --unit NAME=PATH       repeatable; PATH = a sweep folder (with *.jpg), or a unit / run folder:
                         EVERY sweep/<HHMMSS>/ under it is used (one sheet column each)
  --sweep-dir NAME=PATH  repeatable; explicit sweep folders for NAME (replaces --unit discovery
                         for that unit), e.g. two repeat sweeps of the same unit
  --board-distance-m D   board distance for the title (not used in maths); "not given" otherwise
  --medium in-air|underwater (default in-air; the pool sweep is underwater: say so)
  --run-tag TAG          title + file names (default: the first sweep folder name)
  board flags as pool_calib_check.py (--squares-x/-y, --square-mm, --marker-mm, --dictionary, --layout)
Outputs (in --out, default ./runs/focus_sweep_<UTC>/)
  focus_sheet_<unit>.jpg  one row per LensPosition: full frame (SCALED to 480 px wide, common box
                          drawn) | per sweep, side by side: 100 % crop (1:1 native pixels, 480x270,
                          centred on the common box) + metrics + status; best cells marked
  focus_plot.png          lapvar_norm and tenengrad_norm (each series normalised to its peak = 1)
                          and corners vs LensPosition; colour = unit, solid / dashed = 1st / later
                          sweep; best marked
  focus_sweep.csv         one row per frame (unit, sweep, ...)
  focus_sweep.json        settings, versions, per unit: common region, per-sweep best + verdict,
                          spread, verdict; per-frame records
  focus_sweep.log         everything printed
Exit 0 = every unit PASS or WARN; 1 = a unit FAILs; 2 = usage.

Example (Mac, repo root, after `python3 tools/pool_pull.py --sweep ...`):
  python3 tools/focus_sweep_sheet.py --unit bmcam003=~/NereusPool/20261013/bmcam003 \\
      --unit bmcam004=~/NereusPool/20261013/bmcam004 --board-distance-m 1.0
  python3 tools/focus_sweep_sheet.py --sweep-dir bmcam003=<.../sweep/113005> --sweep-dir bmcam003=<.../sweep/114210>

Requirements: opencv-contrib-python-headless (>= 4.7; tested 5.0.0) + numpy, as pool_calib_check.py.
Limits: JPEG (ISP-sharpened) metrics, not RAW; a single board distance per run of the tool;
  LensPosition is the manifest's applied value when present, else the requested one (else parsed
  from _lp<x> in the file name); sweep rows are matched on the requested lens (2 decimals);
  metrics compare frames WITHIN a unit (normalised plots compare curve shapes across units).
"""
import argparse
import csv
import glob
import json
import math
import os
import platform
import re
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool_calib_check as pcc  # noqa: E402

ROW_W, CROP_W, CROP_H, TEXT_W = 480, 480, 270, 300
UNIT_COLORS = [(60, 140, 255), (80, 200, 80), (200, 120, 255), (0, 200, 220)]   # BGR
RE_LP = re.compile(r"_lp(\d+(?:\.\d+)?)")
IOU_MIN = 0.7


# ----------------------------------------------------------------------------- inputs

def find_sweep_dirs(path):
    """A folder with *.jpg -> [itself]; else EVERY sweep/<x>/ (or */sweep/<x>/) below it, by name."""
    path = os.path.abspath(os.path.expanduser(path))
    if glob.glob(os.path.join(path, "*.jpg")):
        return [path]
    cands = [d for d in glob.glob(os.path.join(path, "sweep", "*")) + glob.glob(os.path.join(path, "*", "sweep", "*"))
             if os.path.isdir(d) and glob.glob(os.path.join(d, "*.jpg"))]
    return sorted(set(cands), key=lambda d: (os.path.basename(d), d))


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




def iou(a, b):
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def lens_key(f):
    """Row key across sweeps: the requested lens (else the applied one), 2 decimals."""
    v = f["lp_req"] if f["lp_req"] is not None else f["lp"]
    return None if v is None else round(v, 2)


def sweep_best(frames, log, unit, sweep):
    """Best lp of ONE sweep + its reasons -> dict (or None without usable frames)."""
    ok = [f for f in frames if "lapvar_norm" in f and f["lp"] is not None]
    if not ok:
        return {"sweep": sweep, "verdict": "FAIL", "reasons": ["no readable frames with a LensPosition"], "best": None}
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
    if best["corners_detected"] < 4:
        reasons.append("board not detected at the best LensPosition")
    if i in (0, len(ok) - 1):
        reasons.append(f"peak at the sweep edge (lp {xs[i]:g}): true focus may lie outside {min(xs):g}..{max(xs):g}")
    if med > 0 and lv[i] < 1.15 * med:
        reasons.append(f"peak not distinct (best {lv[i]:.4g} < 1.15 x median {med:.4g})")
    if abs(i - j) > 1:
        reasons.append(f"lapvar best lp {xs[i]:g} vs tenengrad best lp {xs[j]:g} differ by > 1 step")
    steps = sorted(b - a for a, b in zip(xs, xs[1:]) if b > a)
    res = {"sweep": sweep, "verdict": "WARN" if reasons else "PASS", "reasons": reasons,
           "step": steps[len(steps) // 2] if steps else None,
           "best": {"lp": best["lp"], "lp_requested": best["lp_req"], "lp_applied": best["lp_applied"],
                    "lp_parabola": fit, "lapvar_norm": lv[i], "tenengrad_best_lp": xs[j],
                    "corners_detected": best["corners_detected"], "file": best["file"]}}
    log(f"[focus] {unit} sweep {sweep}: {res['verdict']} best lp {best['lp']:g}"
        f"{f' (parabola {fit:.3f})' if fit is not None else ''}; tenengrad best {xs[j]:g}; "
        f"corners {best['corners_detected']}{'; ' + '; '.join(reasons) if reasons else ''}")
    return res


def analyse_unit(name, sweep_dirs, a, cv2, np, log):
    res = {"unit": name, "sweep_dirs": sweep_dirs, "frames": [], "sweeps": []}
    frames, pi_best = [], {}
    for sd in sweep_dirs:
        fs, manifest = load_frames(sd)
        sw = os.path.basename(sd.rstrip("/"))
        for f in fs:
            f["sweep"] = sw
        frames += fs
        if isinstance(manifest, dict):
            pi_best[sw] = {"best_lp": _num(manifest.get("best_lp")),
                           "best_lp_tenengrad": _num(manifest.get("best_lp_tenengrad"))}
        log(f"[focus] {name} sweep {sw}: {len(fs)} frames in {sd} (manifest {'yes' if manifest else 'NO, lens from file names'})")
    res["frames"], res["pi_best"] = frames, pi_best
    if not frames:
        res.update(verdict="FAIL", reasons=["no sweep JPEGs"], best_lp=None)
        log(f"[focus] {name}: FAIL no JPEGs")
        return res
    grays = [cv2.imread(f["path"], cv2.IMREAD_GRAYSCALE) for f in frames]
    readable = [g for g in grays if g is not None]
    if a.layout == "auto":
        step = max(1, len(readable) // 8)                    # layout from <= 8 frames spread over the sweeps
        layout, trials, why = pcc.choose_layout(readable[::step], a, cv2)
    else:
        c = pcc.layout_candidates(a)[0]
        layout, trials, why = {"squares_x": c[0], "squares_y": c[1], "legacy": c[2]}, [], None
    res["layout"] = {"chosen": layout, "trials": trials, "why_none": why}
    board = pcc.make_board(layout["squares_x"], layout["squares_y"], a, cv2, layout["legacy"]) if layout else None
    total = (layout["squares_x"] - 1) * (layout["squares_y"] - 1) if layout else 0
    res["total_corners"] = total
    det = cv2.aruco.CharucoDetector(board) if board else None
    for f, g in zip(frames, grays):
        f["w"], f["h"] = (g.shape[1], g.shape[0]) if g is not None else (None, None)
        f["corners_detected"] = f["markers"] = 0
        if g is None:
            f["error"] = "unreadable JPEG"
            continue
        if det is not None:
            d = pcc.detect(g, board, det, a, cv2, np)
            f["corners_detected"], f["markers"] = d["corners"], d["markers"]
            if d["corners"] >= 4:
                f["roi_detected"] = roi_from_corners(d["_corners"], f["w"], f["h"], cv2, np)
    # ONE common region for every frame of every sweep (QE #165): the best-detected frame's box
    found = [f for f in frames if f.get("roi_detected")]
    if found:
        ref = max(found, key=lambda f: (f["corners_detected"], -abs((f["lp"] or 0))))
        common, src = ref["roi_detected"], f"common board box (from {ref['sweep']} lp {ref['lp']:g}, {ref['corners_detected']} corners)"
    else:
        ref = None
        w0, h0 = next((f["w"], f["h"]) for f in frames if f["w"])
        common, src = (w0 // 3, h0 // 3, w0 // 3, h0 // 3), "frame centre (board never detected)"
    res["common_roi"], res["common_roi_source"] = list(common), src
    log(f"[focus] {name}: metric region for ALL frames = {src}: x{common[0]} y{common[1]} w{common[2]} h{common[3]}")
    for f, g in zip(frames, grays):
        if g is None:
            continue
        f["roi"], f["roi_source"] = common, src
        f.update(focus_metrics(g, common, cv2, np))
        f["status"] = ("FAIL" if f["corners_detected"] < 4 else
                       "PASS" if total and f["corners_detected"] >= 0.5 * total else "WARN")
    del grays
    if not found:
        res.update(verdict="FAIL", reasons=["board never detected: metrics are on the frame centre"], best_lp=None)
        for sw in dict.fromkeys(f["sweep"] for f in frames):
            res["sweeps"].append(sweep_best([f for f in frames if f["sweep"] == sw], log, name, sw))
        return res
    reasons = []
    for sw in dict.fromkeys(f["sweep"] for f in frames):
        sf = [f for f in frames if f["sweep"] == sw]
        sb = sweep_best(sf, log, name, sw)
        own = [f for f in sf if f.get("roi_detected")]
        if own:
            mine = max(own, key=lambda f: f["corners_detected"])["roi_detected"]
            sb["box_iou_vs_common"] = round(iou(mine, common), 3)
            if sb["box_iou_vs_common"] < IOU_MIN:
                reasons.append(f"sweep {sw}: board box moved (IoU {sb['box_iou_vs_common']:.2f} < {IOU_MIN} vs the common box)")
        sb["pi"] = pi_best.get(sw)
        res["sweeps"].append(sb)
    bests = [s["best"]["lp"] for s in res["sweeps"] if s.get("best")]
    steps = [s["step"] for s in res["sweeps"] if s.get("step")]
    step = min(steps) if steps else None
    spread = (max(bests) - min(bests)) if bests else None
    res["best_lp_per_sweep"] = bests
    res["best_lp_spread"] = spread
    res["best_lp"] = statistics.median(bests) if bests else None
    if len(bests) > 1 and step and spread > step + 1e-9:
        reasons.append(f"sweeps disagree: best lp {', '.join(f'{b:g}' for b in bests)} (spread {spread:g} > one step {step:g})")
    for s in res["sweeps"]:
        if s["verdict"] != "PASS":
            reasons += [f"sweep {s['sweep']}: {r}" for r in s["reasons"]]
    # per-lens run-to-run spread of lapvar_norm
    lens_spread = []
    for key in sorted({lens_key(f) for f in frames if lens_key(f) is not None}):
        vals = [f["lapvar_norm"] for f in frames if lens_key(f) == key and "lapvar_norm" in f]
        if len(vals) > 1 and statistics.mean(vals) > 0:
            lens_spread.append({"lp": key, "n": len(vals),
                                "rel_spread": round((max(vals) - min(vals)) / statistics.mean(vals), 4)})
    res["lens_spread"] = lens_spread
    res["reasons"] = reasons
    res["verdict"] = "WARN" if reasons else "PASS"
    log(f"[focus] {name}: {res['verdict']} best lp per sweep {', '.join(f'{b:g}' for b in bests)}"
        f" (spread {spread:g})" + (f"; {'; '.join(reasons)}" if reasons else ""))
    return res


# ----------------------------------------------------------------------------- drawing

def put(img, text, xy, cv2, scale=0.5, color=(235, 235, 235), thick=1):
    cv2.putText(img, text, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


STATUS_BGR = {"PASS": (80, 200, 80), "WARN": (0, 190, 255), "FAIL": (60, 60, 240)}


def unit_sheet(res, path, header, cv2, np):
    """Rows = lens positions (requested, 2 dp); thumbnail once; per sweep a 1:1 crop + metrics."""
    sweeps = [s["sweep"] for s in res["sweeps"]]
    x, y, w, h = res["common_roi"]
    cx, cy = x + w // 2, y + h // 2
    cell_w = CROP_W + TEXT_W + 8
    width = 4 + ROW_W + 8 + len(sweeps) * cell_w
    rows = []
    for key in sorted({lens_key(f) for f in res["frames"] if lens_key(f) is not None}):
        cells = {f["sweep"]: f for f in res["frames"] if lens_key(f) == key}
        first = cells[next(s for s in sweeps if s in cells)]
        img0 = cv2.imread(first["path"], cv2.IMREAD_COLOR)
        if img0 is None:
            continue
        H, W = img0.shape[:2]
        s = ROW_W / W
        th = cv2.resize(img0, (ROW_W, int(round(H * s))), interpolation=cv2.INTER_AREA)
        cv2.rectangle(th, (int(x * s), int(y * s)), (int((x + w) * s), int((y + h) * s)), (80, 220, 255), 2)
        put(th, f"lp {key:g} (requested)", (6, th.shape[0] - 8), cv2, 0.6, (255, 255, 255), 2)
        rh = max(th.shape[0], CROP_H) + 8
        row = np.full((rh, width, 3), 24, np.uint8)
        row[4:4 + th.shape[0], 4:4 + ROW_W] = th
        for k, sw in enumerate(sweeps):
            x_cell = 4 + ROW_W + 8 + k * cell_w
            f = cells.get(sw)
            if f is None:
                put(row, f"{sw}: no frame at lp {key:g}", (x_cell + 10, 40), cv2, 0.5, (150, 150, 150))
                continue
            img = img0 if f is first else cv2.imread(f["path"], cv2.IMREAD_COLOR)
            if img is None:
                put(row, f"{sw}: unreadable", (x_cell + 10, 40), cv2, 0.5, (60, 60, 240))
                continue
            x0 = min(max(0, cx - CROP_W // 2), max(0, img.shape[1] - CROP_W))
            y0 = min(max(0, cy - CROP_H // 2), max(0, img.shape[0] - CROP_H))
            crop = img[y0:y0 + CROP_H, x0:x0 + CROP_W].copy()
            crop = cv2.copyMakeBorder(crop, 0, CROP_H - crop.shape[0], 0, CROP_W - crop.shape[1], cv2.BORDER_CONSTANT)
            row[4:4 + CROP_H, x_cell:x_cell + CROP_W] = crop
            tx = x_cell + CROP_W + 8
            lines = [(f"{sw}" + ("  BEST" if f.get("best") else ""), 0.6, (80, 220, 255) if f.get("best") else (255, 255, 255), 2),
                     (f"applied {f['lp_applied']}" if f["lp_applied"] is not None else "applied -", 0.45, None, 1),
                     (f"lapvar_norm {f.get('lapvar_norm', 0):.4g}", 0.5, None, 1),
                     (f"tenengrad_norm {f.get('tenengrad_norm', 0):.4g}", 0.5, None, 1),
                     (f"corners detected {f['corners_detected']}/{res.get('total_corners', 0)}", 0.45, None, 1),
                     (f"pi lapvar (centre) {f['pi_metric']:.4g}" if f["pi_metric"] is not None else "pi lapvar -", 0.42, None, 1),
                     (f.get("status", "-"), 0.65, STATUS_BGR.get(f.get("status")), 2)]
            yy = 26
            for text, sc, col, tk in lines:
                put(row, text, (tx, yy), cv2, sc, col or (225, 225, 225), tk)
                yy += 28 if sc >= 0.6 else 25
            if f.get("best"):
                cv2.rectangle(row, (x_cell - 3, 2), (x_cell + cell_w - 6, rh - 3), (80, 220, 255), 3)
        rows.append(row)
    head = np.full((30 * (len(header) + 1) + 10, width, 3), 0, np.uint8)
    for k, (text, col) in enumerate(header):
        put(head, text, (10, 28 + 30 * k), cv2, 0.6, col)
    cols = np.full((26, width, 3), 40, np.uint8)
    put(cols, "full frame (SCALED to 480 px; box = the COMMON metric region)", (8, 18), cv2, 0.42)
    for k, sw in enumerate(sweeps):
        put(cols, f"sweep {sw}: 100% crop, 1:1 native px, 480x270, centred on the common box | metrics",
            (4 + ROW_W + 8 + k * cell_w + 4, 18), cv2, 0.42)
    cv2.imwrite(path, np.vstack([head, cols] + rows), [cv2.IMWRITE_JPEG_QUALITY, 88])


def _dashed(img, a_, b_, col, cv2, dash=10):
    x0, y0 = a_
    x1, y1 = b_
    n = max(1, int(math.hypot(x1 - x0, y1 - y0) // dash))
    for k in range(0, n, 2):
        p = (int(x0 + (x1 - x0) * k / n), int(y0 + (y1 - y0) * k / n))
        q = (int(x0 + (x1 - x0) * min(k + 1, n) / n), int(y0 + (y1 - y0) * min(k + 1, n) / n))
        cv2.line(img, p, q, col, 2, cv2.LINE_AA)


def plot(results, path, title, cv2, np):
    """Three panels; one series per (unit, sweep): colour = unit, solid = 1st sweep, dashed = later."""
    Wd, ph, ml, mr, mt = 1200, 300, 90, 30, 96
    series = []
    for k, r in enumerate(results):
        for m, s in enumerate(r.get("sweeps", [])):
            fs = sorted([f for f in r["frames"] if f["sweep"] == s["sweep"] and f.get("lp") is not None and "lapvar_norm" in f],
                        key=lambda f: f["lp"])
            if fs:
                series.append((r["unit"], s, fs, UNIT_COLORS[k % len(UNIT_COLORS)], m > 0))
    xs_all = [f["lp"] for _, _, fs, _, _ in series for f in fs]
    if not xs_all:
        return
    xlo, xhi = min(xs_all), max(xs_all)
    if xhi == xlo:
        xlo, xhi = xlo - 0.1, xhi + 0.1
    pad = 0.04 * (xhi - xlo)
    xlo, xhi = xlo - pad, xhi + pad
    panels = [("lapvar_norm (each series / its peak)", "lapvar_norm", True),
              ("tenengrad_norm (each series / its peak)", "tenengrad_norm", True),
              ("ChArUco corners detected", "corners_detected", False)]
    img = np.full((mt + len(panels) * (ph + 40) + 40, Wd, 3), 255, np.uint8)
    put(img, title, (ml, 28), cv2, 0.6, (20, 20, 20), 1)
    for n, (unit, s, fs, col, dashed) in enumerate(series):
        lx, ly = ml + (n % 3) * 360, 52 + (n // 3) * 20
        (_dashed if dashed else (lambda im, p, q, c, cv: cv.line(im, p, q, c, 3)))(img, (lx, ly - 5), (lx + 22, ly - 5), col, cv2)
        b = s.get("best") or {}
        put(img, f"{unit} {s['sweep']} ({s['verdict']}, best {b.get('lp', float('nan')):g})", (lx + 28, ly), cv2, 0.45, (20, 20, 20))
    for p, (label, key, norm) in enumerate(panels):
        top = mt + p * (ph + 40) + 20
        x0, x1, y0, y1 = ml, Wd - mr, top, top + ph
        cv2.rectangle(img, (x0, y0), (x1, y1), (120, 120, 120), 1)
        put(img, label, (x0 + 6, y0 - 6), cv2, 0.5, (20, 20, 20))
        ymax = 1.0 if norm else max([1] + [f.get(key, 0) or 0 for _, _, fs, _, _ in series for f in fs])
        for t in (0, 0.5, 1.0):
            yy = int(y1 - t * (y1 - y0))
            cv2.line(img, (x0, yy), (x1, yy), (225, 225, 225), 1)
            put(img, f"{t:.1f}" if norm else f"{t * ymax:.0f}", (x0 - 50, yy + 5), cv2, 0.45, (60, 60, 60))
        for lp in sorted(set(xs_all)):
            xx = int(x0 + (lp - xlo) / (xhi - xlo) * (x1 - x0))
            cv2.line(img, (xx, y1), (xx, y1 + 5), (60, 60, 60), 1)
            if p == len(panels) - 1:
                put(img, f"{lp:g}", (xx - 14, y1 + 22), cv2, 0.4, (60, 60, 60))
        for n, (unit, s, fs, col, dashed) in enumerate(series):
            peak = max(f.get(key, 0) or 0 for f in fs) or 1
            pts = [(int(x0 + (f["lp"] - xlo) / (xhi - xlo) * (x1 - x0)),
                    int(y1 - ((f.get(key, 0) or 0) / (peak if norm else ymax)) * (y1 - y0))) for f in fs]
            for a_, b_ in zip(pts, pts[1:]):
                if dashed:
                    _dashed(img, a_, b_, col, cv2)
                else:
                    cv2.line(img, a_, b_, col, 2, cv2.LINE_AA)
            for pt in pts:
                cv2.circle(img, pt, 4, col, -1)
            bf = [i for i, f in enumerate(fs) if f.get("best")]
            if bf:
                bp = pts[bf[0]]
                cv2.circle(img, bp, 10, col, 2)
                put(img, f"{fs[bf[0]]['lp']:g}", (bp[0] + 12, bp[1] + 4 + 14 * n), cv2, 0.42, col, 1)
    put(img, "LensPosition (applied when the manifest has it, else requested); solid = first sweep, dashed = later sweeps",
        (ml, img.shape[0] - 8), cv2, 0.42, (60, 60, 60))
    cv2.imwrite(path, img)


# ----------------------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(description="Focus-sweep review: per-unit cut sheet + metric plot + CSV/JSON.",
                                 epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", action="append", default=[], help="NAME=PATH (sweep folder, or unit/run folder: ALL its sweeps)")
    ap.add_argument("--sweep-dir", action="append", default=[], help="NAME=PATH explicit sweep folder (repeatable)")
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


def resolve_units(a):
    """-> ordered {name: [sweep dirs]} or an error string. --sweep-dir replaces discovery for a unit."""
    units, explicit = {}, {}
    for spec in a.sweep_dir:
        name, sep, path = spec.partition("=")
        p = os.path.abspath(os.path.expanduser(path)) if sep else ""
        if not sep or not name or not glob.glob(os.path.join(p, "*.jpg")):
            return f"--sweep-dir {spec!r}: use NAME=PATH to a sweep folder with *.jpg"
        explicit.setdefault(name, []).append(p)
    for spec in a.unit:
        name, sep, path = spec.partition("=")
        if not sep or not name:
            return f"--unit {spec!r}: use NAME=PATH"
        if name in explicit:
            continue
        ds = find_sweep_dirs(path)
        if not ds:
            return f"--unit {spec!r}: no sweep JPEGs under {path} (pool_pull.py --sweep first?)"
        units.setdefault(name, [])
        units[name] += [d for d in ds if d not in units[name]]
    for name, ds in explicit.items():
        units[name] = ds
    return units or "give --unit NAME=PATH and/or --sweep-dir NAME=PATH"


def main(argv=None):
    a = build_parser().parse_args(argv)
    units = resolve_units(a)
    if isinstance(units, str):
        print(f"[focus] {units}", file=sys.stderr)
        return 2
    cv2, np = pcc.need_cv2()
    first_dir = next(iter(units.values()))[0]
    tag = a.run_tag or os.path.basename(first_dir)
    out = a.out or os.path.join("runs", f"focus_sweep_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    os.makedirs(out, exist_ok=True)
    logf = open(os.path.join(out, "focus_sweep.log"), "a")

    def log(msg):
        line = f"{datetime.now().strftime('%I:%M:%S %p')} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    dist = f"{a.board_distance_m:g} m" if a.board_distance_m is not None else "not given"
    log(f"[focus] {a.medium.upper()} focus sweep {tag}: " + "; ".join(f"{n}: {len(d)} sweep(s)" for n, d in units.items())
        + f"; board distance {dist}; OpenCV {cv2.__version__}; out {out}")
    results = [analyse_unit(n, d, a, cv2, np, log) for n, d in units.items()]

    title = f"FOCUS SWEEP {a.medium.upper()}  {tag}  units {', '.join(r['unit'] for r in results)}  board distance {dist}"
    legend = ["frame: PASS = board detected, >= 50% corners | WARN = fewer | FAIL = not detected (still scored on the common box)",
              "unit: PASS = every sweep PASS (board at best, interior + distinct peak, metrics agree) + best-lp spread <= 1 step + box stable"]
    for r in results:
        if not r["frames"] or not r.get("common_roi"):
            continue
        per_sweep = ", ".join(f"{s['sweep']} {s['verdict']} best {s['best']['lp']:g}" for s in r["sweeps"] if s.get("best"))
        lay = r["layout"]["chosen"]
        header = [(title, (255, 255, 255)),
                  (f"{r['unit']}: {r['verdict']}   {per_sweep}   spread {r.get('best_lp_spread') if r.get('best_lp_spread') is None else format(r['best_lp_spread'], 'g')}"
                   + (f"   layout {lay['squares_x']}x{lay['squares_y']}{' legacy' if lay['legacy'] else ''}" if lay else ""),
                   STATUS_BGR.get(r["verdict"], (255, 255, 255))),
                  (f"metric region (all frames): {r['common_roi_source']}", (220, 220, 220)),
                  (("; ".join(r["reasons"]) or "no warnings")[:170], (220, 220, 220)),
                  ("Pi best lp (centre crop): " + (", ".join(f"{k} {v['best_lp']}" for k, v in r.get("pi_best", {}).items()) or "-"),
                   (180, 180, 180)),
                  (legend[0], (170, 170, 170)), (legend[1], (170, 170, 170))]
        unit_sheet(r, os.path.join(out, f"focus_sheet_{r['unit']}.jpg"), header, cv2, np)
    plot(results, os.path.join(out, "focus_plot.png"), title, cv2, np)

    cols = ["unit", "sweep", "file", "lp_req", "lp_applied", "lp", "status", "best", "corners_detected", "markers",
            "roi_source", "roi", "mean_intensity", "lapvar", "lapvar_norm", "tenengrad", "tenengrad_norm",
            "pi_metric", "pi_error", "error"]
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
        "metrics": {"region": "ONE common box per unit = the best-detected frame's ChArUco box, for every frame",
                    "lapvar_norm": "var(Laplacian 3x3) / mean(I)^2 on the region",
                    "tenengrad_norm": "mean(Sobel Gx^2 + Gy^2) / mean(I)^2 on the region",
                    "best": "per sweep: argmax lapvar_norm (+ 3-point parabola when interior)",
                    "spread": "best lp max - min across sweeps; lens_spread = (max - min) / mean of lapvar_norm per lens"},
        "units": [{k: v for k, v in r.items() if k != "frames"} | {
            "frames": [{k: v for k, v in fr.items() if not k.startswith("_") and k != "path"} for fr in r["frames"]]}
            for r in results],
    }
    with open(os.path.join(out, "focus_sweep.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    log(f"[focus] wrote {out}/: " + ", ".join(sorted(os.listdir(out))))
    for r in results:
        log(f"[focus] VERDICT {r['unit']}: {r['verdict']}"
            + (f"  best lp per sweep {', '.join(f'{b:g}' for b in r.get('best_lp_per_sweep', []))}"
               f" (spread {r['best_lp_spread']:g})" if r.get("best_lp_per_sweep") else "")
            + (f"  ({'; '.join(r['reasons'])})" if r.get("reasons") else ""))
    return 1 if any(r["verdict"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
