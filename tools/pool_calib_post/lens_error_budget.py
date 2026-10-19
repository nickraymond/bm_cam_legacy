#!/usr/bin/env python3
# filename: lens_error_budget.py
# description: Per-camera lens-error budget for urchins per m^2 (density error if the correction is skipped, in-air vs underwater focal length, remaining spread) from finished pool_calib_pipeline runs.
"""
lens_error_budget.py: what does each lens error cost a density (count / area), per camera?

Purpose
  Companion to q1_observed_area.py for Nick's d58 question 1. Reads finished pipeline camera folders (no refit) and
  reports, per region:
    - the density error if the distortion correction is skipped (A_true / A_pinhole - 1);
    - the worst local size error (signed, largest magnitude);
    - the spread of the region's true area across the three model families and across the bootstrap replicates.
  Also the density error of using the in-air datasheet focal length instead of the underwater one.

Regions
  seen  = convex hull of the board corners the fit used (MEASURED);
  crop / crop_seen = a production crop (--crop), and its part inside 'seen';
  full_EXTRAPOLATED_not_measured = the whole frame. EXTRAPOLATED outside 'seen': context only, never a result.

Inputs
  NAME=CAM_DIR   finished pipeline camera folder (intrinsics_full_*.json, models.json, coverage.json, bootstrap.csv)
  --nominal-fx NAME=PX   in-air datasheet focal length (px) for the in-air comparison (required per camera)
  --crop NAME=x,y,w,h, --step NAME=PX, --out JSON

Example
  .venv-dev/bin/python tools/pool_calib_post/lens_error_budget.py \\
      imx708=$D/20261017_152713_pool049_imx708_dng/imx708 n6=$D/20261017_153029_pool049_openmv_raw/n6 \\
      ae3=$D/20261017_153029_pool049_openmv_raw/ae3 --nominal-fx imx708=1964.3 --nominal-fx n6=933.3 \\
      --nominal-fx ae3=1033.3 --crop imx708=1504,846,1600,900 --out runs/pool_calib_20261016/lens_error_budget.json

Known limitations
  Area terms assume a camera square-on to a flat seafloor. The bootstrap spread uses a 2x coarser grid.
"""
import argparse
import csv
import json
import os
import sys

import numpy as np
from matplotlib.path import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pool_calib_models as pm  # noqa: E402

FULL = "full_EXTRAPOLATED_not_measured"


def det_j(model, K, D_, pts):
    e = 0.5
    rx = pm.rays(model, K, D_, pts + [e, 0]) - pm.rays(model, K, D_, pts - [e, 0])
    ry = pm.rays(model, K, D_, pts + [0, e]) - pm.rays(model, K, D_, pts - [0, e])
    return (rx[:, 0] * ry[:, 1] - rx[:, 1] * ry[:, 0]) / (2 * e) ** 2


def grid(w, h, step):
    xs, ys = np.arange(step / 2, w, step), np.arange(step / 2, h, step)
    return np.stack(np.meshgrid(xs, ys), -1).reshape(-1, 2)


def region_stats(model, K, D_, pts, step):
    """-> area_true (normalised units, = m^2 at 1 m range), area_pinhole, density error if the correction is
    skipped, invertible fraction, local linear scale s = sqrt(detJ fx fy) (1 at the centre)."""
    dj = det_j(model, K, D_, pts)
    ok = np.isfinite(dj) & (dj > 0)
    fxfy = K[0, 0] * K[1, 1]
    a_true = float(np.sum(dj[ok])) * step * step
    a_pin = float(ok.sum()) * step * step / fxfy
    s = np.sqrt(np.clip(dj, 1e-30, None) * fxfy)
    return {"area_true": a_true, "area_pinhole": a_pin,
            "density_error_if_ignored_pct": 100 * (a_true / a_pin - 1) if a_pin else None,
            "frac_invertible": float(ok.mean()), "scale_min": float(np.min(s[ok])) if ok.any() else None,
            "scale_max": float(np.max(s[ok])) if ok.any() else None}


