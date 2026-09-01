#!/usr/bin/env python3
"""
Score EXTERNALLY corrected images against the reference card.

Purpose:
  Benchmark any color-correction output (research method, manual edit,
  third-party tool) on the same card metrics used by
  tools/bm_reference_card_color_smoke.py — without that method's code ever
  entering this repo. The card is detected in the ORIGINAL image; the same
  quad (scaled if resolutions differ) rectifies the corrected image, so both
  are sampled patch-for-patch.

Inputs:
  Repeated --pair ORIGINAL CORRECTED LABEL triples.

Outputs (default runs/external_score_<YYYYMMDD>/):
  external_scores.csv, external_scores.json, and per pair a compact
  <label>_<stem>_sheet.jpg (original | corrected | rectified cards | swatches).

Example:
  python3 tools/bm_reference_card_score_external.py \
    --pair ~/Downloads/orig.jpg ~/bench/seathru_out.png seathru_mono \
    --output-dir runs/external_score_20260901

Assumptions:
  - The corrected image is geometrically identical to the original (same
    framing; any uniform resize is handled by scaling the card quad).
  - Same card template/corner map assumptions as the smoke tool.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import reference_card_color_utils as ccu  # noqa: E402
from bm_reference_card_color_smoke import (  # noqa: E402
    CANONICAL_H, CANONICAL_W, DEFAULT_TEMPLATE_DIR, load_quality_module,
    _paste_scaled, _swatch_strip)


def metrics_from_samples(samples, img_rgb) -> dict:
    de76 = ccu.patch_delta_e(samples)
    de2000 = ccu.patch_delta_e(samples, metric="de2000")
    de_color = [v for k, v in de76.items()
                if next(s for s in samples if s.patch_id == k).patch_type == "color"]
    return {
        "gray_neutrality": round(ccu.gray_neutrality(samples), 4),
        "gray_angular_deg": round(ccu.gray_angular_error_deg(samples), 2),
        "mean_patch_de76": round(float(np.mean(list(de76.values()))), 2),
        "mean_patch_de2000": round(float(np.mean(list(de2000.values()))), 2),
        "mean_patch_de76_color": round(float(np.mean(de_color)), 2),
        "max_patch_de76": round(float(np.max(list(de76.values()))), 2),
        "mean_luma": round(float(np.asarray(img_rgb).mean()), 1),
        **ccu.clip_stats_srgb255(img_rgb),
    }


def build_sheet(qm, out_path, label, orig_rgb, corr_rgb, rect_orig, rect_corr,
                samples_corr, m_before, m_after):
    f_title, f_txt = qm.pil_font(24, True), qm.pil_font(14)
    W, margin = 1500, 20
    half = (W - 2 * margin - 20) // 2
    sheet = Image.new("RGB", (W, 1200), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    y = margin
    d.text((margin, y), f"External method score — {label}", font=f_title, fill=(30, 50, 70))
    y += 40
    h1 = _paste_scaled(sheet, Image.fromarray(orig_rgb), margin, y, half)
    h2 = _paste_scaled(sheet, Image.fromarray(corr_rgb), margin + half + 20, y, half)
    y += max(h1, h2) + 10
    h1 = _paste_scaled(sheet, Image.fromarray(rect_orig), margin, y, half)
    h2 = _paste_scaled(sheet, Image.fromarray(rect_corr), margin + half + 20, y, half)
    y += max(h1, h2) + 10
    d.text((margin, y), "Patches — top: design target, bottom: corrected output",
           font=f_txt, fill=(95, 110, 130))
    y += 20
    y += _swatch_strip(d, margin, y, samples_corr, model=None, sw=40, sh=26) + 12
    for i, (k, before) in enumerate(m_before.items()):
        d.text((margin, y + i * 20),
               f"{k}: {before} -> {m_after.get(k)}", font=f_txt, fill=(30, 50, 70))
    y += len(m_before) * 20
    sheet.crop((0, 0, W, y + margin)).save(out_path, quality=92)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pair", nargs=3, action="append", required=True,
                    metavar=("ORIGINAL", "CORRECTED", "LABEL"))
    ap.add_argument("--output-dir", default="")
    ap.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    ap.add_argument("--tag-family", default="DICT_APRILTAG_36h11")
    ap.add_argument("--scales", nargs="+", type=float, default=[1, 2, 3, 4, 6, 8])
    ap.add_argument("--corner-map", default="tl:0,tr:1,bl:2,br:3")
    ap.add_argument("--card-expand-x", type=float, default=1.25)
    ap.add_argument("--card-expand-y", type=float, default=2.0)
    ap.add_argument("--patch-inset", type=float, default=0.30)
    args = ap.parse_args()

    out_dir = (Path(args.output_dir).expanduser().resolve() if args.output_dir else
               TOOLS_DIR.parent / "runs" /
               f"external_score_{datetime.now(timezone.utc).strftime('%Y%m%d')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    layout = ccu.load_template(Path(args.template_dir) / "template_layout.json")
    qm = load_quality_module()
    corner_map = qm.parse_corner_map(args.corner_map)

    rows = []
    for orig_path, corr_path, label in args.pair:
        orig_path, corr_path = Path(orig_path).expanduser(), Path(corr_path).expanduser()
        print(f"\n=== {label}: {corr_path.name} (vs {orig_path.name}) ===")
        orig_bgr = qm.load_image_bgr(orig_path)
        corr_bgr = qm.load_image_bgr(corr_path)

        _, corners_by_id, _, _ = qm.detect_tags(orig_bgr, args.tag_family, args.scales)
        fid_quad, _, _ = qm.infer_card_corners_from_tags(corners_by_id, corner_map)
        if fid_quad is None:
            print("  FAIL: card not detected in original; skipping")
            rows.append({"label": label, "original": orig_path.name,
                         "corrected": corr_path.name, "card_detected": False})
            continue
        card_quad = qm.expand_quad(fid_quad, args.card_expand_x, args.card_expand_y)

        # Corrected image may be a uniform resize of the original: scale the quad.
        sx = corr_bgr.shape[1] / orig_bgr.shape[1]
        sy = corr_bgr.shape[0] / orig_bgr.shape[0]
        corr_quad = card_quad * np.array([sx, sy], dtype=np.float32)

        rect_orig = cv2.cvtColor(qm.rectify_quad(orig_bgr, card_quad, CANONICAL_W, CANONICAL_H),
                                 cv2.COLOR_BGR2RGB)
        rect_corr = cv2.cvtColor(qm.rectify_quad(corr_bgr, corr_quad, CANONICAL_W, CANONICAL_H),
                                 cv2.COLOR_BGR2RGB)
        samples_orig = ccu.sample_patches(rect_orig, layout, args.patch_inset)
        samples_corr = ccu.sample_patches(rect_corr, layout, args.patch_inset)
        m_before = metrics_from_samples(samples_orig, cv2.cvtColor(orig_bgr, cv2.COLOR_BGR2RGB))
        m_after = metrics_from_samples(samples_corr, cv2.cvtColor(corr_bgr, cv2.COLOR_BGR2RGB))

        print(f"  dE76 {m_before['mean_patch_de76']} -> {m_after['mean_patch_de76']}   "
              f"dE2000 {m_before['mean_patch_de2000']} -> {m_after['mean_patch_de2000']}   "
              f"psi {m_before['gray_angular_deg']} -> {m_after['gray_angular_deg']}deg")

        sheet = out_dir / f"{label}_{orig_path.stem}_sheet.jpg"
        build_sheet(qm, sheet, f"{label} — {orig_path.stem}",
                    cv2.cvtColor(orig_bgr, cv2.COLOR_BGR2RGB),
                    cv2.cvtColor(corr_bgr, cv2.COLOR_BGR2RGB),
                    rect_orig, rect_corr, samples_corr, m_before, m_after)
        rows.append({"label": label, "original": orig_path.name,
                     "corrected": corr_path.name, "card_detected": True,
                     **{k + "_before": v for k, v in m_before.items()},
                     **{k + "_after": v for k, v in m_after.items()},
                     "sheet": str(sheet)})

    (out_dir / "external_scores.json").write_text(json.dumps(rows, indent=2),
                                                  encoding="utf-8")
    if rows:
        fields = sorted({k for r in rows for k in r}, key=lambda k: (k != "label", k))
        with (out_dir / "external_scores.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    print(f"\nscores={out_dir / 'external_scores.csv'}")


if __name__ == "__main__":
    main()
