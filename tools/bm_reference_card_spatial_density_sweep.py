#!/usr/bin/env python3
"""
BM Reference Card Spatial Density Sweep

Mac-side Sprint 02 wrapper for selecting a practical 16:9 spatial sampling
resolution before HEIC/compression testing.

This script:
  1. Takes a native full-resolution JPEG.
  2. Builds a fixed production-720-like ROI crop.
  3. Optionally builds an auto-card-centered ROI crop.
  4. Creates a high-quality 16:9 downsample ladder.
  5. Runs bm_reference_card_quality_v2.py as the metrics engine.
  6. Applies stricter Sprint 02 PASS/WARN/FAIL logic requiring all four tags.
  7. Writes final CSVs, cut sheets, and a run manifest.

Install:
  python3 -m pip install opencv-contrib-python pillow numpy

Example:
  python3 bm_reference_card_spatial_density_sweep.py \
    --input ~/Downloads/bm_native_reference_captures/<run>/native_full_q95.jpg \
    --output ~/Downloads/bm_spatial_density_sweep/test_$(date -u +%Y%m%dT%H%M%SZ) \
    --fixed-crop 768,432,3072,1728 \
    --corner-map tl:0,tr:1,bl:2,br:3 \
    --include-auto-centered \
    --quality-script ./bm_reference_card_quality_v2.py \
    --prefix test
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_FIXED_CROP = (768, 432, 3072, 1728)
DEFAULT_CORNER_MAP = "tl:0,tr:1,bl:2,br:3"
DEFAULT_LADDER = [
    (3072, 1728),
    (2688, 1512),
    (2304, 1296),
    (1920, 1080),
    (1600, 900),
    (1280, 720),
    (1024, 576),
    (854, 480),
    (640, 360),
]
DEFAULT_ANALYZER_SCALES = [1, 2, 3, 4]
EXPECTED_NATIVE_SIZE = (4608, 2592)
BASE_WIDTH = 3072
BASE_HEIGHT = 1728
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class CropInfo:
    crop_mode: str
    source_roi_x: int
    source_roi_y: int
    source_roi_w: int
    source_roi_h: int
    roi_path: str
    auto_center_x: Optional[float] = None
    auto_center_y: Optional[float] = None
    auto_status: str = ""
    auto_reason: str = ""


@dataclass
class DownsampleInfo:
    crop_mode: str
    output_width: int
    output_height: int
    source_roi_x: int
    source_roi_y: int
    source_roi_w: int
    source_roi_h: int
    image_path: str
    image_size_bytes: int
    image_size_kb: float
    relative_output_scale_vs_3072: float
    output_pixels_total: int


# -----------------------------------------------------------------------------
# Basic parsing / IO helpers
# -----------------------------------------------------------------------------


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def expand_path(p: str) -> Path:
    return Path(p).expanduser().resolve()


def parse_crop(s: str) -> Tuple[int, int, int, int]:
    try:
        parts = [int(x.strip()) for x in s.split(",")]
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"Invalid crop {s!r}; expected x,y,w,h") from exc
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"Invalid crop {s!r}; expected x,y,w,h")
    x, y, w, h = parts
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError(f"Invalid crop {s!r}; w/h must be positive")
    return x, y, w, h


def parse_resolution(s: str) -> Tuple[int, int]:
    sep = "x" if "x" in s.lower() else ","
    parts = s.lower().replace("×", "x").split(sep)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Invalid resolution {s!r}; expected WIDTHxHEIGHT")
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid resolution {s!r}; expected WIDTHxHEIGHT") from exc
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError(f"Invalid resolution {s!r}; dimensions must be positive")
    return w, h


def parse_corner_map(s: str) -> Dict[str, Optional[int]]:
    out: Dict[str, Optional[int]] = {"tl": None, "tr": None, "bl": None, "br": None}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"Invalid corner-map entry {part!r}; expected key:value")
        k, v = [x.strip().lower() for x in part.split(":", 1)]
        if k not in out:
            raise ValueError(f"Invalid corner-map key {k!r}; expected tl,tr,bl,br")
        out[k] = None if v in {"", "none", "null"} else int(v)
    return out


def required_tag_ids(corner_map: Dict[str, Optional[int]]) -> List[int]:
    ids = [v for k, v in corner_map.items() if v is not None]
    # Preserve order while removing duplicates.
    seen = set()
    out = []
    for tag_id in ids:
        if tag_id not in seen:
            seen.add(tag_id)
            out.append(tag_id)
    return out


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_float(v, default: float = float("nan")) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def safe_int(v, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


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


def save_high_quality_jpeg(img: Image.Image, path: Path, quality: int = 95) -> None:
    ensure_dir(path.parent)
    # quality=95 + subsampling=0 keeps this sprint focused on spatial sampling
    # instead of compression artifacts. HEIC/JPEG compression is the next sprint.
    img.convert("RGB").save(path, format="JPEG", quality=quality, subsampling=0, optimize=False)


# -----------------------------------------------------------------------------
# Analyzer import and tag geometry helpers
# -----------------------------------------------------------------------------


def import_quality_module(quality_script: Path):
    if not quality_script.exists():
        raise FileNotFoundError(f"Quality script not found: {quality_script}")
    spec = importlib.util.spec_from_file_location("bm_reference_card_quality_v2_imported", quality_script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import quality script from {quality_script}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses in imported analyzer expect the module to be present in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def detect_tag_geometry(
    image_path: Path,
    quality_module,
    corner_map: Dict[str, Optional[int]],
    tag_family: str,
    scales: Sequence[float],
) -> Dict[str, object]:
    """Run the same detector used by the analyzer and return lightweight geometry.

    This is used for auto-centered cropping and for derived fields such as
    approximate card width in output pixels. It intentionally does not replace
    bm_reference_card_quality_v2.py's scoring.
    """
    img = quality_module.load_image_bgr(image_path)
    tag_metrics, corners_by_id, best_scale, rejected_count = quality_module.detect_tags(
        img, tag_family, scales
    )
    ids = required_tag_ids(corner_map)
    centers: Dict[int, Tuple[float, float]] = {}
    for tag_id in ids:
        pts = corners_by_id.get(tag_id)
        if pts is not None:
            ctr = np.asarray(pts, dtype=np.float32).reshape(4, 2).mean(axis=0)
            centers[int(tag_id)] = (float(ctr[0]), float(ctr[1]))

    # Approximate fiducial/card width between left and right tag centers.
    approx_card_width_px = float("nan")
    try:
        tl_id, tr_id, bl_id, br_id = corner_map.get("tl"), corner_map.get("tr"), corner_map.get("bl"), corner_map.get("br")
        widths = []
        if tl_id in centers and tr_id in centers:
            widths.append(math.dist(centers[tl_id], centers[tr_id]))
        if bl_id in centers and br_id in centers:
            widths.append(math.dist(centers[bl_id], centers[br_id]))
        if widths:
            approx_card_width_px = float(sum(widths) / len(widths))
    except Exception:
        approx_card_width_px = float("nan")

    return {
        "detected_ids": sorted(int(tm.tag_id) for tm in tag_metrics),
        "centers": centers,
        "best_scale": best_scale,
        "rejected_count": rejected_count,
        "approx_pixels_per_card_width": approx_card_width_px,
    }


# -----------------------------------------------------------------------------
# Image generation steps
# -----------------------------------------------------------------------------


def validate_crop_fits(crop: Tuple[int, int, int, int], image_size: Tuple[int, int]) -> None:
    x, y, w, h = crop
    img_w, img_h = image_size
    if x < 0 or y < 0 or x + w > img_w or y + h > img_h:
        raise ValueError(
            f"Crop {crop} does not fit image size {img_w}x{img_h}. "
            "Adjust --fixed-crop or provide the expected native full image."
        )


def crop_image(source_img: Image.Image, crop: Tuple[int, int, int, int]) -> Image.Image:
    x, y, w, h = crop
    return source_img.crop((x, y, x + w, y + h))


def clamp_centered_crop(
    center_x: float,
    center_y: float,
    crop_w: int,
    crop_h: int,
    image_w: int,
    image_h: int,
) -> Tuple[int, int, int, int]:
    if crop_w > image_w or crop_h > image_h:
        raise ValueError(f"Centered crop {crop_w}x{crop_h} does not fit native image {image_w}x{image_h}")
    x = int(round(center_x - crop_w / 2.0))
    y = int(round(center_y - crop_h / 2.0))
    x = max(0, min(x, image_w - crop_w))
    y = max(0, min(y, image_h - crop_h))
    return x, y, crop_w, crop_h


def make_crops(
    source_path: Path,
    out_dir: Path,
    fixed_crop: Tuple[int, int, int, int],
    include_auto: bool,
    quality_module,
    corner_map: Dict[str, Optional[int]],
    tag_family: str,
    scales: Sequence[float],
    jpeg_quality: int,
    manifest: Dict[str, object],
) -> Dict[str, CropInfo]:
    source_img = Image.open(source_path).convert("RGB")
    img_w, img_h = source_img.size
    warnings = manifest.setdefault("warnings", [])
    if (img_w, img_h) != EXPECTED_NATIVE_SIZE:
        warnings.append(
            f"Input image is {img_w}x{img_h}; expected {EXPECTED_NATIVE_SIZE[0]}x{EXPECTED_NATIVE_SIZE[1]}. Continuing because crop fits."
        )
    validate_crop_fits(fixed_crop, (img_w, img_h))

    roi_dir = ensure_dir(out_dir / "roi")
    crops: Dict[str, CropInfo] = {}

    fixed_img = crop_image(source_img, fixed_crop)
    fixed_path = roi_dir / "fixed_3072x1728.jpg"
    save_high_quality_jpeg(fixed_img, fixed_path, jpeg_quality)
    x, y, w, h = fixed_crop
    crops["fixed"] = CropInfo("fixed", x, y, w, h, str(fixed_path))

    if include_auto:
        try:
            geom = detect_tag_geometry(source_path, quality_module, corner_map, tag_family, scales)
            ids = set(int(x) for x in geom["detected_ids"])  # type: ignore[index]
            req = set(required_tag_ids(corner_map))
            missing = sorted(req - ids)
            if missing:
                manifest["auto_crop"] = {
                    "status": "skipped",
                    "reason": f"Native full image missing required tag IDs: {missing}",
                    "detected_ids": sorted(ids),
                }
            else:
                centers = geom["centers"]  # type: ignore[assignment]
                cx = float(np.mean([centers[tag_id][0] for tag_id in req]))  # type: ignore[index]
                cy = float(np.mean([centers[tag_id][1] for tag_id in req]))  # type: ignore[index]
                auto_crop = clamp_centered_crop(cx, cy, BASE_WIDTH, BASE_HEIGHT, img_w, img_h)
                auto_img = crop_image(source_img, auto_crop)
                auto_path = roi_dir / "auto_3072x1728.jpg"
                save_high_quality_jpeg(auto_img, auto_path, jpeg_quality)
                ax, ay, aw, ah = auto_crop
                crops["auto"] = CropInfo(
                    "auto",
                    ax,
                    ay,
                    aw,
                    ah,
                    str(auto_path),
                    auto_center_x=round(cx, 3),
                    auto_center_y=round(cy, 3),
                    auto_status="created",
                )
                manifest["auto_crop"] = {
                    "status": "created",
                    "center_x": round(cx, 3),
                    "center_y": round(cy, 3),
                    "crop": {"x": ax, "y": ay, "w": aw, "h": ah},
                    "detected_ids": sorted(ids),
                }
        except Exception as exc:
            manifest["auto_crop"] = {
                "status": "skipped",
                "reason": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }

    return crops


def make_downsample_ladder(
    out_dir: Path,
    crops: Dict[str, CropInfo],
    ladder: Sequence[Tuple[int, int]],
    jpeg_quality: int,
) -> Dict[str, List[DownsampleInfo]]:
    result: Dict[str, List[DownsampleInfo]] = {}
    downsampled_root = ensure_dir(out_dir / "downsampled")
    for mode, crop_info in crops.items():
        mode_dir = ensure_dir(downsampled_root / mode)
        roi_img = Image.open(crop_info.roi_path).convert("RGB")
        rows: List[DownsampleInfo] = []
        for w, h in ladder:
            out_path = mode_dir / f"{mode}_{w}x{h}.jpg"
            if (w, h) == roi_img.size:
                out_img = roi_img.copy()
            else:
                out_img = roi_img.resize((w, h), Image.Resampling.LANCZOS)
            save_high_quality_jpeg(out_img, out_path, jpeg_quality)
            size_bytes = out_path.stat().st_size
            rows.append(
                DownsampleInfo(
                    crop_mode=mode,
                    output_width=w,
                    output_height=h,
                    source_roi_x=crop_info.source_roi_x,
                    source_roi_y=crop_info.source_roi_y,
                    source_roi_w=crop_info.source_roi_w,
                    source_roi_h=crop_info.source_roi_h,
                    image_path=str(out_path),
                    image_size_bytes=size_bytes,
                    image_size_kb=round(size_bytes / 1024.0, 3),
                    relative_output_scale_vs_3072=round(w / float(BASE_WIDTH), 6),
                    output_pixels_total=w * h,
                )
            )
        result[mode] = rows
    return result


# -----------------------------------------------------------------------------
# Analyzer subprocess and metrics merge
# -----------------------------------------------------------------------------


def run_quality_analyzer(
    mode: str,
    downsample_dir: Path,
    quality_dir: Path,
    quality_script: Path,
    corner_map: str,
    tag_family: str,
    scales: Sequence[float],
) -> Path:
    ensure_dir(quality_dir)
    ref_path = downsample_dir / f"{mode}_{BASE_WIDTH}x{BASE_HEIGHT}.jpg"
    if not ref_path.exists():
        raise FileNotFoundError(f"Mode reference image not found: {ref_path}")

    cmd = [
        sys.executable,
        str(quality_script),
        "--input",
        str(downsample_dir),
        "--output",
        str(quality_dir),
        "--corner-map",
        corner_map,
        "--tag-family",
        tag_family,
        "--reference",
        str(ref_path),
        "--scales",
        *[str(x) for x in scales],
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    (quality_dir / "analyzer_command.txt").write_text(" ".join(cmd) + "\n", encoding="utf-8")
    (quality_dir / "analyzer_stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (quality_dir / "analyzer_stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(
            f"Quality analyzer failed for mode={mode} with exit {proc.returncode}. "
            f"See {quality_dir / 'analyzer_stderr.log'}"
        )
    csv_path = quality_dir / "reference_card_quality_results.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Analyzer did not create expected CSV: {csv_path}")
    return csv_path


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: Path, rows: List[Dict[str, object]], preferred_fields: Sequence[str]) -> None:
    ensure_dir(path.parent)
    all_fields: List[str] = []
    for f in preferred_fields:
        if f not in all_fields:
            all_fields.append(f)
    for r in rows:
        for f in r.keys():
            if f not in all_fields:
                all_fields.append(f)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def parse_tag_ids(tag_ids_text: object) -> List[int]:
    if tag_ids_text is None:
        return []
    out = []
    for part in str(tag_ids_text).replace(",", " ").split():
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def sprint02_status(row: Dict[str, object], required_ids: Sequence[int]) -> Tuple[str, str]:
    detected = set(parse_tag_ids(row.get("tag_ids")))
    missing = sorted(set(required_ids) - detected)
    min_side = safe_float(row.get("tag_side_px_min"), default=0.0)
    if missing:
        return "FAIL", f"missing required tag IDs {missing}"
    if min_side < 10.0:
        return "FAIL", f"min tag side {min_side:.3f}px < 10px"
    if min_side < 18.0:
        return "WARN", f"all required tags detected; min tag side {min_side:.3f}px is 10-18px"
    return "PASS", f"all required tags detected; min tag side {min_side:.3f}px >= 18px"


def compare_full_roi_to_reference(test_path: Path, ref_path: Path) -> Dict[str, float]:
    import cv2

    ref_img = Image.open(ref_path).convert("RGB")
    test_img = Image.open(test_path).convert("RGB")
    if test_img.size != ref_img.size:
        test_img = test_img.resize(ref_img.size, Image.Resampling.LANCZOS)
    ref = np.asarray(ref_img, dtype=np.float32)
    tst = np.asarray(test_img, dtype=np.float32)
    mse = float(np.mean((ref - tst) ** 2))
    psnr = 99.0 if mse <= 1e-9 else float(20.0 * math.log10(255.0 / math.sqrt(mse)))
    ref_g = cv2.cvtColor(ref.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    tst_g = cv2.cvtColor(tst.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    ref_lap = cv2.Laplacian(ref_g, cv2.CV_32F).reshape(-1)
    tst_lap = cv2.Laplacian(tst_g, cv2.CV_32F).reshape(-1)
    denom = float(np.linalg.norm(ref_lap) * np.linalg.norm(tst_lap))
    corr = float(np.dot(ref_lap, tst_lap) / denom) if denom > 1e-9 else 0.0
    return {
        "ref_roi_mse_rgb": round(mse, 4),
        "ref_roi_psnr_rgb": round(psnr, 4),
        "ref_roi_laplacian_corr": round(corr, 6),
    }


def geometry_derived_fields(
    image_path: Path,
    quality_module,
    corner_map: Dict[str, Optional[int]],
    tag_family: str,
    scales: Sequence[float],
) -> Dict[str, object]:
    try:
        geom = detect_tag_geometry(image_path, quality_module, corner_map, tag_family, scales)
        approx_card = geom.get("approx_pixels_per_card_width", float("nan"))
        if isinstance(approx_card, float) and math.isnan(approx_card):
            approx_card_out: object = ""
        else:
            approx_card_out = round(float(approx_card), 3)
        return {"approx_pixels_per_card_width": approx_card_out}
    except Exception as exc:
        return {"approx_pixels_per_card_width": "", "geometry_derived_error": f"{type(exc).__name__}: {exc}"}


def merge_mode_results(
    mode: str,
    analyzer_csv: Path,
    downsample_infos: List[DownsampleInfo],
    output_dir: Path,
    quality_module,
    corner_map_dict: Dict[str, Optional[int]],
    tag_family: str,
    scales: Sequence[float],
) -> List[Dict[str, object]]:
    analyzer_rows = read_csv_rows(analyzer_csv)
    by_name = {Path(str(r.get("source_name", ""))).name: r for r in analyzer_rows}
    required_ids = required_tag_ids(corner_map_dict)
    ref_path = Path(downsample_infos[0].image_path)
    rows: List[Dict[str, object]] = []

    for info in downsample_infos:
        info_dict = asdict(info)
        image_name = Path(info.image_path).name
        analyzer = dict(by_name.get(image_name, {}))
        if not analyzer:
            analyzer = {
                "source_name": image_name,
                "quality_status": "ERROR",
                "error": "Missing from analyzer CSV",
            }

        analyzer_status = analyzer.get("quality_status", "")
        # Wrapper metadata wins where names overlap.
        merged: Dict[str, object] = {}
        merged.update(analyzer)
        merged["analyzer_quality_status"] = analyzer_status
        merged.update(info_dict)
        merged["source_name"] = image_name
        merged["image_path"] = str(Path(info.image_path).resolve())
        merged["image_size_bytes"] = Path(info.image_path).stat().st_size
        merged["image_size_kb"] = round(Path(info.image_path).stat().st_size / 1024.0, 3)
        merged["approx_pixels_per_tag_side"] = merged.get("tag_side_px_min", "")
        merged.update(compare_full_roi_to_reference(Path(info.image_path), ref_path))
        merged.update(geometry_derived_fields(Path(info.image_path), quality_module, corner_map_dict, tag_family, scales))
        status, reason = sprint02_status(merged, required_ids)
        merged["quality_status"] = status
        merged["status_reason"] = reason
        merged["required_tag_ids"] = " ".join(str(x) for x in required_ids)
        rows.append(merged)
    return rows


# -----------------------------------------------------------------------------
# Threshold summary
# -----------------------------------------------------------------------------


def resolution_label(row: Dict[str, object]) -> str:
    return f"{safe_int(row.get('output_width'))}x{safe_int(row.get('output_height'))}"


def status_rank(status: str) -> int:
    return {"PASS": 0, "WARN": 1, "FAIL": 2}.get(status, 3)


def summarize_threshold(mode: str, rows: List[Dict[str, object]]) -> Dict[str, object]:
    # Rows are expected high-to-low resolution, but sort defensively.
    rows = sorted(rows, key=lambda r: safe_int(r.get("output_pixels_total")), reverse=True)
    statuses = [str(r.get("quality_status", "")) for r in rows]
    highest_resolution = resolution_label(rows[0]) if rows else ""

    lowest_pass_idx: Optional[int] = None
    first_warn_idx: Optional[int] = None
    first_fail_idx: Optional[int] = None
    for i, r in enumerate(rows):
        st = str(r.get("quality_status", ""))
        if st == "PASS":
            lowest_pass_idx = i
        elif st == "WARN" and first_warn_idx is None:
            first_warn_idx = i
        elif st == "FAIL" and first_fail_idx is None:
            first_fail_idx = i

    notes: List[str] = []
    # Non-monotonic = any improvement after degradation as resolution decreases.
    ranks = [status_rank(s) for s in statuses]
    non_monotonic = any(ranks[i] < max(ranks[:i]) for i in range(1, len(ranks)))
    if non_monotonic:
        notes.append("non-monotonic detection sequence; use conservative recommendation")

    lowest_pass_resolution = resolution_label(rows[lowest_pass_idx]) if lowest_pass_idx is not None else ""
    first_warn_resolution = resolution_label(rows[first_warn_idx]) if first_warn_idx is not None else ""
    first_fail_resolution = resolution_label(rows[first_fail_idx]) if first_fail_idx is not None else ""

    recommended = ""
    if lowest_pass_idx is None:
        recommended = "NO_PASS"
        notes.append("no resolution met Sprint 02 PASS threshold")
    else:
        recommended_idx = lowest_pass_idx
        # User-selected margin logic: if PASS is immediately followed by WARN/FAIL at
        # the next smaller image, recommend one step higher.
        next_idx = lowest_pass_idx + 1
        if next_idx < len(rows) and str(rows[next_idx].get("quality_status")) in {"WARN", "FAIL"}:
            if lowest_pass_idx > 0:
                recommended_idx = lowest_pass_idx - 1
                notes.append("lowest PASS is immediately followed by WARN/FAIL; recommended one step higher for margin")
            else:
                notes.append("highest resolution is the only PASS; no higher margin step available")
        if non_monotonic:
            # Conservative fallback: use the row before the first degradation, if possible.
            degrade_idx = None
            for i, rank in enumerate(ranks):
                if rank > 0:
                    degrade_idx = i
                    break
            if degrade_idx is not None and degrade_idx > 0:
                recommended_idx = min(recommended_idx, degrade_idx - 1)
        recommended = resolution_label(rows[recommended_idx])

    return {
        "crop_mode": mode,
        "highest_resolution": highest_resolution,
        "lowest_pass_resolution": lowest_pass_resolution,
        "first_warn_resolution": first_warn_resolution,
        "first_fail_resolution": first_fail_resolution,
        "recommended_min_resolution": recommended,
        "notes": "; ".join(notes),
        "status_sequence_high_to_low": " ".join(f"{resolution_label(r)}:{r.get('quality_status')}" for r in rows),
    }


# -----------------------------------------------------------------------------
# Cut sheet rendering
# -----------------------------------------------------------------------------


def status_text(row: Dict[str, object]) -> str:
    return str(row.get("quality_status", "")) or "UNKNOWN"


def open_or_placeholder(path: Optional[Path], size: Tuple[int, int], text: str) -> Image.Image:
    if path and path.exists():
        try:
            return Image.open(path).convert("RGB")
        except Exception:
            pass
    img = Image.new("RGB", size, (235, 238, 242))
    d = ImageDraw.Draw(img)
    f = pil_font(18, True)
    small = pil_font(13)
    d.text((18, 18), "No image", font=f, fill=(80, 90, 105))
    d.text((18, 48), text[:80], font=small, fill=(80, 90, 105))
    return img


def make_same_roi_sheet(
    mode: str,
    rows: List[Dict[str, object]],
    cut_dir: Path,
    prefix: str,
) -> Path:
    rows = sorted(rows, key=lambda r: safe_int(r.get("output_pixels_total")), reverse=True)
    cols = 3
    margin = 28
    display_w, display_h = 420, 236
    label_h = 122
    tile_w, tile_h = display_w + 24, display_h + label_h
    title_h = 92
    sheet_w = margin * 2 + cols * tile_w
    sheet_h = margin * 2 + title_h + math.ceil(len(rows) / cols) * tile_h
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    f_title = pil_font(28, True)
    f_sub = pil_font(15)
    f_label = pil_font(13)
    d.text((margin, margin), f"{prefix}: same-ROI normalized display — {mode}", font=f_title, fill=(30, 45, 60))
    d.text(
        (margin, margin + 38),
        "Every tile shows the full selected ROI at the same display size. Lower-resolution images are upsampled for visual comparison. Not a 1:1 sheet.",
        font=f_sub,
        fill=(90, 95, 105),
    )
    for i, r in enumerate(rows):
        x = margin + (i % cols) * tile_w
        y = margin + title_h + (i // cols) * tile_h
        d.rectangle((x, y, x + tile_w - 10, y + tile_h - 10), fill=(255, 255, 255), outline=(205, 212, 220))
        img = open_or_placeholder(Path(str(r.get("image_path", ""))), (display_w, display_h), status_text(r))
        img = img.resize((display_w, display_h), Image.Resampling.LANCZOS)
        sheet.paste(img, (x + 8, y + 8))
        lines = [
            f"{mode} {safe_int(r.get('output_width'))}x{safe_int(r.get('output_height'))}",
            f"JPEG {safe_float(r.get('image_size_kb'), 0):.1f} KB | {status_text(r)}",
            f"tags={r.get('tag_count','')} ids={r.get('tag_ids','')}",
            f"min_tag_px={r.get('tag_side_px_min','')} | PSNR_ROI={r.get('ref_roi_psnr_rgb','')}",
            str(r.get("status_reason", ""))[:58],
        ]
        ly = y + display_h + 18
        for j, line in enumerate(lines):
            d.text((x + 12, ly + j * 21), line, font=f_label, fill=(45, 55, 70))
    out = cut_dir / f"{prefix}_same_roi_normalized_display_{mode}.jpg"
    sheet.save(out, quality=94)
    return out


def make_reference_card_sheet(
    mode: str,
    rows: List[Dict[str, object]],
    quality_mode_dir: Path,
    cut_dir: Path,
    prefix: str,
) -> Path:
    rows = sorted(rows, key=lambda r: safe_int(r.get("output_pixels_total")), reverse=True)
    rect_dir = quality_mode_dir / "rectified_cards"
    annotated_dir = quality_mode_dir / "annotated"
    cols = 3
    margin = 28
    display_w, display_h = 420, 176
    label_h = 154
    tile_w, tile_h = display_w + 24, display_h + label_h
    title_h = 92
    sheet_w = margin * 2 + cols * tile_w
    sheet_h = margin * 2 + title_h + math.ceil(len(rows) / cols) * tile_h
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    f_title = pil_font(28, True)
    f_sub = pil_font(15)
    f_label = pil_font(13)
    d.text((margin, margin), f"{prefix}: rectified reference-card metrics — {mode}", font=f_title, fill=(30, 45, 60))
    d.text(
        (margin, margin + 38),
        "Rectified card crops are normalized for engineering comparison. Failure tiles show annotated image or placeholder where available.",
        font=f_sub,
        fill=(90, 95, 105),
    )
    for i, r in enumerate(rows):
        x = margin + (i % cols) * tile_w
        y = margin + title_h + (i // cols) * tile_h
        d.rectangle((x, y, x + tile_w - 10, y + tile_h - 10), fill=(255, 255, 255), outline=(205, 212, 220))
        stem = Path(str(r.get("source_name", ""))).stem
        rect_path = rect_dir / f"{stem}_card_rectified.jpg"
        ann_path = annotated_dir / f"{stem}_annotated.jpg"
        img_path = rect_path if rect_path.exists() else ann_path if ann_path.exists() else None
        img = open_or_placeholder(img_path, (display_w, display_h), status_text(r))
        img.thumbnail((display_w, display_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (display_w, display_h), (230, 235, 240))
        canvas.paste(img, ((display_w - img.width) // 2, (display_h - img.height) // 2))
        sheet.paste(canvas, (x + 8, y + 8))
        lines = [
            f"{mode} {safe_int(r.get('output_width'))}x{safe_int(r.get('output_height'))} | {status_text(r)}",
            f"tags={r.get('tag_count','')} ids={r.get('tag_ids','')} min_tag_px={r.get('tag_side_px_min','')}",
            f"tag_sharp={r.get('tag_laplacian_var_mean','')} ten={r.get('tag_tenengrad_mean','')}",
            f"card_sharp={r.get('card_laplacian_var','')} contrast={r.get('card_contrast_p95_p05','')}",
            f"card_PSNR={r.get('ref_psnr_rgb','')} ROI_PSNR={r.get('ref_roi_psnr_rgb','')}",
            f"JPEG {safe_float(r.get('image_size_kb'), 0):.1f} KB",
        ]
        ly = y + display_h + 16
        for j, line in enumerate(lines):
            d.text((x + 12, ly + j * 21), line, font=f_label, fill=(45, 55, 70))
    out = cut_dir / f"{prefix}_reference_card_metrics_{mode}.jpg"
    sheet.save(out, quality=94)
    return out


def make_threshold_summary_sheet(summary_rows: List[Dict[str, object]], cut_dir: Path, prefix: str) -> Path:
    margin = 28
    row_h = 94
    width = 1500
    height = margin * 2 + 105 + max(1, len(summary_rows)) * row_h
    img = Image.new("RGB", (width, height), (245, 247, 250))
    d = ImageDraw.Draw(img)
    f_title = pil_font(30, True)
    f_head = pil_font(15, True)
    f_body = pil_font(14)
    d.text((margin, margin), f"{prefix}: Sprint 02 spatial density threshold summary", font=f_title, fill=(30, 45, 60))
    d.text(
        (margin, margin + 42),
        "Recommendation uses strict all-four-AprilTag PASS/WARN/FAIL and adds one-step margin when lowest PASS is immediately followed by WARN/FAIL.",
        font=f_body,
        fill=(85, 92, 105),
    )
    headers = ["mode", "highest", "lowest PASS", "first WARN", "first FAIL", "recommended", "notes"]
    xs = [margin, 155, 310, 500, 690, 880, 1100]
    y0 = margin + 92
    for x, h in zip(xs, headers):
        d.text((x, y0), h, font=f_head, fill=(30, 45, 60))
    for i, r in enumerate(summary_rows):
        y = y0 + 32 + i * row_h
        d.rectangle((margin - 8, y - 8, width - margin, y + row_h - 14), fill=(255, 255, 255), outline=(205, 212, 220))
        values = [
            r.get("crop_mode", ""),
            r.get("highest_resolution", ""),
            r.get("lowest_pass_resolution", ""),
            r.get("first_warn_resolution", ""),
            r.get("first_fail_resolution", ""),
            r.get("recommended_min_resolution", ""),
            str(r.get("notes", ""))[:78],
        ]
        for x, val in zip(xs, values):
            d.text((x, y), str(val), font=f_body, fill=(45, 55, 70))
        seq = str(r.get("status_sequence_high_to_low", ""))
        d.text((xs[0], y + 34), seq[:170], font=f_body, fill=(90, 95, 105))
    out = cut_dir / f"{prefix}_threshold_summary.jpg"
    img.save(out, quality=94)
    return out


# -----------------------------------------------------------------------------
# Main CLI
# -----------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Sprint 02 spatial density sweep for Nereus/BM reference-card detection."
    )
    ap.add_argument("--input", required=True, help="Native full-resolution JPEG, typically native_full_q95.jpg")
    ap.add_argument("--output", required=True, help="Timestamped output folder")
    ap.add_argument("--fixed-crop", type=parse_crop, default=DEFAULT_FIXED_CROP, help="Native crop x,y,w,h; default 768,432,3072,1728")
    ap.add_argument("--corner-map", default=DEFAULT_CORNER_MAP, help="Corner tag map; default tl:0,tr:1,bl:2,br:3")
    ap.add_argument("--include-auto-centered", action="store_true", help="Also create auto-card-centered 3072x1728 crop if all four tags are detected in native image")
    ap.add_argument("--quality-script", required=True, help="Path to bm_reference_card_quality_v2.py")
    ap.add_argument("--prefix", default="test", help="Filename prefix for cut sheets and run labeling; default test")
    ap.add_argument("--tag-family", default="DICT_APRILTAG_36h11", help="OpenCV ArUco/AprilTag dictionary name")
    ap.add_argument("--scales", nargs="+", type=float, default=DEFAULT_ANALYZER_SCALES, help="Detector scales passed to quality analyzer; default 1 2 3 4. Add 6 8 only if needed for difficult low-res detections.")
    ap.add_argument("--ladder", nargs="+", type=parse_resolution, default=DEFAULT_LADDER, help="Downsample ladder, e.g. 3072x1728 1920x1080")
    ap.add_argument("--jpeg-quality", type=int, default=95, help="JPEG quality for ROI/downsample outputs; default 95")
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()

    input_path = expand_path(args.input)
    output_dir = expand_path(args.output)
    quality_script = expand_path(args.quality_script)
    if not input_path.exists():
        raise SystemExit(f"Input image not found: {input_path}")
    if input_path.suffix.lower() not in IMAGE_EXTS:
        raise SystemExit(f"Input does not look like an image: {input_path}")
    if not quality_script.exists():
        raise SystemExit(f"Quality script not found: {quality_script}")
    if not (1 <= args.jpeg_quality <= 100):
        raise SystemExit("--jpeg-quality must be between 1 and 100")

    ensure_dir(output_dir)
    source_dir = ensure_dir(output_dir / "source")
    results_dir = ensure_dir(output_dir / "results")
    cut_dir = ensure_dir(output_dir / "cut_sheets")
    quality_root = ensure_dir(output_dir / "quality")

    corner_map_dict = parse_corner_map(args.corner_map)
    req_ids = required_tag_ids(corner_map_dict)
    if len(req_ids) < 4:
        print(
            f"WARNING: corner map only contains {len(req_ids)} required tag IDs ({req_ids}). Sprint 02 expects four IDs.",
            file=sys.stderr,
        )

    native_copy = source_dir / "native_full_q95.jpg"
    shutil.copy2(input_path, native_copy)

    manifest: Dict[str, object] = {
        "script": Path(__file__).name,
        "created_utc": utc_stamp(),
        "input_path": str(input_path),
        "source_copy": str(native_copy),
        "output_dir": str(output_dir),
        "fixed_crop": {"x": args.fixed_crop[0], "y": args.fixed_crop[1], "w": args.fixed_crop[2], "h": args.fixed_crop[3]},
        "corner_map": args.corner_map,
        "required_tag_ids": req_ids,
        "include_auto_centered": bool(args.include_auto_centered),
        "downsample_ladder": [f"{w}x{h}" for w, h in args.ladder],
        "downsample_method": "Pillow Image.Resampling.LANCZOS",
        "jpeg_settings": {"quality": args.jpeg_quality, "subsampling": "0 / 4:4:4", "optimize": False},
        "quality_script": str(quality_script),
        "tag_family": args.tag_family,
        "detector_scales": list(args.scales),
        "python": sys.version,
        "platform": platform.platform(),
        "warnings": [],
    }

    print(f"[spatial-sweep] output={output_dir}")
    print(f"[spatial-sweep] importing analyzer={quality_script}")
    quality_module = import_quality_module(quality_script)

    print("[spatial-sweep] creating fixed/auto ROI crops")
    crops = make_crops(
        native_copy,
        output_dir,
        args.fixed_crop,
        args.include_auto_centered,
        quality_module,
        corner_map_dict,
        args.tag_family,
        args.scales,
        args.jpeg_quality,
        manifest,
    )
    manifest["crops"] = {mode: asdict(info) for mode, info in crops.items()}

    print("[spatial-sweep] creating high-quality downsample ladder")
    downsample_infos = make_downsample_ladder(output_dir, crops, args.ladder, args.jpeg_quality)
    manifest["downsampled"] = {
        mode: [asdict(info) for info in infos] for mode, infos in downsample_infos.items()
    }

    all_final_rows: Dict[str, List[Dict[str, object]]] = {}
    for mode, infos in downsample_infos.items():
        print(f"[spatial-sweep] running quality analyzer for mode={mode}")
        mode_downsample_dir = output_dir / "downsampled" / mode
        mode_quality_dir = quality_root / mode
        analyzer_csv = run_quality_analyzer(
            mode,
            mode_downsample_dir,
            mode_quality_dir,
            quality_script,
            args.corner_map,
            args.tag_family,
            args.scales,
        )
        print(f"[spatial-sweep] merging Sprint 02 metadata/status for mode={mode}")
        final_rows = merge_mode_results(
            mode,
            analyzer_csv,
            infos,
            output_dir,
            quality_module,
            corner_map_dict,
            args.tag_family,
            args.scales,
        )
        all_final_rows[mode] = final_rows

    preferred_fields = [
        "crop_mode",
        "output_width",
        "output_height",
        "source_roi_x",
        "source_roi_y",
        "source_roi_w",
        "source_roi_h",
        "image_path",
        "image_size_bytes",
        "image_size_kb",
        "relative_output_scale_vs_3072",
        "output_pixels_total",
        "approx_pixels_per_card_width",
        "approx_pixels_per_tag_side",
        "tag_count",
        "tag_ids",
        "required_tag_ids",
        "tag_side_px_min",
        "tag_side_px_mean",
        "tag_laplacian_var_mean",
        "tag_tenengrad_mean",
        "tag_contrast_mean",
        "fiducial_geometry_residual_px",
        "card_laplacian_var",
        "card_tenengrad",
        "card_contrast_p95_p05",
        "card_clipped_dark_frac",
        "card_clipped_bright_frac",
        "ref_psnr_rgb",
        "ref_laplacian_corr",
        "ref_roi_psnr_rgb",
        "ref_roi_laplacian_corr",
        "analyzer_quality_status",
        "quality_status",
        "status_reason",
    ]

    summary_rows: List[Dict[str, object]] = []
    for mode, rows in all_final_rows.items():
        out_csv = results_dir / f"reference_card_quality_{mode}.csv"
        write_csv_rows(out_csv, rows, preferred_fields)
        summary_rows.append(summarize_threshold(mode, rows))
        print(f"[spatial-sweep] wrote {out_csv}")

    summary_csv = results_dir / "threshold_summary.csv"
    write_csv_rows(
        summary_csv,
        summary_rows,
        [
            "crop_mode",
            "highest_resolution",
            "lowest_pass_resolution",
            "first_warn_resolution",
            "first_fail_resolution",
            "recommended_min_resolution",
            "notes",
            "status_sequence_high_to_low",
        ],
    )
    print(f"[spatial-sweep] wrote {summary_csv}")

    print("[spatial-sweep] building cut sheets")
    cut_outputs = []
    for mode, rows in all_final_rows.items():
        cut_outputs.append(str(make_same_roi_sheet(mode, rows, cut_dir, args.prefix)))
        cut_outputs.append(str(make_reference_card_sheet(mode, rows, quality_root / mode, cut_dir, args.prefix)))
    cut_outputs.append(str(make_threshold_summary_sheet(summary_rows, cut_dir, args.prefix)))
    manifest["cut_sheets"] = cut_outputs
    manifest["results"] = {
        "threshold_summary_csv": str(summary_csv),
        **{f"reference_card_quality_{mode}_csv": str(results_dir / f"reference_card_quality_{mode}.csv") for mode in all_final_rows},
    }

    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[spatial-sweep] wrote {manifest_path}")

    # Terminal summary for fast field decision-making.
    print("\n=== Sprint 02 threshold summary ===")
    for r in summary_rows:
        print(
            f"{r['crop_mode']}: recommended={r['recommended_min_resolution']} | "
            f"lowest_pass={r['lowest_pass_resolution']} | "
            f"first_warn={r['first_warn_resolution']} | first_fail={r['first_fail_resolution']}"
        )
        if r.get("notes"):
            print(f"  notes: {r['notes']}")
    if manifest.get("warnings"):
        print("\nWarnings:")
        for w in manifest["warnings"]:  # type: ignore[index]
            print(f"  - {w}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
