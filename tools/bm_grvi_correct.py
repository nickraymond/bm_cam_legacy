#!/usr/bin/env python3
"""
GRVI production color correction for card-in-frame underwater stills.

Purpose:
  One-shot automated correction of a bmcam still (green cast, dead red
  channel) into a clean surface-camera-style rendition, anchored entirely on
  the Nereus Reef Reference Card V2 in the frame. Implements the GRVI method
  (Gray-Ramp Veil Inversion, see tools/reference_card_color_utils.py) plus
  the spatial water-body rendering prior that keeps open water looking like
  water instead of subtracted gray mud.

Inputs:
  - one or more JPEG/PNG stills with the reference card visible
  - the V2 template layout (tools/reference_card_color_correction/...)

Outputs (per image, in <output-dir>/<stem>/):
  before.jpg, after_grvi.jpg, correction_grvi.json, metrics.json,
  cutsheet.jpg (before | after side-by-side)
  Plus <output-dir>/run_manifest.json and summary.csv.

Example:
  python3 tools/bm_grvi_correct.py \
      --images ~/Downloads/SPOT-*_BMCAM_001_*.jpg \
      --output-dir runs/grvi_$(date +%Y%m%d)

Assumptions / known limitations:
  - Card must be detected via its 4 AprilTags (same detector as the smoke
    tool); no card -> no correction (exit nonzero if ALL images fail).
  - The water prior re-renders low-texture veil-colored regions toward a
    target water color; it is a display prior, not colorimetry. Disable with
    --water-strength 0 for quantitative work.
  - Tuned on AOML reef-flat imagery (2026-09); other sites may want
    --water-rgb adjusted.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference_card_color_utils as ccu  # noqa: E402
import bm_reference_card_quality_v2 as qm  # noqa: E402

CANONICAL_W, CANONICAL_H = 3000, 1000
TEMPLATE_DIR = (Path(__file__).resolve().parent
                / "reference_card_color_correction" / "reference_card_template_v2")


def detect_and_sample(img_rgb: np.ndarray, layout: dict):
    """AprilTag card detection -> rectified card -> patch samples (or None)."""
    img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    corner_map = qm.parse_corner_map("tl:0,tr:1,bl:2,br:3")
    tag_metrics, corners_by_id, best_scale, _rej = qm.detect_tags(
        img_bgr, "DICT_APRILTAG_36h11", [1, 2, 3, 4, 6, 8])
    fid_quad, _status, _resid = qm.infer_card_corners_from_tags(corners_by_id, corner_map)
    if fid_quad is None:
        return None, len(tag_metrics)
    quad = qm.expand_quad(fid_quad, 1.25, 2.0)
    rect_bgr = qm.rectify_quad(img_bgr, quad, CANONICAL_W, CANONICAL_H)
    rect_rgb = cv2.cvtColor(rect_bgr, cv2.COLOR_BGR2RGB)
    return ccu.sample_patches(rect_rgb, layout), len(tag_metrics)


def water_mask(lin: np.ndarray, veil: np.ndarray) -> np.ndarray:
    """Waterness in [0,1]: veil-like chroma AND bright AND low texture.

    The texture gate is what keeps bright (but textured) sand out of the mask.
    Returned shape (H, W, 1), smoothed so the blend has no seams.
    """
    y = ccu.rel_luminance_linear(lin).astype(np.float32)
    chroma = lin / (y[..., None] + 1e-9)
    vd = (veil / (ccu.rel_luminance_linear(veil[None, :])[0] + 1e-9)).astype(np.float32)
    d = np.linalg.norm(chroma - vd, axis=-1).astype(np.float32)
    chroma_sim = np.exp(-(d / 0.20) ** 2)
    tex = np.sqrt(np.clip(cv2.GaussianBlur(y * y, (0, 0), 6)
                          - cv2.GaussianBlur(y, (0, 0), 6) ** 2, 0, None))
    smooth = np.exp(-(tex / (np.quantile(tex, 0.9) + 1e-9) / 0.25) ** 2)
    bright = np.clip(cv2.GaussianBlur(y, (0, 0), 3) / (np.quantile(y, 0.8) + 1e-9), 0, 1) ** 2
    m = cv2.GaussianBlur(chroma_sim * smooth * bright, (0, 0), 12)
    return np.clip(m, 0, 1)[..., None].astype(np.float64)


def attach_reference_tone_curve(model: "ccu.GRVIModel", samples,
                                tone_targets: dict) -> None:
    """Fit a monotone (PCHIP) luminance curve: our corrected gray ramp -> the
    reference camera's gray-ramp rendition (veil included — its hazy shadow
    lift is part of the look). Stored on the model as a 257-point LUT.
    """
    from scipy.interpolate import PchipInterpolator
    xs, ys = [0.0], [0.0]
    white = next(s for s in samples if s.patch_id == "gray_white")
    pw = model.apply_core(ccu.srgb_to_linear(white.median_srgb[None, :] / 255.0))
    y_white = float(ccu.rel_luminance_linear(np.clip(pw, 0, None))[0])
    for s in samples:
        if s.patch_id not in tone_targets:
            continue
        p = model.apply_core(ccu.srgb_to_linear(s.median_srgb[None, :] / 255.0))
        xs.append(float(ccu.rel_luminance_linear(np.clip(p, 0, None))[0]))
        ys.append(float(tone_targets[s.patch_id]) * y_white)
    xs.append(1.0)
    ys.append(max(1.0, max(ys)))
    xs, ys = np.asarray(xs), np.asarray(ys)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    xs, uniq = np.unique(xs, return_index=True)
    ys = np.maximum.accumulate(np.clip(ys[uniq], 0, None))
    curve = PchipInterpolator(xs, ys, extrapolate=True)
    model.tone_lut_x = np.linspace(0.0, 1.0, 257)
    model.tone_lut_y = np.clip(curve(model.tone_lut_x), 0.0, 1.0)
    model.notes.append("tone: reference gray-ramp PCHIP curve "
                       f"({len(xs)} knots, replaces gamma)")


def warm_grade(srgb01: np.ndarray, shift_deg: float, dark_frac: float,
               sat_scale: float) -> np.ndarray:
    """Warm-band HSV grade, identical math to the Coral Tone Bench picker.

    Operates on final sRGB (0..1) — the same domain the picker's canvas
    used, so a pick made in the tool reproduces exactly. Weight: hue window
    16-102 deg (full 38-80), gated by saturation/0.12. Values come from the
    calibration profile's "warm_grade" block (Nick's saved pick).
    """
    if shift_deg == 0 and dark_frac == 0 and sat_scale == 1:
        return srgb01
    r, g, b = srgb01[..., 0], srgb01[..., 1], srgb01[..., 2]
    mx = np.max(srgb01, axis=-1)
    mn = np.min(srgb01, axis=-1)
    df = mx - mn
    h = np.zeros_like(mx)
    m = df > 0
    rm = m & (mx == r); h[rm] = np.mod((g[rm] - b[rm]) / df[rm], 6.0)
    gm = m & (mx == g) & ~rm; h[gm] = (b[gm] - r[gm]) / df[gm] + 2.0
    bm = m & ~rm & ~gm; h[bm] = (r[bm] - g[bm]) / df[bm] + 4.0
    h *= 60.0
    s = np.where(mx > 0, df / np.maximum(mx, 1e-9), 0.0)
    w = np.zeros_like(h)
    band = (h > 16) & (h < 102)
    w[band] = np.where(h[band] < 38, (h[band] - 16) / 22,
                       np.where(h[band] > 80, (102 - h[band]) / 22, 1.0))
    w *= np.minimum(1.0, s / 0.12)
    h2 = h - shift_deg * w
    s2 = np.minimum(1.0, s * (1 + (sat_scale - 1) * w))
    v2 = mx * (1 - dark_frac * w)
    c = v2 * s2
    x = c * (1 - np.abs(np.mod(h2 / 60.0, 2.0) - 1))
    mm = v2 - c
    out = np.empty_like(srgb01)
    seg0 = h2 < 60
    seg1 = (h2 >= 60) & (h2 < 120)
    seg2 = h2 >= 120
    out[..., 0] = np.select([seg0, seg1, seg2], [c, x, np.zeros_like(c)]) + mm
    out[..., 1] = np.select([seg0, seg1, seg2], [x, c, c]) + mm
    out[..., 2] = np.select([seg0, seg1, seg2],
                            [np.zeros_like(c), np.zeros_like(c), x]) + mm
    return np.where(w[..., None] > 0, out, srgb01)


def coral_tan(x: np.ndarray, rot: float, lift: float) -> np.ndarray:
    """Rendition stage: rotate warm (orange-red) hues toward tan-yellow.

    Matches the surface-camera reference rendition (P9/Olympus): reef coral
    reads brown/yellow, not orange-red. Warmness weight comes from red
    dominance over blue; luminance is preserved by renormalization, then warm
    areas get a small brightness lift. Scene-wide (card warm patches shift
    slightly too) — this is the visualization layer, correction_grvi.json
    stays the measurement layer.
    """
    if rot <= 0 and lift <= 0:
        return x
    y = np.clip(ccu.rel_luminance_linear(np.clip(x, 0, None)), 1e-9, None)[..., None]
    r, g = x[..., 0], x[..., 1]
    warm = (np.clip((r - x[..., 2]) / (y[..., 0] + 1e-9), 0, 2.5) / 2.5) ** 1.5
    shift = rot * warm * np.clip(r - g, 0, None)
    x2 = x.copy()
    x2[..., 0] = r - shift
    x2[..., 1] = g + 0.3 * shift
    y2 = np.clip(ccu.rel_luminance_linear(np.clip(x2, 0, None)), 1e-9, None)[..., None]
    return x2 * (y / y2) * (1 + lift * warm[..., None])


def correct_image(img_rgb: np.ndarray, model: "ccu.GRVIModel",
                  water_strength: float, water_rgb, water_bright: float,
                  tan_rot: float = 0.4, tan_lift: float = 0.2,
                  grade: dict | None = None) -> np.ndarray:
    """Full-frame GRVI: core -> water prior -> coral tan -> render -> warm
    grade. Returns uint8 RGB."""
    lin = ccu.srgb_to_linear(img_rgb.astype(np.float64) / 255.0)
    x = model.apply_core(lin)
    if water_strength > 0:
        m = water_mask(lin, model.veil) * water_strength
        y = np.clip(ccu.rel_luminance_linear(np.clip(x, 0, None)), 0, None)[..., None]
        wt = ccu.srgb_to_linear(np.asarray(water_rgb, dtype=np.float64) / 255.0)
        wt = wt / ccu.rel_luminance_linear(wt[None, :])[0]
        x = x * (1 - m) + (y * wt * water_bright) * m
    x = coral_tan(x, tan_rot, tan_lift)
    out = ccu.linear_to_srgb(model.apply_render(x))
    if grade:
        out = warm_grade(out, grade.get("shift_deg", 0.0),
                         grade.get("dark_frac", 0.0),
                         grade.get("sat_scale", 1.0))
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


def build_cutsheet(out_path: Path, stem: str, run_tag: str,
                   before: np.ndarray, after: np.ndarray, info: str) -> None:
    """Simple review sheet: before | after with a header strip."""
    h, w = before.shape[:2]
    header = 90
    sheet = np.full((h + header, w * 2 + 30, 3), 245, dtype=np.uint8)
    sheet[header:header + h, 10:10 + w] = before
    sheet[header:header + h, w + 20:w + 20 + w] = after
    img = Image.fromarray(sheet)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
        small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except OSError:
        font = small = ImageFont.load_default()
    d.text((12, 8), f"GRVI correction — {stem}", font=font, fill=(20, 20, 20))
    d.text((12, 46), f"{run_tag}   left: as received   right: grvi   {info}",
           font=small, fill=(80, 80, 80))
    img.save(out_path, quality=92)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--template-dir", default=str(TEMPLATE_DIR))
    ap.add_argument("--water-strength", type=float, default=0.75,
                    help="water-body rendering prior blend 0..1 (0 disables)")
    ap.add_argument("--water-rgb", default="60,125,155",
                    help="target water color, sRGB R,G,B")
    ap.add_argument("--water-bright", type=float, default=1.3)
    ap.add_argument("--coral-tan", type=float, default=0.4,
                    help="warm-hue rotation toward tan-yellow 0..1 (0 disables)")
    ap.add_argument("--coral-lift", type=float, default=0.2,
                    help="brightness lift on warm areas 0..0.5")
    ap.add_argument("--render-targets", default="",
                    help="reference-render calibration JSON from "
                         "bm_grvi_calibrate_reference.py; stage-3 fits toward "
                         "that camera's card rendition, and its water color is "
                         "used unless --water-rgb is given explicitly")
    args = ap.parse_args()

    target_override = None
    if args.render_targets:
        cal = json.loads(Path(args.render_targets).expanduser().read_text())
        target_override = {pid: np.asarray(v, dtype=np.float64)
                           for pid, v in cal["targets_linear"].items()}
        if "--water-rgb" not in " ".join(sys.argv) and cal.get("water_srgb"):
            args.water_rgb = ",".join(str(v) for v in cal["water_srgb"])
        if "--coral-tan" not in " ".join(sys.argv):
            # calibrated matrix + tone curve carry the rendition; the tan
            # rotation on top pushes warm hues past P9 into yellow
            # (measured: P9 warm pixels a*/b* 6/14, tan 0.2 gave 6/18)
            args.coral_tan = 0.0
            args.coral_lift = 0.0
        print(f"render calibration: {cal['source_image']} "
              f"({len(target_override)} patch targets, water {args.water_rgb})")

    out_root = Path(args.output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    run_tag = f"grvi_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    layout = ccu.load_template(Path(args.template_dir) / "template_layout.json")
    water_rgb = [float(v) for v in args.water_rgb.split(",")]

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True,
                                cwd=Path(__file__).parent).stdout.strip()
    except OSError:
        commit = ""

    rows, ok = [], 0
    for path_str in args.images:
        img_path = Path(path_str).expanduser().resolve()
        stem = img_path.stem
        img_dir = out_root / stem
        img_dir.mkdir(parents=True, exist_ok=True)
        print(f"=== {img_path.name} ===")
        img_rgb = np.array(Image.open(img_path).convert("RGB"))
        Image.fromarray(img_rgb).save(img_dir / "before.jpg", quality=95)
        row = {"image": img_path.name, "card_detected": False}

        samples, n_tags = detect_and_sample(img_rgb, layout)
        row["tag_count"] = n_tags
        if samples is None:
            print(f"  FAIL: card not detected ({n_tags} tags); skipping")
            rows.append(row)
            continue
        row["card_detected"] = True
        img_lin = ccu.srgb_to_linear(img_rgb.astype(np.float64) / 255.0)
        model = ccu.solve_grvi(samples, img_lin, target_override=target_override)
        if args.render_targets and cal.get("tone_luma_targets"):
            attach_reference_tone_curve(model, samples, cal["tone_luma_targets"])
            model.vib = 0.2   # P9 warm chroma match (b* 15 vs target 14)
        clar = cal.get("clarity") if args.render_targets else None
        if clar:
            # Clarity Bench pick: partial veil subtraction keeps some natural
            # water; shadow_blend pulls the P9 tone curve's hazy dark floor
            # back toward true blacks (weight exp(-Y/0.12), shadows only).
            # Applied post-solve, matching the picker tool's math exactly.
            model.veil = model.veil * clar.get("veil_scale", 1.0)
            sh = clar.get("shadow_blend", 1.0)
            if model.tone_lut_x is not None and sh != 1.0:
                lx, ly = model.tone_lut_x, model.tone_lut_y
                model.tone_lut_y = ly + (lx - ly) * (1.0 - sh) * np.exp(-lx / 0.12)
            model.notes.append(f"clarity pick: veil_scale={clar.get('veil_scale')} "
                               f"shadow_blend={sh}")
        after = correct_image(img_rgb, model, args.water_strength,
                              water_rgb, args.water_bright,
                              args.coral_tan, args.coral_lift,
                              grade=(cal.get("warm_grade")
                                     if args.render_targets else None))
        Image.fromarray(after).save(img_dir / "after_grvi.jpg", quality=95)
        (img_dir / "correction_grvi.json").write_text(
            json.dumps(model.to_dict(), indent=2), encoding="utf-8")

        red = ccu.card_red_health(samples)
        de = ccu.patch_delta_e(samples, None)
        row.update({
            "mean_patch_de_before": round(float(np.mean(list(de.values()))), 2),
            "gray_angular_deg_before": round(ccu.gray_angular_error_deg(samples), 2),
            **red,
            "veil": [round(float(v), 4) for v in model.veil],
            "gain": [round(float(1.0 / a), 4) for a in model.amp],
            "mean_luma_after": round(float(after.mean()), 1),
        })
        (img_dir / "metrics.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        info = (f"red {red['white_patch_red_frac']:.1%} of scale   "
                f"veil RGB {row['veil']}")
        build_cutsheet(img_dir / "cutsheet.jpg", stem, run_tag, img_rgb, after, info)
        print(f"  veil={row['veil']} gain={row['gain']} "
              f"red_frac={red['white_patch_red_frac']:.1%}")
        print(f"  wrote {img_dir / 'after_grvi.jpg'}")
        ok += 1
        rows.append(row)

    manifest = {
        "run_tag": run_tag, "git_commit": commit,
        "command": " ".join(sys.argv),
        "args": vars(args), "images_ok": ok, "images_total": len(args.images),
    }
    (out_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2),
                                                encoding="utf-8")
    if rows:
        keys = sorted({k for r in rows for k in r})
        with (out_root / "summary.csv").open("w", newline="") as fh:
            wcsv = csv.DictWriter(fh, fieldnames=keys)
            wcsv.writeheader()
            wcsv.writerows({k: (json.dumps(v) if isinstance(v, list) else v)
                            for k, v in r.items()} for r in rows)
    print(f"done: {ok}/{len(args.images)} corrected -> {out_root}")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
