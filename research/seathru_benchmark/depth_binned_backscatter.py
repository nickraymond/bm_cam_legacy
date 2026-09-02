#!/usr/bin/env python3
"""
Depth-binned backscatter estimation — RESEARCH ONLY (patent-flagged technique).

This implements Sea-thru's backscatter estimator: bin pixels into N distance
bins, take the darkest fraction in each bin as pure-backscatter candidates,
and fit B_inf_c * (1 - exp(-beta_B_c * z)) per channel across the bins. It is
the specific technique the research brief flags for patent caution
(docs/underwater_color_correction_research_202609.md §4b) — hence it lives in
research/, is NOT part of the commercial path, and exists only to measure how
much accuracy the card-anchored single-point estimate in
tools/bm_reference_card_hybrid_physics.py gives up.

Everything downstream (LSAC, exposure anchor, finish) is reused from the
commercial hybrid tool so the ONLY variable is the backscatter model.

Run with the seathru bench venv (torch/transformers/scipy/skimage):
  <bench venv python> research/seathru_benchmark/depth_binned_backscatter.py \
    --images <img.jpg> ... --out-dir runs/<dir> [--bins 10] [--dark-frac 0.01]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.optimize import curve_fit

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import reference_card_color_utils as ccu  # noqa: E402
from bm_reference_card_hybrid_physics import (  # noqa: E402
    CANONICAL_H, CANONICAL_W, DEFAULT_TEMPLATE_DIR, anchor_depth, finish,
    get_depth_pipe, load_quality_module, lsac_recover, relative_depth,
    solve_physics)


def binned_backscatter(img_lin: np.ndarray, z: np.ndarray, bins: int,
                       dark_frac: float) -> dict:
    """Sea-thru-style fit: darkest pixels per depth bin -> B_inf, beta_B."""
    flat = img_lin.reshape(-1, 3)
    zf = z.reshape(-1)
    edges = np.quantile(zf, np.linspace(0, 1, bins + 1))
    pts_z, pts_rgb = [], []
    for i in range(bins):
        sel = (zf >= edges[i]) & (zf <= edges[i + 1])
        if sel.sum() < 50:
            continue
        lum = flat[sel].sum(axis=1)
        k = max(int(dark_frac * sel.sum()), 10)
        dark = np.argpartition(lum, k)[:k]
        pts_z.append(np.full(k, float(zf[sel][dark].mean())))
        pts_rgb.append(flat[sel][dark])
    pts_z = np.concatenate(pts_z)
    pts_rgb = np.concatenate(pts_rgb)

    def model(zv, B_inf, beta):
        return B_inf * (1.0 - np.exp(-beta * zv))

    B_inf, beta_B, per_bin = np.zeros(3), np.zeros(3), []
    for c in range(3):
        try:
            (B_inf[c], beta_B[c]), _ = curve_fit(
                model, pts_z, pts_rgb[:, c], p0=[max(pts_rgb[:, c].max(), 1e-3), 1.0],
                bounds=([0, 0.01], [1.0, 8.0]), maxfev=5000)
        except RuntimeError:
            B_inf[c], beta_B[c] = pts_rgb[:, c].max(), 1.0
    for i in range(bins):
        sel = (pts_z >= edges[i]) & (pts_z <= edges[i + 1])
        if sel.any():
            per_bin.append({"z_mean": round(float(pts_z[sel].mean()), 3),
                            "dark_rgb": np.round(pts_rgb[sel].mean(axis=0), 4).tolist()})
    return {"B_inf": B_inf, "beta_B": beta_B, "bins": per_bin,
            "n_points": int(len(pts_z))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--dark-frac", type=float, default=0.01)
    ap.add_argument("--z-card", type=float, default=1.5)
    ap.add_argument("--near-ratio", type=float, default=0.45)
    ap.add_argument("--lsac-sigma-frac", type=float, default=0.12)
    ap.add_argument("--finish", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    layout = ccu.load_template(DEFAULT_TEMPLATE_DIR / "template_layout.json")
    qm = load_quality_module()
    corner_map = qm.parse_corner_map("tl:0,tr:1,bl:2,br:3")
    depth_pipe = get_depth_pipe()

    for img_path in [Path(p).expanduser() for p in args.images]:
        print(f"\n=== {img_path.name} ===")
        img_bgr = qm.load_image_bgr(img_path)
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        _, corners_by_id, _, _ = qm.detect_tags(img_bgr, "DICT_APRILTAG_36h11",
                                                [1, 2, 3, 4, 6, 8])
        fid_quad, _, _ = qm.infer_card_corners_from_tags(corners_by_id, corner_map)
        card_quad = qm.expand_quad(fid_quad, 1.25, 2.0)
        rect_rgb = cv2.cvtColor(qm.rectify_quad(img_bgr, card_quad,
                                                CANONICAL_W, CANONICAL_H),
                                cv2.COLOR_BGR2RGB)
        samples = ccu.sample_patches(rect_rgb, layout, 0.30)

        disp_n = relative_depth(depth_pipe, Image.fromarray(img_rgb))
        z, zinfo = anchor_depth(disp_n, card_quad, args.z_card, args.near_ratio)
        img_lin = ccu.srgb_to_linear(img_rgb.astype(np.float64) / 255.0)

        # Card-anchored estimate for the same image, for the comparison table.
        card_phys = solve_physics(img_lin, z, samples, args.z_card, 0.98)
        binned = binned_backscatter(img_lin, z, args.bins, args.dark_frac)
        print(f"  card-anchored: B_inf={np.round(card_phys['B_inf'], 4).tolist()} "
              f"beta_B={np.round(card_phys['beta_B'], 3).tolist()}")
        print(f"  depth-binned : B_inf={np.round(binned['B_inf'], 4).tolist()} "
              f"beta_B={np.round(binned['beta_B'], 3).tolist()} "
              f"({binned['n_points']} dark pts, {args.bins} bins)")

        phys = {"B_inf": binned["B_inf"], "beta_B": binned["beta_B"],
                "beta_D": card_phys["beta_D"], "red_boost_cap": None}
        recovered_lin = lsac_recover(img_lin, z, phys, args.lsac_sigma_frac)
        # Same card-anchored exposure as the hybrid tool.
        rect_lin = qm.rectify_quad(recovered_lin, card_quad, CANONICAL_W, CANONICAL_H)
        p_mid = next(p for p in layout["patches"] if p["id"] == "gray_mid")
        sx, sy = CANONICAL_W / layout["template_width_px"], CANONICAL_H / layout["template_height_px"]
        box = rect_lin[int((p_mid["y"] + 0.3 * p_mid["h"]) * sy):
                       int((p_mid["y"] + 0.7 * p_mid["h"]) * sy),
                       int((p_mid["x"] + 0.3 * p_mid["w"]) * sx):
                       int((p_mid["x"] + 0.7 * p_mid["w"]) * sx)]
        lum = float(ccu.rel_luminance_linear(np.median(box.reshape(-1, 3), axis=0)))
        target_lum = float(ccu.rel_luminance_linear(
            ccu.srgb_to_linear(np.asarray(p_mid["target_srgb"]) / 255.0)))
        recovered_lin = np.clip(recovered_lin * target_lum / max(lum, 1e-6), 0.0, 1.0)

        finish_info = None
        if args.finish:
            recovered_lin, finish_info = finish(recovered_lin, card_quad, qm, layout, 0.30)

        out = np.clip(np.rint(ccu.linear_to_srgb(recovered_lin) * 255.0),
                      0, 255).astype(np.uint8)
        out_png = out_dir / f"{img_path.stem}_binnedbs.png"
        Image.fromarray(out).save(out_png)
        (out_dir / f"{img_path.stem}_binnedbs_params.json").write_text(json.dumps({
            "research_only": True, "technique": "sea-thru depth-binned dark-pixel backscatter",
            "depth_anchor": zinfo,
            "card_anchored": {"B_inf": card_phys["B_inf"].tolist(),
                              "beta_B": card_phys["beta_B"].tolist()},
            "depth_binned": {"B_inf": binned["B_inf"].tolist(),
                             "beta_B": binned["beta_B"].tolist(),
                             "bins": binned["bins"]},
            "finish": finish_info, "args": vars(args),
        }, indent=2, default=str), encoding="utf-8")
        print(f"  saved {out_png}")


if __name__ == "__main__":
    main()
