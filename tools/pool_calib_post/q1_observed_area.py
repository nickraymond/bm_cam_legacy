#!/usr/bin/env python3
# filename: q1_observed_area.py
# description: Lens-error numbers per camera from finished pool_calib_pipeline runs, inside the observed area only (spec rev 3 "Question 1").
"""
q1_observed_area.py: how big are the lens errors, measured only where the calibration board was?

Purpose
  Answer Nick's d58 question 1 (SPEC_lens_calib_pool049 rev 3) per camera from the fitted (provisional) models,
  evaluated ONLY inside the observed area = the convex hull of the board corners the fit used
  (coverage.json observed_hull_px). Nothing is refitted. A number that would need the model outside the
  observed area (e.g. the whole-frame density error) is not computed.

Definitions (native px of each camera's frame; r = distance from the principal point / half-diagonal)
  radial displacement d(p) = |p - pinhole(ray(p))| px: how far the lens moves a point from where an ideal
    camera with the same K puts it; also as % of the point's radius.
  local area scale A(p) = det J(p) * fx * fy, J = d(normalised undistorted ray)/d(pixel): seafloor area one pixel
    covers relative to the centre (A = 1 there). An object's size in px is 1/sqrt(A) times its size at the centre.
  density error if the correction is skipped over a region = mean(A) - 1.

Inputs
  NAME=CAM_DIR   a finished pipeline camera folder (intrinsics_full_*.json, coverage.json), repeatable
  --step NAME=PX grid step (default 16 px for frames >= 3000 px wide, else 4 px)
  --crop NAME=x,y,w,h   a production crop to report (e.g. imx708=1504,846,1600,900)
  --out JSON, --chart PNG (area scale vs radius, observed area only; optional)

Example
  .venv-dev/bin/python tools/pool_calib_post/q1_observed_area.py \\
      imx708=$D/20261017_152713_pool049_imx708_dng/imx708 n6=$D/20261017_153029_pool049_openmv_raw/n6 \\
      ae3=$D/20261017_153029_pool049_openmv_raw/ae3 --crop imx708=1504,846,1600,900 \\
      --out runs/pool_calib_20261016/q1_observed_area.json --chart $D/cutsheet/area_scale_vs_radius.png

Known limitations
  The observed area is a convex hull: it can include thin gaps between observed regions. Rings report medians.
  The central crop search is limited to frame-aspect crops centred on the frame centre or the principal point.
"""
import argparse
import json
import math
import os
import sys

import numpy as np
from matplotlib.path import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pool_calib_models as pm  # noqa: E402

BIN = 0.05            # ring width, in units of the half-diagonal
MIN_PTS = 12          # a ring counts as observed if >= 12 grid points of it lie inside the observed area
DIAM_CM = (5.0, 8.0)  # urchin test diameters (assumption, spec rev 3: ~5-8 cm)
RANGES_M = (0.8, 1.5, 2.0)


def detj(model, K, Dd, p):
    """det of d(normalised undistorted ray)/d(pixel) at pixels p (N,2), central differences of 1 px."""
    e = 0.5
    rx = pm.rays(model, K, Dd, p + [e, 0]) - pm.rays(model, K, Dd, p - [e, 0])
    ry = pm.rays(model, K, Dd, p + [0, e]) - pm.rays(model, K, Dd, p - [0, e])
    return (rx[:, 0] * ry[:, 1] - rx[:, 1] * ry[:, 0]) / (2 * e) ** 2


def load_cam(cam_dir):
    k = [json.load(open(os.path.join(cam_dir, f))) for f in sorted(os.listdir(cam_dir)) if f.startswith("intrinsics_full_")][0]
    cov = json.load(open(os.path.join(cam_dir, "coverage.json")))
    return k, cov


