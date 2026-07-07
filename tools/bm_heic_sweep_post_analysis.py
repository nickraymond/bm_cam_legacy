#!/usr/bin/env python3
"""
BM HEIC Sweep Post Analysis

Reads an existing Sprint 03 HEIC compression sweep output folder and creates:

1. Transmission-duration heat maps:
   - rows: starting spatial sampling density / source resolution
   - columns: HEIC quality
   - cell text: 300-byte message count + estimated transmission duration
   - colors: green/yellow/orange/red by duration

2. Constant-quality reference-card cut sheets:
   - one sheet per crop mode and HEIC quality
   - compares reference-card crops across source resolutions at constant HEIC quality
   - includes both normalized-display and true 1:1 native-pixel sheets

This script does NOT rerun HEIC encoding. It post-processes an existing folder from:
  bm_reference_card_heic_compression_sweep.py

Expected input:
  <heic_sweep>/results/heic_compression_quality_all.csv

Optional but recommended:
  --quality-script ./bm_reference_card_quality_v2.py

The quality script is used only as a detector/crop helper so the card trim is based
on the same AprilTag geometry used in prior analyses.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import re
import sys
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_CORNER_MAP = "tl:0,tr:1,bl:2,br:3"
DEFAULT_SCALES = [1, 2, 3, 4]
DEFAULT_QUALITIES = list(range(10, 100, 10))
DEFAULT_RESOLUTIONS = ["3072x1728", "2688x1512", "2304x1296", "1920x1080", "1600x900"]

# User-requested duration color bands.
DURATION_BANDS = [
    (0.0, 10.0, "Green: 0-10 min", (116, 210, 134)),
    (10.0, 20.0, "Yellow: 10-20 min", (255, 236, 102)),
    (20.0, 25.0, "Orange: 20-25 min", (255, 168, 76)),
    (25.0, float("inf"), "Red: 25+ min", (230, 76, 70)),
]


@dataclass
class CropResult:
    ok: bool
    crop_path: str
    crop_width: int
    crop_height: int
    reason: str
    tag_count: int = 0
    tag_ids: str = ""
    bbox: Optional[Tuple[int, int, int, int]] = None


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def expand_path(p: str) -> Path:
    return Path(p).expanduser().resolve()


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_int(v, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


def safe_float(v, default: float = float("nan")) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def res_pixels(res: str) -> int:
    m = re.match(r"^(\d+)x(\d+)$", str(res).lower().replace("×", "x"))
    if not m:
        return 0
    return int(m.group(1)) * int(m.group(2))


def parse_resolution(res: str) -> Tuple[int, int]:
    m = re.match(r"^(\d+)x(\d+)$", str(res).lower().replace("×", "x"))
    if not m:
        raise ValueError(f"Invalid resolution {res!r}; expected WIDTHxHEIGHT")
    return int(m.group(1)), int(m.group(2))


def pil_font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, object]], fields: Optional[List[str]] = None) -> None:
    ensure_dir(path.parent)
    out_fields: List[str] = []
    if fields:
        out_fields.extend(fields)
    for r in rows:
        for k in r.keys():
            if k not in out_fields:
                out_fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_analyzer_module(path: Optional[Path]):
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"Quality script not found: {path}")
    spec = importlib.util.spec_from_file_location("bm_reference_card_quality_v2_post", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import analyzer module: {path}")
    mod = importlib.util.module_from_spec(spec)
    # dataclasses and some module-level introspection expect the module to be
    # present in sys.modules while it is executing.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def row_key(row: Dict[str, str]) -> Tuple[str, str, int]:
    return str(row.get("crop_mode", "")), str(row.get("resolution", "")), safe_int(row.get("heic_quality"), -1)


def load_heic_results(heic_sweep: Path) -> List[Dict[str, str]]:
    csv_path = heic_sweep / "results" / "heic_compression_quality_all.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected HEIC results CSV not found: {csv_path}")
    rows = read_csv_rows(csv_path)
    if not rows:
        raise RuntimeError(f"No rows found in {csv_path}")
    return rows


def duration_minutes_from_messages(messages: int, seconds_per_message: float) -> float:
    return (messages * seconds_per_message) / 60.0


def color_for_duration(minutes: float) -> Tuple[int, int, int]:
    for lo, hi, _label, color in DURATION_BANDS:
        if lo <= minutes < hi:
            return color
    return DURATION_BANDS[-1][3]


def dark_text_for_fill(color: Tuple[int, int, int]) -> bool:
    r, g, b = color
    # Perceptual brightness heuristic.
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150


def build_heatmap_data(
    rows: List[Dict[str, str]],
    mode: str,
    resolutions: Sequence[str],
    qualities: Sequence[int],
    seconds_per_message: float,
) -> List[Dict[str, object]]:
    by_key = {row_key(r): r for r in rows if str(r.get("crop_mode")) == mode}
    out: List[Dict[str, object]] = []
    for res in resolutions:
        for q in qualities:
            r = by_key.get((mode, res, q))
            if not r:
                out.append({
                    "crop_mode": mode,
                    "resolution": res,
                    "heic_quality": q,
                    "messages_300b": "",
                    "tx_seconds_per_message": seconds_per_message,
                    "tx_duration_min": "",
                    "heic_size_kb": "",
                    "quality_status": "MISSING",
                })
                continue
            messages = safe_int(r.get("messages_300b"), 0)
            if messages <= 0:
                heic_bytes = safe_int(r.get("heic_size_bytes"), 0)
                messages = int(math.ceil(heic_bytes / 300.0)) if heic_bytes > 0 else 0
            duration_min = duration_minutes_from_messages(messages, seconds_per_message) if messages > 0 else float("nan")
            out.append({
                "crop_mode": mode,
                "resolution": res,
                "heic_quality": q,
                "messages_300b": messages if messages > 0 else "",
                "tx_seconds_per_message": seconds_per_message,
                "tx_duration_min": round(duration_min, 2) if not math.isnan(duration_min) else "",
                "tx_duration_label": f"{duration_min:.1f} min" if not math.isnan(duration_min) else "",
                "heic_size_bytes": r.get("heic_size_bytes", ""),
                "heic_size_kb": r.get("heic_size_kb", ""),
                "messages_900b": r.get("messages_900b", ""),
                "quality_status": r.get("quality_status", ""),
                "tag_side_px_min": r.get("tag_side_px_min", ""),
                "same_resolution_ref_psnr_rgb": r.get("same_resolution_ref_psnr_rgb", ""),
                "decoded_analysis_path": r.get("decoded_analysis_path", ""),
                "heic_path": r.get("heic_path", ""),
            })
    return out


def make_transmission_heatmap(
    heat_rows: List[Dict[str, object]],
    mode: str,
    resolutions: Sequence[str],
    qualities: Sequence[int],
    seconds_per_message: float,
    out_path: Path,
) -> None:
    ensure_dir(out_path.parent)
    f_title = pil_font(30, True)
    f_sub = pil_font(16)
    f_axis = pil_font(17, True)
    f_cell = pil_font(16, True)
    f_small = pil_font(13)
    f_legend = pil_font(14)

    left = 150
    top = 118
    cell_w = 108
    cell_h = 76
    right = 30
    bottom = 110
    legend_h = 56
    w = left + len(qualities) * cell_w + right
    h = top + len(resolutions) * cell_h + bottom + legend_h

    sheet = Image.new("RGB", (w, h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    title = f"Transmission heat map: {mode} crop"
    subtitle = (
        "Rows = starting spatial sampling density; columns = HEIC quality. "
        f"Cell = 300-byte messages + duration at {seconds_per_message:g}s/message."
    )
    d.text((24, 22), title, font=f_title, fill=(30, 45, 60))
    d.text((24, 62), subtitle, font=f_sub, fill=(80, 90, 100))

    # Column labels.
    for j, q in enumerate(qualities):
        x = left + j * cell_w
        d.text((x + cell_w / 2 - 18, top - 30), f"Q{q}", font=f_axis, fill=(35, 50, 65))
    d.text((left, top - 62), "HEIC quality", font=f_small, fill=(80, 90, 100))

    by_cell = {(str(r["resolution"]), safe_int(r["heic_quality"], -1)): r for r in heat_rows}

    for i, res in enumerate(resolutions):
        y = top + i * cell_h
        d.text((22, y + cell_h / 2 - 10), res, font=f_axis, fill=(35, 50, 65))
        for j, q in enumerate(qualities):
            x = left + j * cell_w
            r = by_cell.get((res, q))
            if not r or r.get("messages_300b", "") == "":
                fill = (220, 224, 230)
                text = "missing"
                text_fill = (70, 80, 90)
            else:
                minutes = safe_float(r.get("tx_duration_min"), float("nan"))
                fill = color_for_duration(minutes)
                text_fill = (15, 25, 35) if dark_text_for_fill(fill) else (255, 255, 255)
                messages = safe_int(r.get("messages_300b"), 0)
                status = str(r.get("quality_status", ""))
                text = f"{messages} msg\n{minutes:.1f} min"
                if status:
                    text += f"\n{status}"
            d.rectangle((x, y, x + cell_w - 2, y + cell_h - 2), fill=fill, outline=(255, 255, 255), width=2)
            lines = text.split("\n")
            line_h = 18
            start_y = y + max(6, (cell_h - len(lines) * line_h) // 2)
            for k, line in enumerate(lines):
                # Approximate centering without relying on Pillow version-specific textbbox details.
                d.text((x + 10, start_y + k * line_h), line, font=f_cell if k < 2 else f_small, fill=text_fill)

    # Legend.
    ly = top + len(resolutions) * cell_h + 28
    lx = 24
    d.text((lx, ly - 22), "Color key by expected transmission duration", font=f_small, fill=(80, 90, 100))
    for lo, hi, label, color in DURATION_BANDS:
        d.rectangle((lx, ly, lx + 28, ly + 22), fill=color, outline=(120, 130, 140))
        d.text((lx + 36, ly + 2), label, font=f_legend, fill=(35, 50, 65))
        lx += 210

    sheet.save(out_path, quality=95)


def detect_and_crop_card(
    img_path: Path,
    out_path: Path,
    analyzer_mod,
    corner_map: str,
    tag_family: str,
    scales: Sequence[float],
    card_expand_x: float,
    card_expand_y: float,
    pad_px: int,
) -> CropResult:
    ensure_dir(out_path.parent)
    if analyzer_mod is None:
        return CropResult(False, "", 0, 0, "no analyzer module provided for AprilTag crop")
    try:
        img_bgr = analyzer_mod.load_image_bgr(img_path)
        h, w = img_bgr.shape[:2]
        cmap = analyzer_mod.parse_corner_map(corner_map)
        tag_metrics, corners_by_id, _best_scale, _rejected = analyzer_mod.detect_tags(img_bgr, tag_family, scales)
        tag_ids = " ".join(str(x.tag_id) for x in sorted(tag_metrics, key=lambda t: t.tag_id))
        fid_quad, _corner_status, _geom = analyzer_mod.infer_card_corners_from_tags(corners_by_id, cmap)
        if fid_quad is None:
            return CropResult(False, "", 0, 0, "unable to estimate card quad from detected tags", len(tag_metrics), tag_ids)
        card_quad = analyzer_mod.expand_quad(fid_quad, card_expand_x, card_expand_y)
        q = np.asarray(card_quad, dtype=np.float32).reshape(-1, 2)
        x0 = max(0, int(math.floor(float(np.min(q[:, 0])))) - pad_px)
        y0 = max(0, int(math.floor(float(np.min(q[:, 1])))) - pad_px)
        x1 = min(w, int(math.ceil(float(np.max(q[:, 0])))) + pad_px)
        y1 = min(h, int(math.ceil(float(np.max(q[:, 1])))) + pad_px)
        if x1 <= x0 or y1 <= y0:
            return CropResult(False, "", 0, 0, "computed card crop bbox is empty", len(tag_metrics), tag_ids)
        # Convert BGR numpy crop to RGB PIL without using cv2 directly here.
        crop_bgr = img_bgr[y0:y1, x0:x1]
        crop_rgb = crop_bgr[:, :, ::-1]
        im = Image.fromarray(crop_rgb.astype(np.uint8), "RGB")
        im.save(out_path, format="PNG")
        return CropResult(True, str(out_path), im.width, im.height, "ok", len(tag_metrics), tag_ids, (x0, y0, x1, y1))
    except Exception as exc:
        return CropResult(False, "", 0, 0, f"{type(exc).__name__}: {exc}")


def make_placeholder(size: Tuple[int, int], lines: Sequence[str]) -> Image.Image:
    im = Image.new("RGB", size, (232, 235, 240))
    d = ImageDraw.Draw(im)
    f = pil_font(16, True)
    fs = pil_font(13)
    d.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(150, 155, 165), width=2)
    y = 18
    for i, line in enumerate(lines):
        d.text((14, y), line[:62], font=f if i == 0 else fs, fill=(70, 80, 90))
        y += 22
    return im


def make_constant_quality_sheet(
    rows_for_q: List[Dict[str, str]],
    crops_by_key: Dict[Tuple[str, str, int], CropResult],
    mode: str,
    q: int,
    resolutions: Sequence[str],
    out_path: Path,
    normalized: bool,
    normalized_size: Tuple[int, int],
    seconds_per_message: float,
) -> None:
    ensure_dir(out_path.parent)
    f_title = pil_font(28, True)
    f_sub = pil_font(15)
    f_label = pil_font(14, True)
    f_small = pil_font(12)

    rows_by_res = {str(r.get("resolution")): r for r in rows_for_q}
    crop_results = [crops_by_key.get((mode, res, q)) for res in resolutions]
    valid_crops = [c for c in crop_results if c is not None and c.ok and c.crop_width > 0 and c.crop_height > 0]

    if normalized:
        display_w, display_h = normalized_size
    elif valid_crops:
        display_w = max(c.crop_width for c in valid_crops)
        display_h = max(c.crop_height for c in valid_crops)
        # Keep sheets from getting accidentally enormous if a crop goes wild.
        display_w = min(display_w, 1400)
        display_h = min(display_h, 760)
    else:
        display_w, display_h = normalized_size

    tile_w = display_w + 24
    tile_h = display_h + 132
    margin = 24
    cols = min(len(resolutions), 5)
    nrows = int(math.ceil(len(resolutions) / cols))
    sheet_w = margin * 2 + cols * tile_w
    sheet_h = margin * 2 + 96 + nrows * tile_h
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)

    kind = "normalized same-size card display" if normalized else "true 1:1 native-pixel card crops"
    d.text((margin, margin), f"Constant HEIC Q{q}: {mode} card crops", font=f_title, fill=(30, 45, 60))
    d.text((margin, margin + 38), f"{kind}. Compare spatial sampling density, file size, messages, and duration.", font=f_sub, fill=(80, 90, 100))

    for idx, res in enumerate(resolutions):
        r = rows_by_res.get(res)
        crop = crops_by_key.get((mode, res, q))
        x = margin + (idx % cols) * tile_w
        y = margin + 96 + (idx // cols) * tile_h
        d.rectangle((x, y, x + tile_w - 10, y + tile_h - 10), fill=(255, 255, 255), outline=(200, 210, 220))
        canvas = Image.new("RGB", (display_w, display_h), (226, 230, 235))

        if crop and crop.ok and Path(crop.crop_path).exists():
            with Image.open(crop.crop_path) as im0:
                im = im0.convert("RGB")
                if normalized:
                    im.thumbnail((display_w, display_h), Image.Resampling.LANCZOS)
                else:
                    # 1:1: do not scale unless the computed crop exceeds the safety cap.
                    if im.width > display_w or im.height > display_h:
                        im.thumbnail((display_w, display_h), Image.Resampling.LANCZOS)
                        # Label will still show native crop dimensions; this rare case indicates a crop safety cap.
                canvas.paste(im, ((display_w - im.width) // 2, (display_h - im.height) // 2))
        else:
            reason = crop.reason if crop else "missing row/crop"
            canvas = make_placeholder((display_w, display_h), ["CARD CROP FAILED", reason])

        sheet.paste(canvas, (x + 8, y + 8))
        ly = y + display_h + 18
        messages = safe_int(r.get("messages_300b") if r else "", 0)
        duration = duration_minutes_from_messages(messages, seconds_per_message) if messages else float("nan")
        heic_kb = r.get("heic_size_kb", "") if r else ""
        status = r.get("quality_status", "") if r else "MISSING"
        min_tag = r.get("tag_side_px_min", "") if r else ""
        psnr = r.get("same_resolution_ref_psnr_rgb", "") if r else ""
        crop_dim = f"{crop.crop_width}x{crop.crop_height}" if crop and crop.ok else "n/a"
        lines = [
            f"{res}  Q{q}  {status}",
            f"HEIC={heic_kb} KB  300B msgs={messages if messages else ''}",
            f"tx={duration:.1f} min @ {seconds_per_message:g}s/msg" if not math.isnan(duration) else f"tx=n/a @ {seconds_per_message:g}s/msg",
            f"card crop px={crop_dim}  min_tag_px={min_tag}",
            f"PSNR vs source={psnr}",
        ]
        for j, line in enumerate(lines):
            d.text((x + 12, ly + j * 21), line, font=f_label if j == 0 else f_small, fill=(40, 55, 70))

    sheet.save(out_path, format="PNG")


def generate_card_crops_and_constant_quality_sheets(
    rows: List[Dict[str, str]],
    modes: Sequence[str],
    resolutions: Sequence[str],
    qualities: Sequence[int],
    out_dir: Path,
    prefix: str,
    analyzer_mod,
    corner_map: str,
    tag_family: str,
    scales: Sequence[float],
    card_expand_x: float,
    card_expand_y: float,
    pad_px: int,
    normalized_size: Tuple[int, int],
    seconds_per_message: float,
) -> List[Dict[str, object]]:
    crop_rows: List[Dict[str, object]] = []
    crops_by_key: Dict[Tuple[str, str, int], CropResult] = {}
    by_key = {row_key(r): r for r in rows}

    for mode in modes:
        for q in qualities:
            rows_for_q: List[Dict[str, str]] = []
            for res in resolutions:
                r = by_key.get((mode, res, q))
                if not r:
                    continue
                rows_for_q.append(r)
                decoded = r.get("decoded_analysis_path", "")
                out_crop = out_dir / "card_crops_native_px" / mode / f"q{q:02d}" / f"{mode}_{res}_q{q:02d}_card_crop.png"
                if decoded and Path(decoded).exists():
                    crop = detect_and_crop_card(
                        Path(decoded), out_crop, analyzer_mod, corner_map, tag_family, scales,
                        card_expand_x, card_expand_y, pad_px,
                    )
                else:
                    crop = CropResult(False, "", 0, 0, f"decoded image missing: {decoded}")
                crops_by_key[(mode, res, q)] = crop
                crop_rows.append({
                    "crop_mode": mode,
                    "resolution": res,
                    "heic_quality": q,
                    "decoded_analysis_path": decoded,
                    "card_crop_ok": crop.ok,
                    "card_crop_path": crop.crop_path,
                    "card_crop_width": crop.crop_width,
                    "card_crop_height": crop.crop_height,
                    "card_crop_reason": crop.reason,
                    "card_crop_bbox": crop.bbox,
                    "detected_tag_count_for_crop": crop.tag_count,
                    "detected_tag_ids_for_crop": crop.tag_ids,
                    "quality_status": r.get("quality_status", ""),
                    "heic_size_kb": r.get("heic_size_kb", ""),
                    "messages_300b": r.get("messages_300b", ""),
                    "messages_900b": r.get("messages_900b", ""),
                    "tag_side_px_min": r.get("tag_side_px_min", ""),
                    "same_resolution_ref_psnr_rgb": r.get("same_resolution_ref_psnr_rgb", ""),
                })

            if not rows_for_q:
                continue
            norm_path = out_dir / "cut_sheets" / "constant_quality" / f"{prefix}_constant_Q{q:02d}_{mode}_card_crops_normalized.png"
            one_path = out_dir / "cut_sheets" / "constant_quality_1to1" / f"{prefix}_constant_Q{q:02d}_{mode}_card_crops_1to1.png"
            make_constant_quality_sheet(
                rows_for_q, crops_by_key, mode, q, resolutions, norm_path,
                normalized=True, normalized_size=normalized_size, seconds_per_message=seconds_per_message,
            )
            make_constant_quality_sheet(
                rows_for_q, crops_by_key, mode, q, resolutions, one_path,
                normalized=False, normalized_size=normalized_size, seconds_per_message=seconds_per_message,
            )

    return crop_rows


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Post-process HEIC sweep results into transmission heat maps and constant-quality card cut sheets.")
    ap.add_argument("--heic-sweep", "--input", dest="heic_sweep", required=True,
                    help="Path to HEIC sweep output directory containing results/heic_compression_quality_all.csv")
    ap.add_argument("--output", default="",
                    help="Output directory. Default: <heic-sweep>/post_analysis_<UTC>")
    ap.add_argument("--quality-script", default="./bm_reference_card_quality_v2.py",
                    help="Path to bm_reference_card_quality_v2.py. Used to crop cards from decoded images.")
    ap.add_argument("--crop-modes", nargs="+", default=[],
                    help="Crop modes to include. Default: infer from results CSV.")
    ap.add_argument("--resolutions", nargs="+", default=DEFAULT_RESOLUTIONS,
                    help="Resolution row order. Default: 3072x1728 2688x1512 2304x1296 1920x1080 1600x900")
    ap.add_argument("--qualities", nargs="+", type=int, default=DEFAULT_QUALITIES,
                    help="HEIC quality columns/sheets. Default: 10 20 ... 90")
    ap.add_argument("--seconds-per-message", type=float, default=5.0,
                    help="Transmission duration assumption per message. Default: 5 seconds")
    ap.add_argument("--corner-map", default=DEFAULT_CORNER_MAP,
                    help="AprilTag corner map. Default: tl:0,tr:1,bl:2,br:3")
    ap.add_argument("--tag-family", default="DICT_APRILTAG_36h11")
    ap.add_argument("--scales", nargs="+", type=float, default=DEFAULT_SCALES,
                    help="AprilTag detector scales for card crop. Default: 1 2 3 4")
    ap.add_argument("--card-expand-x", type=float, default=1.25,
                    help="Card crop expansion in x, matching analyzer default. Default: 1.25")
    ap.add_argument("--card-expand-y", type=float, default=2.0,
                    help="Card crop expansion in y, matching analyzer default. Default: 2.0")
    ap.add_argument("--card-crop-pad-px", type=int, default=12,
                    help="Extra axis-aligned padding around estimated card crop. Default: 12 px")
    ap.add_argument("--normalized-card-width", type=int, default=900,
                    help="Display width for normalized constant-quality cut sheets. Default: 900")
    ap.add_argument("--normalized-card-height", type=int, default=390,
                    help="Display height for normalized constant-quality cut sheets. Default: 390")
    ap.add_argument("--prefix", default="test", help="Prefix for output image names. Default: test")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    heic_sweep = expand_path(args.heic_sweep)
    out_dir = expand_path(args.output) if args.output else heic_sweep / f"post_analysis_{utc_stamp()}"
    ensure_dir(out_dir)

    manifest: Dict[str, object] = {
        "created_utc": utc_stamp(),
        "heic_sweep": str(heic_sweep),
        "output_dir": str(out_dir),
        "quality_script": str(expand_path(args.quality_script)),
        "seconds_per_message": args.seconds_per_message,
        "duration_bands": [
            {"min_minutes": lo, "max_minutes": hi if math.isfinite(hi) else None, "label": label, "rgb": color}
            for lo, hi, label, color in DURATION_BANDS
        ],
        "warnings": [],
        "errors": [],
    }

    try:
        print(f"[heic-post] heic_sweep={heic_sweep}")
        print(f"[heic-post] output={out_dir}")
        rows = load_heic_results(heic_sweep)
        modes = args.crop_modes or sorted({str(r.get("crop_mode", "")) for r in rows if r.get("crop_mode")})
        resolutions = [str(r) for r in args.resolutions]
        qualities = sorted(set(int(q) for q in args.qualities))
        manifest["crop_modes"] = modes
        manifest["resolutions"] = resolutions
        manifest["qualities"] = qualities

        print("[heic-post] creating transmission heat maps")
        all_heat_rows: List[Dict[str, object]] = []
        for mode in modes:
            heat_rows = build_heatmap_data(rows, mode, resolutions, qualities, args.seconds_per_message)
            all_heat_rows.extend(heat_rows)
            write_csv(
                out_dir / "results" / f"transmission_heatmap_data_{mode}.csv",
                heat_rows,
                fields=[
                    "crop_mode", "resolution", "heic_quality", "messages_300b", "tx_seconds_per_message",
                    "tx_duration_min", "tx_duration_label", "heic_size_bytes", "heic_size_kb",
                    "messages_900b", "quality_status", "tag_side_px_min", "same_resolution_ref_psnr_rgb",
                    "decoded_analysis_path", "heic_path",
                ],
            )
            make_transmission_heatmap(
                heat_rows, mode, resolutions, qualities, args.seconds_per_message,
                out_dir / "heatmaps" / f"{args.prefix}_transmission_heatmap_{mode}_300b_{int(args.seconds_per_message)}s.png",
            )
        write_csv(out_dir / "results" / "transmission_heatmap_data_all.csv", all_heat_rows)

        print("[heic-post] importing analyzer for true card crops")
        analyzer_mod = load_analyzer_module(expand_path(args.quality_script))

        print("[heic-post] creating constant-quality card crop sheets")
        crop_rows = generate_card_crops_and_constant_quality_sheets(
            rows=rows,
            modes=modes,
            resolutions=resolutions,
            qualities=qualities,
            out_dir=out_dir,
            prefix=args.prefix,
            analyzer_mod=analyzer_mod,
            corner_map=args.corner_map,
            tag_family=args.tag_family,
            scales=args.scales,
            card_expand_x=args.card_expand_x,
            card_expand_y=args.card_expand_y,
            pad_px=args.card_crop_pad_px,
            normalized_size=(args.normalized_card_width, args.normalized_card_height),
            seconds_per_message=args.seconds_per_message,
        )
        write_csv(out_dir / "results" / "card_crop_manifest.csv", crop_rows)

        manifest["outputs"] = {
            "heatmaps": str(out_dir / "heatmaps"),
            "constant_quality_normalized_sheets": str(out_dir / "cut_sheets" / "constant_quality"),
            "constant_quality_1to1_sheets": str(out_dir / "cut_sheets" / "constant_quality_1to1"),
            "native_card_crops": str(out_dir / "card_crops_native_px"),
            "transmission_heatmap_data_all": str(out_dir / "results" / "transmission_heatmap_data_all.csv"),
            "card_crop_manifest": str(out_dir / "results" / "card_crop_manifest.csv"),
        }
        (out_dir / "post_analysis_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        print("[heic-post] complete")
        print(f"[heic-post] heatmaps={out_dir / 'heatmaps'}")
        print(f"[heic-post] cut_sheets={out_dir / 'cut_sheets'}")
        print(f"[heic-post] results={out_dir / 'results'}")
        return 0
    except Exception as exc:
        manifest["errors"].append({
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })
        ensure_dir(out_dir)
        (out_dir / "post_analysis_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[heic-post] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"[heic-post] manifest written: {out_dir / 'post_analysis_manifest.json'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
