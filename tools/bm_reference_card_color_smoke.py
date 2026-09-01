#!/usr/bin/env python3
"""
BM Reference Card Color-Correction Smoke Test (Sprint05 revival, TODO-COLOR-001)

Purpose:
  Take deployed BM camera images with the Reef Reference Card V2 in frame,
  detect the card via AprilTags (reusing tools/bm_reference_card_quality_v2.py),
  rectify it to the canonical 3000x1000 frame, sample the color patches, and
  test multiple color-correction methods side by side. Main deliverable is a
  per-image cut sheet plus CSV/JSON metrics.

Inputs:
  - One or more JPG/PNG images (--images explicit paths, or --input-dir + glob).
  - Card template: tools/reference_card_color_correction/reference_card_template_v2/
    (template_layout.json + reference_card_template_3000x1000.png).

Outputs (per run, default runs/sprint05_color_smoke_<YYYYMMDD>/):
  run_manifest.json, summary.csv, summary.json, and per image:
    <stem>/before.jpg, after_<method>.jpg, detected_card_overlay.jpg,
    rectified_card.png, rectified_card_<method>.png, patch_samples.json,
    correction_<method>.json, metrics.json, cutsheet.jpg

Install (same as the quality analyzer):
  python3 -m pip install opencv-contrib-python pillow numpy

Example:
  python3 tools/bm_reference_card_color_smoke.py \
    --images ~/Downloads/SPOT-33361C_BMCAM_001_2026-09-01T17-00-25Z.jpg \
             ~/Downloads/SPOT-33361C_BMCAM_001_2026-09-01T18-00-24Z.jpg \
    --output-dir runs/sprint05_color_smoke_20260901

Assumptions / limitations:
  - Corner map tl:0,tr:1,bl:2,br:3 (V2 card tag IDs; same as the sweep tools).
  - Card targets are nominal design sRGB, not measured print values.
  - Detection failure falls back to --manual-card-corners JSON (Sprint05 §7).
  - Never skips an image silently: failures land in metrics.json + summary.csv.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import reference_card_color_utils as ccu  # noqa: E402

DEFAULT_TEMPLATE_DIR = TOOLS_DIR / "reference_card_color_correction" / "reference_card_template_v2"
CANONICAL_W, CANONICAL_H = 3000, 1000


def load_quality_module():
    """Import tools/bm_reference_card_quality_v2.py the same way the sweep tools do."""
    path = TOOLS_DIR / "bm_reference_card_quality_v2.py"
    spec = importlib.util.spec_from_file_location("bm_reference_card_quality_v2", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses need the module in sys.modules
    spec.loader.exec_module(mod)
    return mod


def parse_timestamp(name: str) -> str:
    """Pull a UTC timestamp out of names like ..._2026-09-01T18-00-24Z.jpg."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})Z", name)
    return f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}Z" if m else ""


def git_commit_or_unknown() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=TOOLS_DIR,
            capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Cut sheet
# ---------------------------------------------------------------------------

SHEET_W = 1720
MARGIN = 24
BG = (245, 247, 250)
INK = (30, 50, 70)
SUB = (95, 110, 130)


def _paste_scaled(sheet: Image.Image, img: Image.Image, x: int, y: int, w: int) -> int:
    """Paste img scaled to width w at (x, y); return its display height."""
    h = round(img.height * w / img.width)
    sheet.paste(img.resize((w, h), Image.Resampling.LANCZOS), (x, y))
    return h


def _swatch_strip(draw: ImageDraw.ImageDraw, x: int, y: int, samples, model, sw=46, sh=30):
    """Two-row strip: target swatches on top, observed (optionally corrected) below."""
    for i, s in enumerate(samples):
        sx = x + i * (sw + 4)
        tgt = tuple(int(v) for v in s.target_srgb)
        obs = s.median_srgb if model is None else model.apply_srgb255(s.median_srgb)
        obs = tuple(int(np.clip(round(v), 0, 255)) for v in obs)
        draw.rectangle((sx, y, sx + sw, y + sh), fill=tgt, outline=(180, 188, 198))
        draw.rectangle((sx, y + sh, sx + sw, y + 2 * sh), fill=obs, outline=(180, 188, 198))
    return 2 * sh