def central_crops(model, K, Dd, G, w, h, hull):
    """Largest frame-aspect crop wholly inside the observed area with |A-1| < 5 % everywhere in it, centred on the
    frame centre and (for reference) on the principal point."""
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    crops = {}
    for cname, (ccx, ccy) in (("frame_centre", ((w - 1) / 2, (h - 1) / 2)), ("principal_point", (cx, cy))):
        best, limit = None, "frame"
        for t in np.arange(0.02, 1.001, 0.01):
            cw, ch = t * w, t * h
            x0, y0 = ccx - cw / 2, ccy - ch / 2
            if x0 < 0 or y0 < 0 or x0 + cw > w or y0 + ch > h:
                limit = "frame"
                break
            q = G[(G[:, 0] >= x0) & (G[:, 0] < x0 + cw) & (G[:, 1] >= y0) & (G[:, 1] < y0 + ch)]
            corners = np.array([[x0, y0], [x0 + cw, y0], [x0, y0 + ch], [x0 + cw, y0 + ch]])
            if not hull.contains_points(corners).all() or not hull.contains_points(q).all():
                limit = "observed area"
                break
            aq = detj(model, K, Dd, q) * fx * fy
            if not np.all(np.isfinite(aq)) or np.max(np.abs(aq - 1)) >= 0.05:
                limit = "5 % area scale"
                break
            best = {"t": round(float(t), 2), "w_px": round(cw), "h_px": round(ch), "frac_of_frame": float(t * t),
                    "xywh": [round(x0), round(y0), round(cw), round(ch)],
                    "density_error_if_skipped_pct": float(np.sum(aq) / len(q) - 1) * 100,
                    "max_area_dev_pct": float(100 * np.max(np.abs(aq - 1)))}
        crops[cname] = {"crop": best, "limited_by": limit}
    return crops


def analyse(cam_dir, step, crop=None):
    k, cov = load_cam(cam_dir)
    K, Dd, model = np.array(k["camera_matrix"]), np.array(k["dist_coeffs"]), k["model"]
    w, h = k["image_size"]
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    xs, ys = np.arange(step / 2, w, step), np.arange(step / 2, h, step)
    G = np.stack(np.meshgrid(xs, ys), -1).reshape(-1, 2)
    hull = Path(np.array(cov["observed_hull_px"]))
    inside = hull.contains_points(G)
    P = G[inside]
    disp = np.linalg.norm(P - pm.pinhole_px(K, pm.rays(model, K, Dd, P)), axis=1)
    rpx = np.hypot(P[:, 0] - cx, P[:, 1] - cy)
    A = detj(model, K, Dd, P) * fx * fy
    Rhd = math.hypot(w / 2, h / 2)
    rn = rpx / Rhd
    ok = np.isfinite(A) & np.isfinite(disp)
    rings, r5, r10 = [], None, None
    for lo in np.arange(0, 1.0, BIN):
        m = ok & (rn >= lo) & (rn < lo + BIN)
        n = int(m.sum())
        if n < MIN_PTS:
            continue
        rr = {"r_lo": round(lo, 2), "r_hi": round(lo + BIN, 2), "r_px_mid": float((lo + BIN / 2) * Rhd), "n": n,
              "disp_px_median": float(np.median(disp[m])),
              "disp_pct_of_radius_median": float(np.median(100 * disp[m] / np.maximum(rpx[m], 1))),
              "area_scale_median": float(np.median(A[m])), "area_scale_min": float(np.min(A[m])),
              "area_scale_max": float(np.max(A[m])), "size_factor_median": float(np.median(1 / np.sqrt(A[m])))}
        dev = abs(rr["area_scale_median"] - 1)
        if r5 is None and dev > 0.05:
            r5 = rr["r_lo"]
        if r10 is None and dev > 0.10:
            r10 = rr["r_lo"]
        rings.append(rr)
    crops = central_crops(model, K, Dd, G, w, h, hull)
    best, limit = crops["frame_centre"]["crop"], crops["frame_centre"]["limited_by"]
    edge = rings[-1] if rings else None
    res = {"model": model, "fx": fx, "fy": fy, "cx": cx, "cy": cy, "observed_frac_of_frame": float(inside.mean()),
           "observed_to_r_half_diag": rings[-1]["r_hi"] if rings else None, "rings": rings,
           "r_area_dev_gt5pct": r5, "r_area_dev_gt10pct": r10,
           "density_error_if_skipped_observed_area_pct": float(np.sum(A[ok]) / ok.sum() - 1) * 100,
           "area_scale_range_observed": [float(np.min(A[ok])), float(np.max(A[ok]))],
           "central_crop_lt5pct": best, "central_crop_limited_by": limit, "central_crops": crops,
           "urchin_px_at_centre": {f"{z} m": {f"{d:.0f} cm": round(fx * d / 100 / z) for d in DIAM_CM} for z in RANGES_M},
           "size_factor_at_observed_edge_median": edge["size_factor_median"] if edge else None,
           "size_factor_range_observed": [float(np.min(1 / np.sqrt(A[ok]))), float(np.max(1 / np.sqrt(A[ok])))],
           "whole_frame": "NOT COMPUTED: needs the model outside the observed area (spec rev 3: stop and say so)"}
    if crop:
        x, y, cw, ch = crop
        q = G[(G[:, 0] >= x) & (G[:, 0] < x + cw) & (G[:, 1] >= y) & (G[:, 1] < y + ch)]
        qi = hull.contains_points(q)
        aq = detj(model, K, Dd, q[qi]) * fx * fy
        res["production_crop"] = {"inside_observed_frac": float(qi.mean()),
                                  "density_error_if_skipped_observed_part_pct": float(np.sum(aq) / len(aq) - 1) * 100,
                                  "max_area_dev_observed_part_pct": float(100 * np.max(np.abs(aq - 1)))}
    return res


