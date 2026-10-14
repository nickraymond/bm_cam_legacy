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
  - ONE metric region PER SWEEP (QE #165/#166): the ChArUco board box of that sweep's
    best-detected frame (most corners; tools/pool_calib_check.py detector + layout; default Nick's
    calib.io CCT600400C 9x14 40/30 mm DICT_5X5_100, --layout auto), used for ALL lens positions of
    the sweep, so they are scored on the SAME pixels; a 1 m sweep's box never scores 2 m frames.
    Each frame's own detection is kept as corners_detected (it sets the frame status, not the
    region). No detection in a sweep -> its frame centre (sweep FAIL).
  - REPEAT GROUPS: sweeps with the same tag (--board-distance-m for --unit discovery, or
    --sweep-dir NAME=TAG:PATH) are repeats; only within a group: best-lp median + spread (WARN if
    > one lens step) + per-lens spread + box check (IoU < 0.7 vs the group's first sweep -> WARN
    "not true repeats?"). Different tags (e.g. 1 m vs 2 m) are reported side by side, never
    aggregated.
  - metrics on that region, computed here from the JPEG (grayscale, float):
      lapvar_norm    var(Laplacian 3x3) / mean(I)^2   (normalised by mean intensity, squared so
                     an exposure change k*I cancels: var scales with k^2)
      tenengrad_norm mean(Gx^2 + Gy^2) / mean(I)^2    (Sobel 3x3, same normalisation)
      corners_detected  ChArUco corners found in that frame (of (sx-1)*(sy-1) = 104)
    plus the on-Pi lapvar + best_lp from sweep_manifest.json (schema bmcam_focus_sweep_v1: on a
    CENTRED 1024x1024 crop, not on the board; reported, not used for "best").
  - per sweep: best LensPosition = argmax lapvar_norm (+ 3-point parabola when interior);
    tenengrad's argmax shown next to it.

Status legend (CLAUDE.md §13):
  frame  PASS  board found in this frame, >= 50 % of its corners
         WARN  board found, fewer corners
         FAIL  board not found in this frame (still scored on the common region)
  sweep  PASS  board found at the best lp, peak interior (not the first/last lens), peak distinct
               (best >= 1.15 x the median), lapvar/tenengrad agree within one step; else WARN
  group  (repeats only) best lp spread <= one lens step and the same board box (IoU >= 0.7), else WARN
  unit   PASS  every sweep PASS and every group OK; WARN otherwise; FAIL = no frames or no board at all

Inputs
  --unit NAME=PATH       repeatable; PATH = a sweep folder (with *.jpg), or a unit / run folder:
                         EVERY sweep/<HHMMSS>/ under it is used, ALL tagged --board-distance-m
                         (= one repeat group: use it for repeats at one distance)
  --sweep-dir NAME=[TAG:]PATH  repeatable; explicit sweep folders for NAME (replaces --unit
                         discovery for that unit). TAG = the distance in m (1.0) or a label: the
                         repeat group. e.g. bmcam003=1.0:<sweep A> bmcam003=1.0:<sweep B>
                         bmcam003=2.0:<sweep C>  -> A + B aggregated, C side by side
  --board-distance-m D   board distance for the title (not used in maths); "not given" otherwise
  --medium in-air|underwater (default in-air; the pool sweep is underwater: say so)
  --run-tag TAG          title + file names (default: the first sweep folder name)
  board flags as pool_calib_check.py (--squares-x/-y, --square-mm, --marker-mm, --dictionary, --layout)
Outputs (in --out, default ./runs/focus_sweep_<UTC>/)
  focus_sheet_<unit>.jpg  one row per requested LensPosition; per sweep, side by side: full frame
                          (SCALED to 320 px wide, that sweep's box drawn) | 100 % crop (1:1 native
                          pixels, 480x270, centred on that box) | metrics + status; best cells marked
  focus_plot.png          lapvar_norm and tenengrad_norm (each series normalised to its peak = 1)
                          and corners vs LensPosition; colour = unit, solid / dashed = 1st / later
                          sweep; best marked
  focus_sweep.csv         one row per frame (unit, sweep, ...)
  focus_sweep.json        settings, versions, per unit: per-sweep box + best + verdict, per-group
                          median + spread, verdict; per-frame records
  focus_sweep.log         everything printed
Exit 0 = every unit PASS or WARN; 1 = a unit FAILs; 2 = usage.