def build_cutsheet(qm, out_path: Path, stem: str, run_tag: str, detect_info: dict,
                   before_rgb: np.ndarray, overlay_path: Path,
                   rect_rgb, template_rgb, samples, before_metrics: dict,
                   method_results: list) -> None:
    f_title = qm.pil_font(30, True)
    f_h2 = qm.pil_font(20, True)
    f_txt = qm.pil_font(15)

    # Generous canvas; cropped to content at the end.
    n_rows = max(1, len(method_results))
    sheet = Image.new("RGB", (SHEET_W, 1400 + n_rows * 460), BG)
    d = ImageDraw.Draw(sheet)
    y = MARGIN

    d.text((MARGIN, y), "Reference Card Color-Correction Smoke Test", font=f_title, fill=INK)
    y += 40
    d.text((MARGIN, y), f"run={run_tag}   image={stem}   ts={before_metrics.get('timestamp_utc', '')}",
           font=f_txt, fill=SUB)
    y += 22
    d.text((MARGIN, y),
           f"detection: status={detect_info.get('quality_status')} tags={detect_info.get('tag_count')} "
           f"min_tag_side_px={detect_info.get('tag_side_px_min')} corners={detect_info.get('corner_source')}   "
           f"rectified={CANONICAL_W}x{CANONICAL_H} (canonical card frame)",
           font=f_txt, fill=SUB)
    y += 34

    # --- BEFORE + overlay ------------------------------------------------
    d.text((MARGIN, y), "BEFORE (as transmitted)", font=f_h2, fill=INK)
    d.text((MARGIN + 850, y), "AprilTag detection overlay", font=f_h2, fill=INK)
    y += 30
    half = (SHEET_W - 2 * MARGIN - 26) // 2
    h1 = _paste_scaled(sheet, Image.fromarray(before_rgb), MARGIN, y, half)
    h2 = 0
    if overlay_path.exists():
        with Image.open(overlay_path) as ov:
            h2 = _paste_scaled(sheet, ov.convert("RGB"), MARGIN + half + 26, y, half)
    y += max(h1, h2) + 18
    d.text((MARGIN, y),
           f"gray_neutrality={before_metrics.get('gray_neutrality_before'):.3f}   "
           f"mean_dE76_all_patches={before_metrics.get('mean_patch_de_before'):.1f}   "
           f"mean_luma={before_metrics.get('mean_luma_before'):.1f}   "
           f"clip%low/high={before_metrics.get('clip_percent_low_before')}/{before_metrics.get('clip_percent_high_before')}",
           font=f_txt, fill=INK)
    y += 34

    # --- Card row: rectified vs template + swatch strip -------------------
    if rect_rgb is not None:
        d.text((MARGIN, y), "Rectified card (from image)", font=f_h2, fill=INK)
        d.text((MARGIN + 850, y), "Design template (targets)", font=f_h2, fill=INK)
        y += 30
        h1 = _paste_scaled(sheet, Image.fromarray(rect_rgb), MARGIN, y, half)
        h2 = _paste_scaled(sheet, Image.fromarray(template_rgb), MARGIN + half + 26, y, half)
        y += max(h1, h2) + 14
        d.text((MARGIN, y), "Patches — top: design target, bottom: measured (uncorrected)",
               font=f_txt, fill=SUB)
        y += 22
        y += _swatch_strip(d, MARGIN, y, samples, model=None) + 30

    # --- Per-method rows ---------------------------------------------------
    for res in method_results:
        d.line((MARGIN, y, SHEET_W - MARGIN, y), fill=(205, 212, 220), width=2)
        y += 12
        d.text((MARGIN, y), f"AFTER — {res['method']}", font=f_h2, fill=INK)
        y += 30
        img_w = 760
        h1 = _paste_scaled(sheet, Image.fromarray(res["corrected_full"]), MARGIN, y, img_w)
        tx = MARGIN + img_w + 26
        m = res["metrics"]
        lines = [
            f"mean_dE76 all/color: {m['mean_patch_de_after']:.1f} / {m['mean_patch_de_color_after']:.1f}"
            f"   (before {before_metrics.get('mean_patch_de_before'):.1f})",
            f"max_dE76: {m['max_patch_de_after']:.1f}   gray_neutrality: {m['gray_neutrality_after']:.3f}"
            f"   (before {before_metrics.get('gray_neutrality_before'):.3f})",
            f"mean_luma: {m['mean_luma_after']:.1f}   clip%low/high: "
            f"{m['clip_percent_low_after']}/{m['clip_percent_high_after']}",
        ] + [f"note: {n}" for n in res["model"].notes]
        for j, line in enumerate(lines):
            d.text((tx, y + j * 24), line, font=f_txt, fill=INK)
        ty = y + len(lines) * 24 + 12
        d.text((tx, ty), "Patches — top: target, bottom: corrected", font=f_txt, fill=SUB)
        ty += 22
        ty += _swatch_strip(d, tx, ty, samples, model=res["model"], sw=42, sh=26)
        if res["corrected_rect"] is not None:
            ch = _paste_scaled(sheet, Image.fromarray(res["corrected_rect"]), tx, ty + 10,
                               SHEET_W - MARGIN - tx)
            ty += 10 + ch
        y += max(h1, ty - y) + 18

    sheet = sheet.crop((0, 0, SHEET_W, min(sheet.height, y + MARGIN)))
    sheet.save(out_path, quality=92)