def chart(results, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    col = ["#2a78d6", "#eb6834", "#1baf7a"]
    fig, ax = plt.subplots(figsize=(8.2, 4.6), dpi=150)
    for y, txt in ((5, "±5 %"), (10, "±10 %")):
        for sgn in (1, -1):
            ax.axhline(sgn * y, color="#c3c2b7", lw=0.9, ls=(0, (2, 3)))
        ax.text(1.0, y + 0.6, txt, color="#52514e", fontsize=8, ha="right")
    ax.axhline(0, color="#8a8a86", lw=1)
    for i, (cam, res) in enumerate(results.items()):
        xs = [(r["r_lo"] + r["r_hi"]) / 2 for r in res["rings"]]
        ys = [100 * (r["area_scale_median"] - 1) for r in res["rings"]]
        c = col[i % len(col)]
        ax.plot(xs, ys, color=c, lw=2, marker="o", ms=4)
        ax.plot(xs[-1], ys[-1], marker="|", ms=16, mew=2.5, color=c)
        ax.annotate(f"{cam.upper() if len(cam) <= 3 else cam.upper()} (observed to {res['observed_to_r_half_diag']:.2f})",
                    (xs[-1], ys[-1]), xytext=(8, -4 if ys[-1] < 0 else 4), textcoords="offset points",
                    color="#0b0b0b", fontsize=9.5, fontweight="bold", va="center")
    ax.set_xlim(0, 1.18)
    ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_xlabel("distance from the lens centre (frame corner ≈ 1.0)", color="#52514e")
    ax.set_ylabel("seafloor area per pixel vs the centre (%)", color="#52514e")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(colors="#52514e")
    ax.set_title("How much area one pixel covers, across the measured part of each frame", fontsize=11,
                 color="#0b0b0b", loc="left", pad=20)
    ax.text(0.0, 1.01, "Each line stops where Friday's board stopped (bar = end of the measured area); nothing beyond "
            "it is shown", transform=ax.transAxes, fontsize=8.5, color="#52514e", va="bottom")
    fig.tight_layout()
    fig.savefig(path, facecolor="white")


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
    ap.add_argument("--step", action="append", default=[], help="NAME=PX")
    ap.add_argument("--crop", action="append", default=[], help="NAME=x,y,w,h")
    ap.add_argument("--out", required=True)
    ap.add_argument("--chart")
    a = ap.parse_args(argv)
    steps = parse_kv(a.step, int)
    crops = parse_kv(a.crop, lambda v: tuple(int(x) for x in v.split(",")))
    out = {"definitions": "see the script header", "observed_area": "convex hull of the board corners the fit used",
           "bin_width_half_diag": BIN, "min_pts_per_ring": MIN_PTS, "diam_cm": DIAM_CM, "ranges_m": RANGES_M,
           "cameras": {}}
    for it in a.cams:
        name, _, cam_dir = it.partition("=")
        cam_dir = os.path.expanduser(cam_dir)
        w = load_cam(cam_dir)[0]["image_size"][0]
        res = analyse(cam_dir, steps.get(name, 16 if w >= 3000 else 4), crops.get(name))
        out["cameras"][name] = res
        print(f"== {name} ({res['model']}): observed {100 * res['observed_frac_of_frame']:.1f} % of frame to r "
              f"{res['observed_to_r_half_diag']}; area > 5 % from r {res['r_area_dev_gt5pct']}, > 10 % from r "
              f"{res['r_area_dev_gt10pct']}; density if skipped (observed) "
              f"{res['density_error_if_skipped_observed_area_pct']:+.1f} %; central crop {res['central_crop_lt5pct']}")
    with open(os.path.expanduser(a.out), "w") as fh:
        json.dump(out, fh, indent=1)
    if a.chart:
        chart(out["cameras"], os.path.expanduser(a.chart))
    print(f"[q1] wrote {a.out}" + (f" and {a.chart}" if a.chart else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
