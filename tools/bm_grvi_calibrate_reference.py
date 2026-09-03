#!/usr/bin/env python3
"""
Build GRVI reference-render calibration targets from a reference photo.

Purpose:
  Read the Reef Reference Card V2 out of a photo taken by a reference camera
  (e.g. the Olympus P9 surface shots) and convert its card rendition into
  stage-3 targets for tools/bm_grvi_correct.py --render-targets. GRVI then
  makes the bmcam output inherit that camera's rendition (its tone of coral,
  water and sand) instead of the card's flat design colors.

How the targets are computed (all in linear RGB):
  1. Sample the 17 card patches from the reference photo.
  2. Subtract the reference photo's own veil, estimated from its black patch
     (design reflectance ~0, so whatever the camera recorded there is haze),
     using the same softplus subtraction as GRVI stage 1 (no negatives).
  3. Normalize exposure: scale so the white patch luminance = 1.0. The color
     cast of the reference camera is deliberately kept (that IS the look).

Inputs:  --image  reference photo with the card in frame (any resolution)
Outputs: --out    JSON with per-patch linear-RGB targets, the water color
                  sampled from the frame's top corners, and provenance.

Example:
  python3 tools/bm_grvi_calibrate_reference.py \
      --image reference_images/reference_reef_coral_card_01.jpg \
      --out tools/reference_card_color_correction/render_targets_p9.json

Known limitations:
  - Tag detection runs at native scale only; the card must be reasonably
    large in the frame (P9-style composition).
  - Water color comes from the top-left/top-right corner medians; check the
    JSON if the reference frame has no open water in its top corners.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference_card_color_utils as ccu  # noqa: E402
import bm_reference_card_quality_v2 as qm  # noqa: E402

CANONICAL_W, CANONICAL_H = 3000, 1000
TEMPLATE_DIR = (Path(__file__).resolve().parent
                / "reference_card_color_correction" / "reference_card_template_v2")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--template-dir", default=str(TEMPLATE_DIR))
    ap.add_argument("--soft-k", type=float, default=0.35,
                    help="softplus knee for the reference-veil subtraction")
    args = ap.parse_args()

    img_path = Path(args.image).expanduser().resolve()
    img_rgb = np.array(Image.open(img_path).convert("RGB"))
    print(f"reference: {img_path.name} {img_rgb.shape[1]}x{img_rgb.shape[0]}")

    img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    corner_map = qm.parse_corner_map("tl:0,tr:1,bl:2,br:3")
    tag_metrics, corners_by_id, _bs, _rej = qm.detect_tags(
        img_bgr, "DICT_APRILTAG_36h11", [1])
    fid_quad, _st, _res = qm.infer_card_corners_from_tags(corners_by_id, corner_map)
    if fid_quad is None:
        print(f"FAIL: card not detected ({len(tag_metrics)} tags)")
        sys.exit(1)
    print(f"tags: {len(tag_metrics)} ids: {sorted(corners_by_id)}")
    quad = qm.expand_quad(fid_quad, 1.25, 2.0)
    rect = cv2.cvtColor(qm.rectify_quad(img_bgr, quad, CANONICAL_W, CANONICAL_H),
                        cv2.COLOR_BGR2RGB)
    layout = ccu.load_template(Path(args.template_dir) / "template_layout.json")
    samples = ccu.sample_patches(rect, layout)

    # reference veil = black patch rendition; softplus-subtract from all patches
    obs_lin = ccu.srgb_to_linear(
        np.array([s.median_srgb for s in samples]) / 255.0)
    black = next(i for i, s in enumerate(samples) if s.patch_id == "gray_black")
    white = next(i for i, s in enumerate(samples) if s.patch_id == "gray_white")
    veil = obs_lin[black]
    d = obs_lin - veil
    eps = args.soft_k * veil + 1e-9
    dehazed = 0.5 * (d + np.sqrt(d * d + eps * eps))
    y_white = float(ccu.rel_luminance_linear(dehazed[white][None, :])[0])
    targets = dehazed / max(y_white, 1e-6)

    h, w = img_rgb.shape[:2]
    water_tl = np.median(img_rgb[0:h // 6, 0:w // 5].reshape(-1, 3), axis=0)
    water_tr = np.median(img_rgb[0:h // 6, -w // 5:].reshape(-1, 3), axis=0)
    water = (water_tl + water_tr) / 2.0

    # tone rendition of the reference, veil INCLUDED: raw gray-patch luminances
    # normalized by the raw white. The reference's hazy shadow lift is part of
    # its look — the GRVI tone curve maps our corrected ramp onto these.
    y_raw = ccu.rel_luminance_linear(obs_lin)
    tone = {s.patch_id: round(float(y_raw[i] / max(y_raw[white], 1e-6)), 5)
            for i, s in enumerate(samples) if s.patch_type == "gray"}

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_image": img_path.name,
        "soft_k": args.soft_k,
        "reference_veil_linear": [round(float(v), 6) for v in veil],
        "white_luma_after_dehaze": round(y_white, 4),
        "targets_linear": {s.patch_id: [round(float(v), 6) for v in targets[i]]
                           for i, s in enumerate(samples)},
        "tone_luma_targets": tone,
        "raw_patch_srgb": {s.patch_id: [round(float(v), 2) for v in s.median_srgb]
                           for s in samples},
        "water_srgb": [round(float(v), 1) for v in water],
    }
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"water color (top corners): {[round(float(v)) for v in water]}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