Example (Mac, repo root, after `python3 tools/pool_pull.py --sweep ...`):
  python3 tools/focus_sweep_sheet.py --unit bmcam003=~/NereusPool/20261013/bmcam003 \\
      --unit bmcam004=~/NereusPool/20261013/bmcam004 --board-distance-m 1.0
  python3 tools/focus_sweep_sheet.py --sweep-dir bmcam003=1.0:<.../sweep/113005> --sweep-dir bmcam003=2.0:<.../sweep/141210>

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

ROW_W, CROP_W, CROP_H, TEXT_W = 320, 480, 270, 290
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


def analyse_unit(name, sweeps_in, a, cv2, np, log):
    """sweeps_in = [(sweep_dir, group, distance_m)]. ONE region PER SWEEP (that sweep's best-detected
    frame's board box, for all its lens positions; QE #166: a 1 m box must never score 2 m frames);
    best-lp median + spread WARN only WITHIN a repeat group (same distance tag / --sweep-dir label)."""
    res = {"unit": name, "frames": [], "sweeps": [], "groups": []}
    frames, pi_best, meta = [], {}, {}
    for sd, group, dist in sweeps_in:
        fs, manifest = load_frames(sd)
        sw = os.path.basename(sd.rstrip("/"))
        meta[sw] = (sd, group, dist)
        for f in fs:
            f["sweep"], f["group"], f["distance_m"] = sw, group, dist
        frames += fs
        if isinstance(manifest, dict):
            pi_best[sw] = {"best_lp": _num(manifest.get("best_lp")),
                           "best_lp_tenengrad": _num(manifest.get("best_lp_tenengrad"))}
        log(f"[focus] {name} sweep {sw} [group {group}]: {len(fs)} frames in {sd} "
            f"(manifest {'yes' if manifest else 'NO, lens from file names'})")
    res["frames"], res["pi_best"] = frames, pi_best
    if not frames:
        res.update(verdict="FAIL", reasons=["no sweep JPEGs"])
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
    gray_of = {id(f): g for f, g in zip(frames, grays)}
    any_board = False
    for sw, (sd, group, dist) in meta.items():
        sf = [f for f in frames if f["sweep"] == sw]
        found = [f for f in sf if f.get("roi_detected")]
        if found:                                            # this sweep's best-detected frame's box
            any_board = True
            ref = max(found, key=lambda f: f["corners_detected"])
            box, src = ref["roi_detected"], f"sweep board box (lp {ref['lp']:g}, {ref['corners_detected']} corners)"
        else:
            w0, h0 = next(((f["w"], f["h"]) for f in sf if f["w"]), (None, None))
            box = (w0 // 3, h0 // 3, w0 // 3, h0 // 3) if w0 else None
            src = "frame centre (board never detected in this sweep)"
        for f in sf:
            g = gray_of[id(f)]
            if g is None or box is None:
                continue
            f["roi"], f["roi_source"] = box, src
            f.update(focus_metrics(g, box, cv2, np))
            f["status"] = ("FAIL" if f["corners_detected"] < 4 else
                           "PASS" if total and f["corners_detected"] >= 0.5 * total else "WARN")
        sb = sweep_best(sf, log, name, sw)
        sb.update(group=group, distance_m=dist, box=list(box) if box else None, box_source=src,
                  board_found=bool(found), pi=pi_best.get(sw))
        if not found:
            sb["verdict"] = "FAIL"
            sb["reasons"] = ["board never detected in this sweep: metrics are on the frame centre"] + sb["reasons"]
        res["sweeps"].append(sb)
        log(f"[focus] {name} sweep {sw}: metric region = {src}" + (f": x{box[0]} y{box[1]} w{box[2]} h{box[3]}" if box else ""))
    del grays, gray_of
    reasons = []
    for s_ in res["sweeps"]:
        if s_["verdict"] != "PASS":
            reasons += [f"sweep {s_['sweep']}: {r}" for r in s_["reasons"]]
    for group in dict.fromkeys(s_["group"] for s_ in res["sweeps"]):
        gs = [s_ for s_ in res["sweeps"] if s_["group"] == group]
        bests = [s_["best"]["lp"] for s_ in gs if s_.get("best") and s_["board_found"]]
        g = {"group": group, "sweeps": [s_["sweep"] for s_ in gs], "best_lp_per_sweep": bests,
             "best_lp": statistics.median(bests) if bests else None,
             "best_lp_spread": (max(bests) - min(bests)) if len(bests) > 1 else None, "reasons": []}
        if len(gs) > 1:                                      # a repeat group: aggregate + check
            steps = [s_["step"] for s_ in gs if s_.get("step")]
            step = min(steps) if steps else None
            if g["best_lp_spread"] is not None and step and g["best_lp_spread"] > step + 1e-9:
                g["reasons"].append(f"repeats disagree: best lp {', '.join(f'{b:g}' for b in bests)} "
                                    f"(spread {g['best_lp_spread']:g} > one step {step:g})")
            boxes = [s_["box"] for s_ in gs if s_["board_found"]]
            for s_ in gs[1:]:
                if s_["board_found"] and boxes:
                    s_["box_iou_vs_first"] = round(iou(s_["box"], boxes[0]), 3)
                    if s_["box_iou_vs_first"] < IOU_MIN:
                        g["reasons"].append(f"sweep {s_['sweep']} board box differs from {gs[0]['sweep']} (IoU "
                                            f"{s_['box_iou_vs_first']:.2f} < {IOU_MIN}): not true repeats? "
                                            f"(different distance -> give it its own tag)")
            spread = []
            for key in sorted({lens_key(f) for f in frames if f["group"] == group and lens_key(f) is not None}):
                vals = [f["lapvar_norm"] for f in frames if f["group"] == group and lens_key(f) == key and "lapvar_norm" in f]
                if len(vals) > 1 and statistics.mean(vals) > 0:
                    spread.append({"lp": key, "n": len(vals),
                                   "rel_spread": round((max(vals) - min(vals)) / statistics.mean(vals), 4)})
            g["lens_spread"] = spread
            log(f"[focus] {name} group {group}: repeats {', '.join(g['sweeps'])}: best lp "
                f"{', '.join(f'{b:g}' for b in bests)} -> median {g['best_lp'] if g['best_lp'] is None else format(g['best_lp'], 'g')}"
                + (f", spread {g['best_lp_spread']:g}" if g["best_lp_spread"] is not None else "")
                + (f"; {'; '.join(g['reasons'])}" if g["reasons"] else ""))
        reasons += [f"group {group}: {r}" for r in g["reasons"]]
        res["groups"].append(g)
    res["reasons"] = reasons
    res["verdict"] = "FAIL" if not any_board else ("WARN" if reasons else "PASS")
    log(f"[focus] {name}: {res['verdict']}" + "".join(
        f"  [{g['group']}] best lp {g['best_lp'] if g['best_lp'] is None else format(g['best_lp'], 'g')}" for g in res["groups"]))
    return res


# ----------------------------------------------------------------------------- drawing

def put(img, text, xy, cv2, scale=0.5, color=(235, 235, 235), thick=1):
    cv2.putText(img, text, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


STATUS_BGR = {"PASS": (80, 200, 80), "WARN": (0, 190, 255), "FAIL": (60, 60, 240)}


def unit_sheet(res, path, header, cv2, np):
    """Rows = requested lens (2 dp); per sweep, side by side: scaled thumbnail with THAT sweep's box,
    1:1 crop centred on that box, metrics."""
    sweeps = [s_ for s_ in res["sweeps"] if s_.get("box")]
    cell_w = ROW_W + CROP_W + TEXT_W + 16
    width = 4 + len(sweeps) * cell_w
    rows = []
    for key in sorted({lens_key(f) for f in res["frames"] if lens_key(f) is not None}):
        cells = {f["sweep"]: f for f in res["frames"] if lens_key(f) == key}
        row = np.full((max(CROP_H, int(ROW_W * 9 / 16)) + 12, width, 3), 24, np.uint8)
        for k, s_ in enumerate(sweeps):
            xc = 4 + k * cell_w
            f = cells.get(s_["sweep"])
            img = cv2.imread(f["path"], cv2.IMREAD_COLOR) if f else None
            if img is None:
                put(row, f"{s_['sweep']}: no frame at lp {key:g}", (xc + 10, 40), cv2, 0.5, (150, 150, 150))
                continue
            H, W = img.shape[:2]
            x, y, w, h = s_["box"]
            sc = ROW_W / W
            th = cv2.resize(img, (ROW_W, int(round(H * sc))), interpolation=cv2.INTER_AREA)
            cv2.rectangle(th, (int(x * sc), int(y * sc)), (int((x + w) * sc), int((y + h) * sc)), (80, 220, 255), 2)
            put(th, f"lp {key:g} req", (5, th.shape[0] - 7), cv2, 0.5, (255, 255, 255), 1)
            row[4:4 + th.shape[0], xc:xc + ROW_W] = th[:row.shape[0] - 4]
            cx, cy = x + w // 2, y + h // 2
            x0 = min(max(0, cx - CROP_W // 2), max(0, W - CROP_W))
            y0 = min(max(0, cy - CROP_H // 2), max(0, H - CROP_H))
            crop = img[y0:y0 + CROP_H, x0:x0 + CROP_W].copy()
            crop = cv2.copyMakeBorder(crop, 0, CROP_H - crop.shape[0], 0, CROP_W - crop.shape[1], cv2.BORDER_CONSTANT)
            row[4:4 + CROP_H, xc + ROW_W + 4:xc + ROW_W + 4 + CROP_W] = crop
            tx = xc + ROW_W + CROP_W + 10
            lines = [(f"{s_['sweep']}" + ("  BEST" if f.get("best") else ""), 0.55, (80, 220, 255) if f.get("best") else (255, 255, 255), 2),
                     (f"applied {f['lp_applied']}" if f["lp_applied"] is not None else "applied -", 0.42, None, 1),
                     (f"lapvar_norm {f.get('lapvar_norm', 0):.4g}", 0.47, None, 1),
                     (f"tenengrad_norm {f.get('tenengrad_norm', 0):.4g}", 0.47, None, 1),
                     (f"corners detected {f['corners_detected']}/{res.get('total_corners', 0)}", 0.42, None, 1),
                     (f"pi lapvar (centre) {f['pi_metric']:.4g}" if f["pi_metric"] is not None else "pi lapvar -", 0.4, None, 1),
                     (f.get("status", "-"), 0.6, STATUS_BGR.get(f.get("status")), 2)]
            yy = 24
            for text, size, col, tk in lines:
                put(row, text, (tx, yy), cv2, size, col or (225, 225, 225), tk)
                yy += 27 if size >= 0.55 else 24
            if f.get("best"):
                cv2.rectangle(row, (xc - 2, 2), (xc + cell_w - 8, row.shape[0] - 3), (80, 220, 255), 3)
        rows.append(row)
    head = np.full((30 * (len(header) + 1) + 10, width, 3), 0, np.uint8)
    for k, (text, col) in enumerate(header):
        put(head, text, (10, 28 + 30 * k), cv2, 0.6, col)
    cols = np.full((26, width, 3), 40, np.uint8)
    for k, s_ in enumerate(sweeps):
        put(cols, f"sweep {s_['sweep']} [group {s_['group']}]: full frame SCALED to {ROW_W} px (box = this sweep's region) | "
                  f"100% crop 1:1 native px {CROP_W}x{CROP_H} | metrics", (4 + k * cell_w + 4, 18), cv2, 0.4)
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
        put(img, f"{unit} {s['sweep']} [{s['group']}] ({s['verdict']}, best {b.get('lp', float('nan')):g})", (lx + 28, ly), cv2, 0.42, (20, 20, 20))
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


RE_TAGGED = re.compile(r"^([^/:~]+):(.+)$")       # TAG:PATH, TAG without '/' (e.g. 1.0 or 1m)


def default_group(a):
    return f"{a.board_distance_m:g} m" if a.board_distance_m is not None else "untagged"


def resolve_units(a):
    """-> ordered {name: [(sweep_dir, group, distance_m)]} or an error string.
    --sweep-dir NAME=[TAG:]PATH: TAG = a distance in m (1.0) or a label; it names the repeat group.
    --unit NAME=PATH: every sweep found; all in one group = the --board-distance-m tag (a repeat set).
    --sweep-dir replaces --unit discovery for that unit."""
    units, explicit = {}, {}
    for spec in a.sweep_dir:
        name, sep, rest = spec.partition("=")
        m = RE_TAGGED.match(rest) if sep else None
        tag, path = (m.group(1), m.group(2)) if m else (None, rest)
        p = os.path.abspath(os.path.expanduser(path)) if sep else ""
        if not sep or not name or not glob.glob(os.path.join(p, "*.jpg")):
            return f"--sweep-dir {spec!r}: use NAME=[TAG:]PATH to a sweep folder with *.jpg"
        dist = _num(tag)
        group = (f"{dist:g} m" if dist is not None else tag) if tag else default_group(a)
        if dist is None:
            dist = a.board_distance_m
        explicit.setdefault(name, []).append((p, group, dist))
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
        units[name] += [(d, default_group(a), a.board_distance_m) for d in ds if d not in [x[0] for x in units[name]]]
    for name, ds in explicit.items():
        units[name] = ds
    return units or "give --unit NAME=PATH and/or --sweep-dir NAME=[TAG:]PATH"


def main(argv=None):
    a = build_parser().parse_args(argv)
    units = resolve_units(a)
    if isinstance(units, str):
        print(f"[focus] {units}", file=sys.stderr)
        return 2
    cv2, np = pcc.need_cv2()
    first_dir = next(iter(units.values()))[0][0]
    tag = a.run_tag or os.path.basename(first_dir)
    out = a.out or os.path.join("runs", f"focus_sweep_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    os.makedirs(out, exist_ok=True)
    logf = open(os.path.join(out, "focus_sweep.log"), "a")

    def log(msg):
        line = f"{datetime.now().strftime('%I:%M:%S %p')} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    dist = (f"{a.board_distance_m:g} m" if a.board_distance_m is not None else "not given") + " (default tag; per sweep via --sweep-dir NAME=TAG:PATH)"
    log(f"[focus] {a.medium.upper()} focus sweep {tag}: " + "; ".join(f"{n}: {len(d)} sweep(s)" for n, d in units.items())
        + f"; board distance {dist}; OpenCV {cv2.__version__}; out {out}")
    results = [analyse_unit(n, d, a, cv2, np, log) for n, d in units.items()]

    title = f"FOCUS SWEEP {a.medium.upper()}  {tag}  units {', '.join(r['unit'] for r in results)}  board distance {dist}"
    legend = ["frame: PASS = board detected, >= 50% corners | WARN = fewer | FAIL = not detected (still scored on its sweep's box)",
              "sweep: PASS = board at best + interior + distinct peak + metrics agree | repeat group: spread <= 1 step + same box | else WARN"]
    for r in results:
        if not r["frames"] or not any(s_.get("box") for s_ in r["sweeps"]):
            continue
        per_sweep = ", ".join(f"{s_['sweep']}[{s_['group']}] {s_['verdict']} best {s_['best']['lp']:g}"
                              for s_ in r["sweeps"] if s_.get("best"))
        groups = "   ".join(f"[{g['group']}] median {g['best_lp'] if g['best_lp'] is None else format(g['best_lp'], 'g')}"
                             + (f" spread {g['best_lp_spread']:g}" if g.get("best_lp_spread") is not None else "")
                             for g in r.get("groups", []))
        lay = r["layout"]["chosen"]
        header = [(title, (255, 255, 255)),
                  (f"{r['unit']}: {r['verdict']}   {per_sweep}"
                   + (f"   layout {lay['squares_x']}x{lay['squares_y']}{' legacy' if lay['legacy'] else ''}" if lay else ""),
                   STATUS_BGR.get(r["verdict"], (255, 255, 255))),
                  (f"groups (repeats aggregated only within a group): {groups}", (220, 220, 220)),
                  (("; ".join(r["reasons"]) or "no warnings")[:170], (220, 220, 220)),
                  ("Pi best lp (centre crop): " + (", ".join(f"{k} {v['best_lp']}" for k, v in r.get("pi_best", {}).items()) or "-"),
                   (180, 180, 180)),
                  (legend[0], (170, 170, 170)), (legend[1], (170, 170, 170))]
        unit_sheet(r, os.path.join(out, f"focus_sheet_{r['unit']}.jpg"), header, cv2, np)
    plot(results, os.path.join(out, "focus_plot.png"), title, cv2, np)

    cols = ["unit", "sweep", "group", "distance_m", "file", "lp_req", "lp_applied", "lp", "status", "best", "corners_detected", "markers",
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
        "metrics": {"region": "ONE box per SWEEP = that sweep's best-detected frame's ChArUco box, for all its lens positions",
                    "groups": "repeats = sweeps with the same tag (--board-distance-m, or --sweep-dir NAME=TAG:PATH); median + spread only within a group",
                    "lapvar_norm": "var(Laplacian 3x3) / mean(I)^2 on the region",
                    "tenengrad_norm": "mean(Sobel Gx^2 + Gy^2) / mean(I)^2 on the region",
                    "best": "per sweep: argmax lapvar_norm (+ 3-point parabola when interior)",
                    "spread": "within a group: best lp max - min; lens_spread = (max - min) / mean of lapvar_norm per lens"},
        "units": [{k: v for k, v in r.items() if k != "frames"} | {
            "frames": [{k: v for k, v in fr.items() if not k.startswith("_") and k != "path"} for fr in r["frames"]]}
            for r in results],
    }
    with open(os.path.join(out, "focus_sweep.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    log(f"[focus] wrote {out}/: " + ", ".join(sorted(os.listdir(out))))
    for r in results:
        log(f"[focus] VERDICT {r['unit']}: {r['verdict']}" + "".join(
            f"  [{g['group']}] best lp {', '.join(f'{b:g}' for b in g['best_lp_per_sweep'])}"
            + (f" (median {g['best_lp']:g}, spread {g['best_lp_spread']:g})" if g.get("best_lp_spread") is not None else "")
            for g in r.get("groups", []))
            + (f"  ({'; '.join(r['reasons'])})" if r.get("reasons") else ""))
    return 1 if any(r["verdict"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