# ---------------------------------------------------------------------------
# Per-image processing
# ---------------------------------------------------------------------------

def load_manual_corners(path: Path) -> dict:
    """Sprint05 §7 fallback: {image_name: quad float32 (tl,tr,br,bl)}."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw if isinstance(raw, list) else [raw]
    out = {}
    for e in entries:
        c = e["corners"]
        out[e["image"]] = np.array(
            [c["top_left"], c["top_right"], c["bottom_right"], c["bottom_left"]],
            dtype=np.float32)
    return out


def process_image(qm, img_path: Path, out_dir: Path, layout, template_rgb,
                  methods: list, args, manual_corners: dict, run_tag: str) -> dict:
    stem = img_path.stem
    img_dir = out_dir / stem
    img_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {img_path.name} ===")

    img_bgr = qm.load_image_bgr(img_path)
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = img_rgb.shape[:2]
    Image.fromarray(img_rgb).save(img_dir / "before.jpg", quality=95)

    row = {
        "image": img_path.name,
        "timestamp_utc": parse_timestamp(img_path.name),
        "image_width": w, "image_height": h,
        "image_size_kb": round(img_path.stat().st_size / 1024, 1),
        "card_detected": False, "tag_count": 0, "tag_ids": "",
        "tag_side_px_min": "", "quality_status": "", "corner_source": "",
        "notes": [],
    }

    # --- detect + rectify --------------------------------------------------
    corner_map = qm.parse_corner_map(args.corner_map)
    tag_metrics, corners_by_id, best_scale, _rej = qm.detect_tags(
        img_bgr, args.tag_family, args.scales)
    fid_quad, corner_status, geom_resid = qm.infer_card_corners_from_tags(
        corners_by_id, corner_map)
    sides = [tm.side_px_min for tm in tag_metrics]
    row.update({
        "tag_count": len(tag_metrics),
        "tag_ids": " ".join(str(t.tag_id) for t in sorted(tag_metrics, key=lambda t: t.tag_id)),
        "tag_side_px_min": round(min(sides), 1) if sides else "",
        "detector_best_scale": best_scale,
    })
    print(f"  tags={row['tag_count']} ids=[{row['tag_ids']}] "
          f"min_side={row['tag_side_px_min']}px scale={best_scale}")

    card_quad, corner_source = None, ""
    if fid_quad is not None:
        card_quad = qm.expand_quad(fid_quad, args.card_expand_x, args.card_expand_y)
        corner_source = "apriltag"
    elif img_path.name in manual_corners:
        card_quad = manual_corners[img_path.name]
        corner_source = "manual"
        row["notes"].append("card corners from --manual-card-corners")
        print("  using manual card corners")
    row["corner_source"] = corner_source
    row["quality_status"] = qm.quality_status(
        row["tag_count"], float(row["tag_side_px_min"] or 0), geom_resid,
        card_quad is not None)

    qm.draw_annotation(img_bgr, tag_metrics, corners_by_id, fid_quad, card_quad,
                       img_dir / "detected_card_overlay.jpg")

    if card_quad is None:
        row["notes"].append("card not detected; no correction attempted")
        print("  FAIL: card not detected (and no manual corners) — skipping correction")
        (img_dir / "metrics.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        return row

    row["card_detected"] = True
    rect_bgr = qm.rectify_quad(img_bgr, card_quad, CANONICAL_W, CANONICAL_H)
    rect_rgb = cv2.cvtColor(rect_bgr, cv2.COLOR_BGR2RGB)
    Image.fromarray(rect_rgb).save(img_dir / "rectified_card.png")

    # --- sample patches ----------------------------------------------------
    samples = ccu.sample_patches(rect_rgb, layout, inset_frac=args.patch_inset)
    (img_dir / "patch_samples.json").write_text(
        json.dumps([s.to_dict() for s in samples], indent=2), encoding="utf-8")
    de_before = ccu.patch_delta_e(samples)
    img_lin = ccu.srgb_to_linear(img_rgb.astype(np.float64) / 255.0)
    row.update({
        "patch_count_used": len(samples),
        "gray_neutrality_before": round(ccu.gray_neutrality(samples), 4),
        "mean_patch_de_before": round(float(np.mean(list(de_before.values()))), 2),
        "mean_luma_before": round(float(img_rgb.mean()), 1),
        **{k + "_before": v for k, v in ccu.clip_stats_srgb255(img_rgb).items()},
    })
    grays = " ".join(f"{s.patch_id}={[round(v) for v in s.median_srgb]}"
                     for s in samples if s.patch_type == "gray")
    print(f"  sampled {len(samples)} patches; gray medians: {grays}")
    print(f"  before: gray_neutrality={row['gray_neutrality_before']} "
          f"mean_dE={row['mean_patch_de_before']}")

    # --- run each correction method ---------------------------------------
    method_results = []
    for name in methods:
        try:
            model = ccu.METHOD_REGISTRY[name](samples, img_lin)
            corrected = np.clip(np.rint(model.apply_srgb255(img_rgb)), 0, 255).astype(np.uint8)
            corrected_rect = np.clip(np.rint(model.apply_srgb255(rect_rgb)), 0, 255).astype(np.uint8)
            Image.fromarray(corrected).save(img_dir / f"after_{name}.jpg", quality=95)
            Image.fromarray(corrected_rect).save(img_dir / f"rectified_card_{name}.png")
            (img_dir / f"correction_{name}.json").write_text(
                json.dumps(model.to_dict(), indent=2), encoding="utf-8")
            de_after = ccu.patch_delta_e(samples, model)
            de_color = [v for k, v in de_after.items()
                        if next(s for s in samples if s.patch_id == k).patch_type == "color"]
            metrics = {
                "gray_neutrality_after": round(ccu.gray_neutrality(samples, model), 4),
                "mean_patch_de_after": round(float(np.mean(list(de_after.values()))), 2),
                "mean_patch_de_color_after": round(float(np.mean(de_color)), 2),
                "max_patch_de_after": round(float(np.max(list(de_after.values()))), 2),
                "mean_luma_after": round(float(corrected.mean()), 1),
                **{k + "_after": v for k, v in ccu.clip_stats_srgb255(corrected).items()},
                "per_patch_de_after": {k: round(v, 2) for k, v in de_after.items()},
            }
            method_results.append({"method": name, "model": model, "metrics": metrics,
                                   "corrected_full": corrected,
                                   "corrected_rect": corrected_rect})
            print(f"  {name:12s} mean_dE {row['mean_patch_de_before']:6.1f} -> "
                  f"{metrics['mean_patch_de_after']:6.1f}   "
                  f"neutrality {row['gray_neutrality_before']:.3f} -> "
                  f"{metrics['gray_neutrality_after']:.3f}")
        except Exception as exc:  # keep going; failures must stay visible
            row["notes"].append(f"{name} failed: {type(exc).__name__}: {exc}")
            print(f"  {name:12s} FAILED: {exc}")

    row["methods"] = {r["method"]: r["metrics"] for r in method_results}
    row["per_patch_de_before"] = {k: round(v, 2) for k, v in de_before.items()}
    (img_dir / "metrics.json").write_text(json.dumps(row, indent=2), encoding="utf-8")

    cutsheet = img_dir / "cutsheet.jpg"
    build_cutsheet(qm, cutsheet, stem, run_tag,
                   detect_info=row, before_rgb=img_rgb,
                   overlay_path=img_dir / "detected_card_overlay.jpg",
                   rect_rgb=rect_rgb, template_rgb=template_rgb, samples=samples,
                   before_metrics=row, method_results=method_results)
    row["cutsheet_path"] = str(cutsheet)
    print(f"  cutsheet: {cutsheet}")
    return row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--images", nargs="*", default=[], help="explicit image paths")
    ap.add_argument("--input-dir", default="", help="folder to scan (with --image-glob)")
    ap.add_argument("--image-glob", default="*.jpg")
    ap.add_argument("--output-dir", default="",
                    help="default: runs/sprint05_color_smoke_<YYYYMMDD>")
    ap.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    ap.add_argument("--methods", nargs="*", default=list(ccu.METHOD_REGISTRY),
                    choices=list(ccu.METHOD_REGISTRY), metavar="METHOD",
                    help=f"subset of: {' '.join(ccu.METHOD_REGISTRY)}")
    ap.add_argument("--tag-family", default="DICT_APRILTAG_36h11")
    ap.add_argument("--scales", nargs="+", type=float, default=[1, 2, 3, 4, 6, 8])
    ap.add_argument("--corner-map", default="tl:0,tr:1,bl:2,br:3")
    ap.add_argument("--card-expand-x", type=float, default=1.25)
    ap.add_argument("--card-expand-y", type=float, default=2.0)
    ap.add_argument("--patch-inset", type=float, default=0.30,
                    help="fraction shaved off each patch-box side before sampling")
    ap.add_argument("--manual-card-corners", default="",
                    help="JSON fallback corners per Sprint05 §7")
    args = ap.parse_args()

    images = [Path(p).expanduser().resolve() for p in args.images]
    if args.input_dir:
        images += sorted(Path(args.input_dir).expanduser().resolve().glob(args.image_glob))
    images = [p for p in dict.fromkeys(images)]  # dedupe, keep order
    if not images:
        raise SystemExit("No input images (use --images or --input-dir)")
    missing = [p for p in images if not p.exists()]
    if missing:
        raise SystemExit(f"Missing inputs: {missing}")

    run_tag = f"sprint05_color_smoke_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    out_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir \
        else TOOLS_DIR.parent / "runs" / run_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    template_dir = Path(args.template_dir).expanduser().resolve()
    layout = ccu.load_template(template_dir / "template_layout.json")
    template_rgb = np.asarray(Image.open(
        template_dir / "reference_card_template_3000x1000.png").convert("RGB"))
    manual_corners = (load_manual_corners(Path(args.manual_card_corners))
                      if args.manual_card_corners else {})

    print(f"run_tag={run_tag}")
    print(f"output={out_dir}")
    print(f"template={template_dir}")
    print(f"methods={' '.join(args.methods)}")
    print(f"images ({len(images)}):")
    for p in images:
        print(f"  {p}")

    qm = load_quality_module()
    rows = []
    for img_path in images:
        try:
            rows.append(process_image(qm, img_path, out_dir, layout, template_rgb,
                                      args.methods, args, manual_corners, run_tag))
        except Exception as exc:
            print(f"  ERROR on {img_path.name}: {type(exc).__name__}: {exc}")
            rows.append({"image": img_path.name, "card_detected": False,
                         "quality_status": "ERROR",
                         "notes": [f"{type(exc).__name__}: {exc}"]})

    # --- summary -----------------------------------------------------------
    (out_dir / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    csv_fields = ["image", "timestamp_utc", "card_detected", "quality_status",
                  "corner_source", "tag_count", "tag_ids", "tag_side_px_min",
                  "patch_count_used", "method",
                  "gray_neutrality_before", "gray_neutrality_after",
                  "mean_patch_de_before", "mean_patch_de_after",
                  "mean_patch_de_color_after", "max_patch_de_after",
                  "mean_luma_before", "mean_luma_after",
                  "clip_percent_low_after", "clip_percent_high_after",
                  "cutsheet_path", "notes"]
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        wcsv = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        wcsv.writeheader()
        for r in rows:
            base = {k: r.get(k, "") for k in csv_fields}
            base["notes"] = "; ".join(r.get("notes", []))
            if r.get("methods"):
                for name, m in r["methods"].items():
                    wcsv.writerow({**base, "method": name,
                                   **{k: m.get(k, "") for k in csv_fields if k in m}})
            else:
                wcsv.writerow(base)

    manifest = {
        "run_tag": run_tag,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit_or_unknown(),
        "command": " ".join(sys.argv),
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "images": [str(p) for p in images],
        "template_dir": str(template_dir),
        "template_name": layout.get("template_name"),
        "methods": args.methods,
        "versions": {"python": sys.version.split()[0], "opencv": cv2.__version__,
                     "numpy": np.__version__},
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2),
                                               encoding="utf-8")

    print(f"\nsummary_csv={out_dir / 'summary.csv'}")
    print(f"manifest={out_dir / 'run_manifest.json'}")
    ok = sum(1 for r in rows if r.get("card_detected"))
    print(f"done: {ok}/{len(rows)} images with card detected")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