def budget(cam_dir, nominal, step, crop=None):
    k = [json.load(open(os.path.join(cam_dir, f))) for f in sorted(os.listdir(cam_dir)) if f.startswith("intrinsics_full_")][0]
    models = json.load(open(os.path.join(cam_dir, "models.json")))
    cov = json.load(open(os.path.join(cam_dir, "coverage.json")))
    w, h = k["image_size"]
    K, D_, ch = np.array(k["camera_matrix"]), np.array(k["dist_coeffs"]), k["model"]
    G = grid(w, h, step)
    hull = Path(np.array(cov["observed_hull_px"]))
    seen = hull.contains_points(G)
    regions = {"seen": seen, FULL: np.ones(len(G), bool)}
    if crop:
        x, y, cw, chh = crop
        c = (G[:, 0] >= x) & (G[:, 0] < x + cw) & (G[:, 1] >= y) & (G[:, 1] < y + chh)
        regions["crop"] = c
        regions["crop_seen"] = c & seen
    fxfy = K[0, 0] * K[1, 1]
    res = {"model": ch, "fx": float(K[0, 0]), "nominal_fx_air": nominal,
           "area_ratio_in_air_vs_water_pct": 100 * (fxfy / nominal ** 2 - 1),
           "density_error_if_in_air_fx_pct": 100 * (nominal ** 2 / fxfy - 1),
           "px_per_cm_at_1m": float(K[0, 0]) / 100.0, "regions": {},
           "note": f"'{FULL}' is the model extrapolating outside the observed area: context only, never a result"}
    for name, m in regions.items():
        st = region_stats(ch, K, D_, G[m], step)
        r = dict(st)
        r["frac_of_frame"] = float(m.mean())
        errs = [100 * (1 / st["scale_min"] - 1), 100 * (1 / st["scale_max"] - 1)] if st["scale_min"] else [None]
        r["size_error_max_pct"] = max(errs, key=abs) if errs[0] is not None else None   # signed, largest magnitude
        fam = {mn: region_stats(mn, np.array(mf["K"]), np.array(mf["D"]), G[m], step)["area_true"]
               for mn, mf in models["models"].items()}
        vals = [v for v in fam.values() if v > 0]
        r["area_by_model"] = fam
        r["area_model_spread_pct"] = 100 * (max(vals) - min(vals)) / np.mean(vals) if len(vals) > 1 else None
        res["regions"][name] = r
    # bootstrap spread of the measured regions' true area (chosen model), 2x coarser grid
    Gb = grid(w, h, step * 2)
    mb = {"seen": hull.contains_points(Gb)}
    if crop:
        x, y, cw, chh = crop
        mb["crop"] = (Gb[:, 0] >= x) & (Gb[:, 0] < x + cw) & (Gb[:, 1] >= y) & (Gb[:, 1] < y + chh)
    boots = {n: [] for n in mb}
    with open(os.path.join(cam_dir, "bootstrap.csv")) as fh:
        for row in csv.DictReader(fh):
            if row["error"]:
                continue
            Kb = np.array([[float(row["fx"]), 0, float(row["cx"])], [0, float(row["fy"]), float(row["cy"])], [0, 0, 1]])
            Db = np.array([float(v) for v in row["dist_coeffs"].split()])
            for n, m in mb.items():
                boots[n].append(region_stats(ch, Kb, Db, Gb[m], step * 2)["area_true"])
    res["bootstrap_area_std_pct"] = {n: 100 * float(np.std(v, ddof=1) / np.mean(v)) for n, v in boots.items()}
    return res


def parse_kv(items, conv):
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k] = conv(v)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cams", nargs="+", help="NAME=CAM_DIR")
    ap.add_argument("--nominal-fx", action="append", default=[], help="NAME=PX (in-air datasheet focal length)")
    ap.add_argument("--crop", action="append", default=[], help="NAME=x,y,w,h")
    ap.add_argument("--step", action="append", default=[], help="NAME=PX")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    nom, crops, steps = parse_kv(a.nominal_fx, float), parse_kv(a.crop, lambda v: tuple(int(x) for x in v.split(","))), \
        parse_kv(a.step, int)
    out = {}
    for it in a.cams:
        name, _, cam_dir = it.partition("=")
        cam_dir = os.path.expanduser(cam_dir)
        if name not in nom:
            print(f"[budget] REFUSED: --nominal-fx {name}=PX missing", file=sys.stderr)
            return 2
        w = [json.load(open(os.path.join(cam_dir, f))) for f in sorted(os.listdir(cam_dir))
             if f.startswith("intrinsics_full_")][0]["image_size"][0]
        r = budget(cam_dir, nom[name], steps.get(name, 16 if w >= 3000 else 4), crops.get(name))
        out[name] = r
        print(f"== {name} ({r['model']}) fx {r['fx']:.0f}: in-air focal -> density {r['density_error_if_in_air_fx_pct']:+.0f} %; "
              f"bootstrap area std {({k: round(v, 2) for k, v in r['bootstrap_area_std_pct'].items()})}")
        for n, g in r["regions"].items():
            print(f"   {n:32s} {100 * g['frac_of_frame']:5.1f}% of frame: skip correction -> density "
                  f"{g['density_error_if_ignored_pct']:+.1f} %; model-family area spread {g['area_model_spread_pct']:.2f} %")
    with open(os.path.expanduser(a.out), "w") as fh:
        json.dump(out, fh, indent=1, default=float)
    print(f"[budget] wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
