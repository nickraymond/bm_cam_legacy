#!/usr/bin/env python3
"""
BM Reference Card Color Smoke Test

Purpose:
  Local-only Sprint 05 smoke test for the Nereus/Bristlemouth reef reference card.
  Takes transmitted/decoded BM images, detects and rectifies the reference card,
  samples known grayscale/color patches, applies a simple color correction, and
  writes a QA cut sheet showing before/card/after.

Install:
  python3 -m pip install opencv-contrib-python pillow numpy

Optional HEIC input support:
  python3 -m pip install pillow-heif

Example:
  python3 tools/bm_reference_card_color_smoke.py \
    --input-dir ./input_images \
    --output-dir ./color_smoke_output \
    --reference-card ./reference_card_template_v2/reference_card_template_3000x1000.png \
    --template-json ./reference_card_template_v2/template_layout.json \
    --quality-script ./tools/bm_reference_card_quality_v2.py

Notes:
  - This is not final color science. It is an MVP smoke test.
  - Default correction is gray_chroma using mid-gray patches only.
  - The included template layout is provisional and based on the rendered RGB print file.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import shutil
import sys
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except Exception:
    HAS_HEIF = False

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
DEFAULT_CORNER_MAP = "tl:0,tr:1,bl:2,br:3"
DEFAULT_SCALES = [1, 2, 3, 4]
DEFAULT_TEMPLATE_W = 3000
DEFAULT_TEMPLATE_H = 1000
SCRIPT_VERSION = "2026-07-11-sprint05-color-smoke-v2-highres-cutsheets"


# -----------------------------------------------------------------------------
# Basic helpers
# -----------------------------------------------------------------------------


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_name(value: str, max_len: int = 110) -> str:
    text = Path(value).stem.strip().replace(" ", "_").replace(":", "-").replace("/", "-")
    text = "".join(ch for ch in text if ch.isalnum() or ch in "._-=")
    return (text[:max_len] or "image")


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


def str2bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return True
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def round_list(values: Sequence[float], ndigits: int = 6) -> List[float]:
    return [round(float(v), ndigits) for v in values]


# -----------------------------------------------------------------------------
# Image IO
# -----------------------------------------------------------------------------


def load_rgb_pil(path: Path) -> Image.Image:
    suffix = path.suffix.lower()
    if suffix in {".heic", ".heif"} and not HAS_HEIF:
        raise RuntimeError(
            f"HEIC/HEIF input requires pillow-heif. Install with: python3 -m pip install pillow-heif. File: {path}"
        )
    return Image.open(path).convert("RGB")


def load_bgr(path: Path) -> np.ndarray:
    pil = load_rgb_pil(path)
    arr = np.asarray(pil, dtype=np.uint8)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def save_rgb_image(arr_rgb: np.ndarray, path: Path, quality: int = 94) -> None:
    ensure_dir(path.parent)
    arr = np.clip(arr_rgb, 0, 255).astype(np.uint8)
    Image.fromarray(arr, mode="RGB").save(path, quality=quality)


def save_bgr_image(arr_bgr: np.ndarray, path: Path, quality: int = 94) -> None:
    rgb = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2RGB)
    save_rgb_image(rgb, path, quality=quality)


def load_reference_image(path: Path, target_size: Tuple[int, int]) -> Image.Image:
    # Keep the runtime dependency simple: reference-card should normally be PNG/JPG.
    # If a PDF is passed and PyMuPDF is available, render page 1.
    if path.suffix.lower() == ".pdf":
        try:
            import fitz  # type: ignore
        except Exception as exc:
            raise RuntimeError("PDF reference-card input needs PyMuPDF/fitz; use the included PNG instead.") from exc
        doc = fitz.open(str(path))
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        mode = "RGB" if pix.n < 4 else "RGBA"
        img = Image.frombytes(mode, [pix.width, pix.height], pix.samples).convert("RGB")
    else:
        img = Image.open(path).convert("RGB")
    if img.size != target_size:
        img = img.resize(target_size, Image.Resampling.LANCZOS)
    return img


# -----------------------------------------------------------------------------
# Analyzer import / discovery
# -----------------------------------------------------------------------------


def find_default_quality_script(script_path: Path) -> Optional[Path]:
    candidates = [
        script_path.parent / "bm_reference_card_quality_v2.py",
        script_path.parent.parent / "tools" / "bm_reference_card_quality_v2.py",
        script_path.parent.parent / "bm_reference_card_quality_v2.py",
        Path.cwd() / "bm_reference_card_quality_v2.py",
        Path.cwd() / "tools" / "bm_reference_card_quality_v2.py",
    ]
    for p in candidates:
        if p.exists():
            return p.resolve()
    return None


def load_quality_module(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Quality script not found: {path}")
    spec = importlib.util.spec_from_file_location("bm_reference_card_quality_v2_for_color_smoke", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import quality script: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


# -----------------------------------------------------------------------------
# Template / patch layout
# -----------------------------------------------------------------------------


def load_template_layout(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "patches" not in data or not isinstance(data["patches"], list):
        raise ValueError(f"template_layout.json has no patches list: {path}")
    return data


def patch_inner_box(patch: Dict[str, Any], inner_fraction: float = 0.60) -> Tuple[int, int, int, int]:
    x = int(patch["x"]); y = int(patch["y"]); w = int(patch["w"]); h = int(patch["h"])
    inner_fraction = max(0.15, min(1.0, float(inner_fraction)))
    dx = int(round((1.0 - inner_fraction) * w / 2.0))
    dy = int(round((1.0 - inner_fraction) * h / 2.0))
    return x + dx, y + dy, max(1, w - 2 * dx), max(1, h - 2 * dy)


def sample_patch_rgb(warp_rgb: np.ndarray, patch: Dict[str, Any], inner_fraction: float) -> Dict[str, Any]:
    h_img, w_img = warp_rgb.shape[:2]
    x, y, w, h = patch_inner_box(patch, inner_fraction)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w_img, x + w), min(h_img, y + h)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"patch {patch.get('id')} is outside warp bounds")
    crop = warp_rgb[y0:y1, x0:x1].astype(np.float32)
    flat = crop.reshape(-1, 3)
    median = np.median(flat, axis=0)
    mean = np.mean(flat, axis=0)
    low_clip = float(np.mean(flat <= 3.0))
    high_clip = float(np.mean(flat >= 252.0))
    target = np.asarray(patch.get("target_srgb", [np.nan, np.nan, np.nan]), dtype=np.float32)
    err = float(np.sqrt(np.mean((median - target) ** 2))) if np.all(np.isfinite(target)) else float("nan")
    luma = float(0.299 * median[0] + 0.587 * median[1] + 0.114 * median[2])
    channel_spread = float(np.std(median))
    return {
        "id": patch.get("id"),
        "type": patch.get("type", ""),
        "label": patch.get("label", ""),
        "box": [int(patch["x"]), int(patch["y"]), int(patch["w"]), int(patch["h"])],
        "inner_box": [x0, y0, x1 - x0, y1 - y0],
        "target_srgb": [int(round(float(v))) for v in target.tolist()] if np.all(np.isfinite(target)) else [],
        "observed_median_rgb": round_list(median, 3),
        "observed_mean_rgb": round_list(mean, 3),
        "observed_luma": round(luma, 3),
        "channel_spread": round(channel_spread, 3),
        "clip_percent_low": round(low_clip * 100.0, 4),
        "clip_percent_high": round(high_clip * 100.0, 4),
        "rmse_to_target": round(err, 4) if np.isfinite(err) else "",
        "use_for_gray_balance": bool(patch.get("use_for_gray_balance", False)),
    }


def sample_all_patches(warp_rgb: np.ndarray, layout: Dict[str, Any], inner_fraction: float) -> List[Dict[str, Any]]:
    rows = []
    for patch in layout.get("patches", []):
        try:
            rows.append(sample_patch_rgb(warp_rgb, patch, inner_fraction))
        except Exception as exc:
            rows.append({
                "id": patch.get("id"),
                "type": patch.get("type", ""),
                "error": f"{type(exc).__name__}: {exc}",
                "use_for_gray_balance": bool(patch.get("use_for_gray_balance", False)),
            })
    return rows


# -----------------------------------------------------------------------------
# Correction math
# -----------------------------------------------------------------------------


def sample_target(sample: Dict[str, Any]) -> Optional[np.ndarray]:
    raw = sample.get("target_srgb") or []
    if len(raw) != 3:
        return None
    arr = np.asarray(raw, dtype=np.float32)
    if not np.all(np.isfinite(arr)):
        return None
    return arr


def sample_observed(sample: Dict[str, Any], field: str = "observed_median_rgb") -> Optional[np.ndarray]:
    raw = sample.get(field) or []
    if len(raw) != 3:
        return None
    arr = np.asarray(raw, dtype=np.float32)
    if not np.all(np.isfinite(arr)):
        return None
    return arr


def valid_gray_sample(sample: Dict[str, Any]) -> bool:
    if sample.get("type") != "gray" or not bool(sample.get("use_for_gray_balance", False)):
        return False
    obs = sample_observed(sample)
    tgt = sample_target(sample)
    if obs is None or tgt is None:
        return False
    target_luma = float(np.mean(tgt))
    if target_luma < 35 or target_luma > 235:
        return False
    if float(sample.get("clip_percent_low", 0.0)) > 25.0:
        return False
    if float(sample.get("clip_percent_high", 0.0)) > 25.0:
        return False
    if float(np.min(obs)) <= 2.0:
        return False
    return True


def compute_gray_gains(samples: List[Dict[str, Any]], min_gray_patches: int = 3) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    ratios = []
    used_ids = []
    for s in samples:
        if not valid_gray_sample(s):
            continue
        obs = sample_observed(s)
        tgt = sample_target(s)
        if obs is None or tgt is None:
            continue
        target_gray = float(np.mean(tgt))
        ratios.append(target_gray / np.maximum(obs, 1.0))
        used_ids.append(s.get("id"))
    info: Dict[str, Any] = {"method": "gray_balance", "gray_patch_ids_used": used_ids, "patch_count_used": len(used_ids)}
    if len(ratios) < min_gray_patches:
        info["ok"] = False
        info["reason"] = f"not enough valid gray patches: {len(ratios)} < {min_gray_patches}"
        return None, info
    gains = np.median(np.vstack(ratios), axis=0)
    gains = np.clip(gains, 0.10, 8.0)
    info.update({"ok": True, "gains_rgb": round_list(gains, 6), "reason": "ok"})
    return gains.astype(np.float32), info



def compute_gray_chroma_gains(samples: List[Dict[str, Any]], min_gray_patches: int = 3) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """Compute gray-patch chromatic gains while preserving scene brightness.

    This is safer for real reef/room images than absolute gray_balance because it
    does not try to force the photographed card to the template's printed luma.
    It only makes neutral gray patches neutral by scaling R/G/B around each
    patch's own observed mean luminance.
    """
    ratios = []
    used_ids = []
    for s in samples:
        if not valid_gray_sample(s):
            continue
        obs = sample_observed(s)
        if obs is None:
            continue
        observed_gray = float(np.mean(obs))
        if observed_gray < 5.0:
            continue
        ratios.append(observed_gray / np.maximum(obs, 1.0))
        used_ids.append(s.get("id"))
    info: Dict[str, Any] = {"method": "gray_chroma", "gray_patch_ids_used": used_ids, "patch_count_used": len(used_ids)}
    if len(ratios) < min_gray_patches:
        info["ok"] = False
        info["reason"] = f"not enough valid gray patches: {len(ratios)} < {min_gray_patches}"
        return None, info
    gains = np.median(np.vstack(ratios), axis=0)
    # Normalize average gain to 1 so this is chroma correction, not exposure correction.
    mean_gain = float(np.mean(gains))
    if mean_gain > 1e-6:
        gains = gains / mean_gain
    gains = np.clip(gains, 0.35, 2.50)
    info.update({"ok": True, "gains_rgb": round_list(gains, 6), "reason": "ok_preserve_luminance"})
    return gains.astype(np.float32), info


def compute_matrix(samples: List[Dict[str, Any]], min_patches: int = 8) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    obs_rows = []
    tgt_rows = []
    used_ids = []
    for s in samples:
        obs = sample_observed(s)
        tgt = sample_target(s)
        if obs is None or tgt is None:
            continue
        low = float(s.get("clip_percent_low", 0.0)); high = float(s.get("clip_percent_high", 0.0))
        if low > 25.0 or high > 25.0 or float(np.min(obs)) <= 1.0:
            continue
        # Skip pure black; it is not useful for an unconstrained color matrix.
        if float(np.mean(tgt)) < 8.0:
            continue
        obs_rows.append(obs / 255.0)
        tgt_rows.append(tgt / 255.0)
        used_ids.append(s.get("id"))
    info: Dict[str, Any] = {"method": "matrix_3x3", "patch_ids_used": used_ids, "patch_count_used": len(used_ids)}
    if len(obs_rows) < min_patches:
        info["ok"] = False
        info["reason"] = f"not enough valid patches: {len(obs_rows)} < {min_patches}"
        return None, info
    A = np.vstack(obs_rows).astype(np.float32)
    B = np.vstack(tgt_rows).astype(np.float32)
    M, residuals, rank, _s = np.linalg.lstsq(A, B, rcond=None)
    M = np.asarray(M, dtype=np.float32)
    info.update({
        "ok": True,
        "matrix_rgb_3x3": [[round(float(v), 6) for v in row] for row in M.tolist()],
        "rank": int(rank),
        "residuals": round_list(residuals, 6) if residuals.size else [],
        "reason": "ok",
    })
    return M, info


def apply_gray_balance(img_rgb: np.ndarray, gains: np.ndarray) -> np.ndarray:
    arr = img_rgb.astype(np.float32) * gains.reshape(1, 1, 3)
    return np.clip(arr, 0, 255).astype(np.uint8)


def apply_matrix(img_rgb: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    arr = img_rgb.astype(np.float32) / 255.0
    out = arr.reshape(-1, 3) @ matrix
    out = np.clip(out.reshape(arr.shape) * 255.0, 0, 255)
    return out.astype(np.uint8)


def patch_error_summary(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    errs = []
    gray_spreads = []
    used = 0
    for s in samples:
        err = s.get("rmse_to_target")
        try:
            if err != "" and math.isfinite(float(err)):
                errs.append(float(err))
                used += 1
        except Exception:
            pass
        if s.get("type") == "gray" and bool(s.get("use_for_gray_balance", False)):
            try:
                gray_spreads.append(float(s.get("channel_spread")))
            except Exception:
                pass
    return {
        "patch_count_for_error": used,
        "mean_patch_error": round(float(np.mean(errs)), 4) if errs else "",
        "median_patch_error": round(float(np.median(errs)), 4) if errs else "",
        "gray_neutrality": round(float(np.mean(gray_spreads)), 4) if gray_spreads else "",
    }


# -----------------------------------------------------------------------------
# Detection / warp
# -----------------------------------------------------------------------------


def parse_manual_corners(path: Optional[Path], image_name: str) -> Optional[np.ndarray]:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    # Accept either a single object with corners, or a mapping by filename/stem.
    if "corners" in data:
        item = data
    else:
        item = data.get(image_name) or data.get(Path(image_name).stem)
    if not item or "corners" not in item:
        return None
    c = item["corners"]
    pts = [c["top_left"], c["top_right"], c["bottom_right"], c["bottom_left"]]
    return np.asarray(pts, dtype=np.float32)


def draw_manual_polygon(img_rgb: np.ndarray, quad: np.ndarray, out_path: Path) -> None:
    pil = Image.fromarray(img_rgb.astype(np.uint8), mode="RGB")
    d = ImageDraw.Draw(pil)
    pts = [tuple(map(float, p)) for p in np.asarray(quad).reshape(4, 2)]
    d.line(pts + [pts[0]], fill=(255, 0, 0), width=5)
    d.text((pts[0][0], max(0, pts[0][1] - 24)), "manual card corners", fill=(255, 0, 0), font=pil_font(18, True))
    ensure_dir(out_path.parent)
    pil.save(out_path, quality=92)


def detect_card(
    img_bgr: np.ndarray,
    img_rgb: np.ndarray,
    img_name: str,
    analyzer_mod,
    args: argparse.Namespace,
    manual_corners_path: Optional[Path],
    overlay_path: Path,
) -> Dict[str, Any]:
    manual_quad = parse_manual_corners(manual_corners_path, img_name) if manual_corners_path else None
    if manual_quad is not None:
        draw_manual_polygon(img_rgb, manual_quad, overlay_path)
        rect_bgr = analyzer_mod.rectify_quad(img_bgr, manual_quad, args.rectified_width, args.rectified_height)
        return {
            "card_detected": True,
            "detection_method": "manual_corners",
            "tag_count": 0,
            "tag_ids": "",
            "homography_ok": True,
            "corner_status": {},
            "fiducial_geometry_residual_px": "",
            "detector_best_scale": "",
            "rejected_candidates": "",
            "card_quad": manual_quad,
            "warp_bgr": rect_bgr,
            "failure_reason": "",
        }

    corner_map = analyzer_mod.parse_corner_map(args.corner_map)
    tag_metrics, corners_by_id, best_scale, rejected_count = analyzer_mod.detect_tags(
        img_bgr, args.tag_family, args.scales
    )
    fid_quad, corner_status, geom_resid = analyzer_mod.infer_card_corners_from_tags(corners_by_id, corner_map)
    card_quad = None
    rect_bgr = None
    homography_ok = False
    failure_reason = ""
    if fid_quad is not None:
        card_quad = analyzer_mod.expand_quad(fid_quad, args.card_expand_x, args.card_expand_y)
        rect_bgr = analyzer_mod.rectify_quad(img_bgr, card_quad, args.rectified_width, args.rectified_height)
        homography_ok = True
    else:
        failure_reason = "unable to infer card corners from detected AprilTags"

    analyzer_mod.draw_annotation(img_bgr, tag_metrics, corners_by_id, fid_quad, card_quad, overlay_path)
    sides = [float(tm.side_px_min) for tm in tag_metrics]
    tag_ids = " ".join(str(tm.tag_id) for tm in sorted(tag_metrics, key=lambda x: x.tag_id))
    return {
        "card_detected": bool(rect_bgr is not None),
        "detection_method": "apriltag",
        "tag_count": len(tag_metrics),
        "tag_ids": tag_ids,
        "tag_side_px_min": round(float(min(sides)), 3) if sides else "",
        "tag_side_px_mean": round(float(np.mean([tm.side_px_mean for tm in tag_metrics])), 3) if tag_metrics else "",
        "tag_laplacian_var_mean": round(float(np.mean([tm.laplacian_var for tm in tag_metrics])), 4) if tag_metrics else "",
        "tag_contrast_mean": round(float(np.mean([tm.contrast_p95_p05 for tm in tag_metrics])), 4) if tag_metrics else "",
        "homography_ok": homography_ok,
        "corner_status": corner_status,
        "fiducial_geometry_residual_px": round(float(geom_resid), 4) if not math.isnan(float(geom_resid)) else "",
        "detector_best_scale": best_scale,
        "rejected_candidates": rejected_count,
        "card_quad": card_quad,
        "warp_bgr": rect_bgr,
        "failure_reason": failure_reason,
    }


# -----------------------------------------------------------------------------
# QA images / cut sheets
# -----------------------------------------------------------------------------


def thumbnail(img: Image.Image, size: Tuple[int, int], fill=(230, 235, 240), *, no_upscale: bool = True) -> Image.Image:
    """Fit image into a fixed tile.

    no_upscale=True keeps 1:1-ish detail for card crops when the source is
    smaller than the tile; large sheets can pass bigger tile sizes instead of
    forcing the regular overview contact sheet to become huge.
    """
    im = img.convert("RGB").copy()
    scale = min(size[0] / max(1, im.width), size[1] / max(1, im.height))
    if no_upscale:
        scale = min(scale, 1.0)
    new_w = max(1, int(round(im.width * scale)))
    new_h = max(1, int(round(im.height * scale)))
    if (new_w, new_h) != im.size:
        im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, fill)
    canvas.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
    return canvas


def draw_wrapped_text(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str, font, fill, max_chars: int = 92, line_gap: int = 4) -> int:
    x, y = xy
    for raw_line in str(text).split("\n"):
        line = raw_line
        while len(line) > max_chars:
            cut = line.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            draw.text((x, y), line[:cut], font=font, fill=fill)
            y += font.size + line_gap if hasattr(font, "size") else 16
            line = line[cut:].lstrip()
        draw.text((x, y), line, font=font, fill=fill)
        y += font.size + line_gap if hasattr(font, "size") else 16
    return y


def make_patch_debug_image(warp_rgb: np.ndarray, layout: Dict[str, Any], samples: List[Dict[str, Any]], out_path: Path) -> None:
    pil = Image.fromarray(warp_rgb.astype(np.uint8), mode="RGB")
    d = ImageDraw.Draw(pil)
    fs = pil_font(18, True)
    sample_by_id = {s.get("id"): s for s in samples}
    for patch in layout.get("patches", []):
        x, y, w, h = int(patch["x"]), int(patch["y"]), int(patch["w"]), int(patch["h"])
        pid = patch.get("id", "")
        color = (0, 190, 0) if patch.get("type") == "gray" else (0, 120, 255)
        d.rectangle((x, y, x + w, y + h), outline=color, width=3)
        s = sample_by_id.get(pid, {})
        label = f"{pid} {s.get('rmse_to_target','')}"
        d.text((x, max(0, y - 18)), label, font=fs, fill=color)
    ensure_dir(out_path.parent)
    pil.save(out_path, quality=92)


def make_card_comparison(warp_rgb: Optional[np.ndarray], ref_img: Image.Image, out_path: Path) -> None:
    ensure_dir(out_path.parent)
    ref = ref_img.convert("RGB")
    if warp_rgb is None:
        sheet = Image.new("RGB", (ref.width * 2, ref.height), (245, 247, 250))
        sheet.paste(ref, (ref.width, 0))
        d = ImageDraw.Draw(sheet)
        d.text((24, 24), "No detected card warp", font=pil_font(36, True), fill=(160, 60, 60))
        sheet.save(out_path, quality=92)
        return
    warp = Image.fromarray(warp_rgb.astype(np.uint8), mode="RGB").resize(ref.size, Image.Resampling.LANCZOS)
    diff = np.abs(np.asarray(warp, dtype=np.int16) - np.asarray(ref, dtype=np.int16)).astype(np.uint8)
    diff = np.clip(diff.astype(np.float32) * 2.0, 0, 255).astype(np.uint8)
    diff_img = Image.fromarray(diff, mode="RGB")
    w, h = ref.size
    sheet = Image.new("RGB", (w * 3, h + 58), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    f = pil_font(24, True)
    sheet.paste(warp, (0, 58)); sheet.paste(ref, (w, 58)); sheet.paste(diff_img, (2 * w, 58))
    d.text((16, 16), "detected warp", font=f, fill=(30, 50, 70))
    d.text((w + 16, 16), "reference template", font=f, fill=(30, 50, 70))
    d.text((2 * w + 16, 16), "abs diff x2", font=f, fill=(30, 50, 70))
    sheet.save(out_path, quality=92)


def metric_lines(metrics: Dict[str, Any]) -> List[str]:
    keys = [
        "card_detected", "detection_method", "tag_count", "tag_ids", "homography_ok",
        "warp_width_px", "warp_height_px", "sharpness_score", "mean_luma",
        "clip_percent_low", "clip_percent_high", "gray_neutrality_before",
        "gray_neutrality_after", "mean_patch_error_before", "mean_patch_error_after",
        "patch_count_used", "correction_method",
    ]
    out = []
    for k in keys:
        if k in metrics:
            out.append(f"{k}: {metrics[k]}")
    if metrics.get("failure_reason"):
        out.append(f"failure_reason: {metrics.get('failure_reason')}")
    return out


def make_cutsheet(
    image_name: str,
    before_path: Path,
    after_path: Path,
    overlay_path: Path,
    warp_path: Optional[Path],
    reference_path: Path,
    comparison_path: Path,
    metrics: Dict[str, Any],
    out_path: Path,
    *,
    scale: float = 1.0,
    title_suffix: str = "",
) -> None:
    ensure_dir(out_path.parent)
    scale = max(0.5, float(scale))
    W = int(round(1800 * scale))
    margin = int(round(28 * scale))
    title_h = int(round(110 * scale))
    panel_h = int(round(460 * scale))
    mid_h = int(round(430 * scale))
    bottom_h = int(round(500 * scale))
    text_w = int(round(500 * scale))
    sheet_h = margin * 4 + title_h + panel_h + mid_h + bottom_h
    sheet = Image.new("RGB", (W, sheet_h), (244, 247, 251))
    d = ImageDraw.Draw(sheet)
    f_title = pil_font(max(12, int(round(32 * scale))), True)
    f_head = pil_font(max(10, int(round(22 * scale))), True)
    f_small = pil_font(max(8, int(round(15 * scale))))
    f_tiny = pil_font(max(8, int(round(13 * scale))))

    title = "Sprint 05 Local Color Correction Smoke Test" + (f" — {title_suffix}" if title_suffix else "")
    d.text((margin, margin), title, font=f_title, fill=(25, 45, 65))
    d.text((margin, margin + 42), f"image={image_name}", font=f_small, fill=(70, 85, 100))
    d.text((margin, margin + 66), f"generated={iso_utc()}  version={SCRIPT_VERSION}", font=f_tiny, fill=(90, 105, 120))

    # Top: before + overlay + metric text.
    y = margin + title_h
    d.text((margin, y), "BEFORE", font=f_head, fill=(30, 50, 70))
    before = thumbnail(Image.open(before_path), (int(round(590 * scale)), panel_h - int(round(48 * scale))))
    overlay = thumbnail(Image.open(overlay_path), (int(round(590 * scale)), panel_h - int(round(48 * scale)))) if overlay_path.exists() else before.copy()
    sheet.paste(before, (margin, y + int(round(36 * scale))))
    sheet.paste(overlay, (margin + int(round(610 * scale)), y + int(round(36 * scale))))
    d.text((margin + int(round(610 * scale)), y), "Detected card overlay", font=f_head, fill=(30, 50, 70))
    text_x = margin + int(round(1220 * scale))
    d.text((text_x, y), "Metrics", font=f_head, fill=(30, 50, 70))
    ty = y + 36
    for line in metric_lines(metrics):
        ty = draw_wrapped_text(d, (text_x, ty), line, f_small, (45, 60, 75), max_chars=max(48, int(48 * scale)), line_gap=max(3, int(3 * scale)))

    # Middle: warped card/reference/diff.
    y += panel_h + margin
    d.text((margin, y), "REFERENCE-CARD QA", font=f_head, fill=(30, 50, 70))
    if warp_path and warp_path.exists():
        warp_img = Image.open(warp_path).convert("RGB")
    else:
        warp_img = Image.new("RGB", (600, 252), (230, 235, 240))
        ImageDraw.Draw(warp_img).text((28, 28), "No card warp", font=f_head, fill=(160, 60, 60))
    ref_img = Image.open(reference_path).convert("RGB")
    comp_img = Image.open(comparison_path).convert("RGB") if comparison_path.exists() else ref_img.copy()
    sheet.paste(thumbnail(warp_img, (int(round(560 * scale)), mid_h - int(round(52 * scale))), no_upscale=False), (margin, y + int(round(38 * scale))))
    sheet.paste(thumbnail(ref_img, (int(round(560 * scale)), mid_h - int(round(52 * scale))), no_upscale=False), (margin + int(round(580 * scale)), y + int(round(38 * scale))))
    sheet.paste(thumbnail(comp_img, (int(round(610 * scale)), mid_h - int(round(52 * scale))), no_upscale=False), (margin + int(round(1160 * scale)), y + int(round(38 * scale))))
    d.text((margin, y + mid_h - int(round(20 * scale))), "detected warp", font=f_tiny, fill=(80, 90, 100))
    d.text((margin + int(round(580 * scale)), y + mid_h - int(round(20 * scale))), "reference template", font=f_tiny, fill=(80, 90, 100))
    d.text((margin + int(round(1160 * scale)), y + mid_h - int(round(20 * scale))), "warp/reference/diff", font=f_tiny, fill=(80, 90, 100))

    # Bottom: after.
    y += mid_h + margin
    d.text((margin, y), "AFTER COLOR CORRECTION", font=f_head, fill=(30, 50, 70))
    after = thumbnail(Image.open(after_path), (int(round(900 * scale)), bottom_h - int(round(48 * scale))))
    sheet.paste(after, (margin, y + int(round(36 * scale))))
    notes = [
        f"correction_method: {metrics.get('correction_method')}",
        f"gray_neutrality_before/after: {metrics.get('gray_neutrality_before')} -> {metrics.get('gray_neutrality_after')}",
        f"mean_patch_error_before/after: {metrics.get('mean_patch_error_before')} -> {metrics.get('mean_patch_error_after')}",
        f"matrix/gains: {metrics.get('correction_summary_compact', '')}",
        "MVP note: targets are provisional rendered RGB values, not measured printed-card values.",
    ]
    tx = margin + int(round(940 * scale))
    ty = y + int(round(46 * scale))
    for line in notes:
        ty = draw_wrapped_text(d, (tx, ty), line, f_small, (45, 60, 75), max_chars=max(58, int(58 * scale)), line_gap=max(5, int(5 * scale)))

    # PNG keeps small patch/text detail crisper than JPEG. Pillow chooses format
    # from the extension; existing cutsheet.png remains lossless.
    sheet.save(out_path)



def make_card_detail_sheet(
    image_name: str,
    warp_path: Optional[Path],
    patch_debug_path: Optional[Path],
    reference_path: Path,
    comparison_path: Path,
    out_path: Path,
) -> Optional[Path]:
    """Create a high-detail card QA sheet without shrinking the card warp.

    This is the inspection artifact for patch placement, AprilTag sharpness,
    HEIC artifacts, and color patch separability. The regular cut sheet remains
    a quick overview; this sheet is intentionally large.
    """
    if warp_path is None or not warp_path.exists():
        return None
    ensure_dir(out_path.parent)
    warp = Image.open(warp_path).convert("RGB")
    ref = Image.open(reference_path).convert("RGB")
    patch = Image.open(patch_debug_path).convert("RGB") if patch_debug_path and patch_debug_path.exists() else warp.copy()
    comp = Image.open(comparison_path).convert("RGB") if comparison_path.exists() else ref.copy()

    # Match all card panels to the detected warp size for direct inspection.
    if ref.size != warp.size:
        ref = ref.resize(warp.size, Image.Resampling.LANCZOS)
    if patch.size != warp.size:
        patch = patch.resize(warp.size, Image.Resampling.LANCZOS)
    if comp.height != warp.height:
        comp = comp.resize((max(1, int(comp.width * warp.height / comp.height)), warp.height), Image.Resampling.LANCZOS)

    title_h = 92
    gap = 18
    margin = 24
    label_h = 34
    panel_w = warp.width
    panel_h = warp.height
    # comparison may be 3x wide; cap display to one panel-width crop if needed to keep sheet manageable.
    comp_panel = thumbnail(comp, (panel_w, panel_h), no_upscale=False)

    W = margin * 2 + panel_w * 2 + gap
    H = margin * 2 + title_h + (panel_h + label_h) * 2 + gap
    sheet = Image.new("RGB", (W, H), (244, 247, 251))
    d = ImageDraw.Draw(sheet)
    f_title = pil_font(34, True)
    f_head = pil_font(22, True)
    f_small = pil_font(15)
    d.text((margin, margin), "Sprint 05 card detail sheet — near-native patch QA", font=f_title, fill=(25, 45, 65))
    d.text((margin, margin + 42), f"image={image_name}  warp={warp.width}x{warp.height}  generated={iso_utc()}", font=f_small, fill=(70, 85, 100))

    y0 = margin + title_h
    x0 = margin
    panels = [
        ("detected card warp", warp),
        ("patch sampling boxes", patch),
        ("reference template", ref),
        ("warp/reference/diff preview", comp_panel),
    ]
    for i, (label, img) in enumerate(panels):
        col = i % 2
        row = i // 2
        x = x0 + col * (panel_w + gap)
        y = y0 + row * (panel_h + label_h + gap)
        d.rectangle((x - 1, y - 1, x + panel_w + 1, y + panel_h + 1), outline=(205, 215, 225), width=2)
        sheet.paste(thumbnail(img, (panel_w, panel_h), no_upscale=False), (x, y))
        d.text((x, y + panel_h + 8), label, font=f_head, fill=(35, 55, 75))

    sheet.save(out_path)
    return out_path

def make_contact_sheet(summary_rows: List[Dict[str, Any]], output_dir: Path) -> Optional[Path]:
    rows = [r for r in summary_rows if r.get("cutsheet_path")]
    if not rows:
        return None
    thumbs = []
    for r in rows:
        p = Path(str(r["cutsheet_path"]))
        if p.exists():
            thumbs.append((r, Image.open(p).convert("RGB")))
    if not thumbs:
        return None
    cols = 2
    tile_w, tile_h = 720, 560
    margin = 24
    header_h = 80
    rows_n = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (margin * 2 + cols * tile_w, margin * 2 + header_h + rows_n * tile_h), (244, 247, 251))
    d = ImageDraw.Draw(sheet)
    d.text((margin, margin), "Sprint 05 color smoke contact sheet", font=pil_font(30, True), fill=(30, 50, 70))
    d.text((margin, margin + 38), f"images={len(thumbs)} generated={iso_utc()}", font=pil_font(14), fill=(80, 95, 110))
    for i, (r, img) in enumerate(thumbs):
        x = margin + (i % cols) * tile_w
        y = margin + header_h + (i // cols) * tile_h
        d.rectangle((x, y, x + tile_w - 12, y + tile_h - 10), fill=(255, 255, 255), outline=(205, 215, 225))
        sheet.paste(thumbnail(img, (tile_w - 24, tile_h - 74)), (x + 6, y + 8))
        label = f"{r.get('image')} | {r.get('quality_status')} | {r.get('correction_method')}"
        d.text((x + 12, y + tile_h - 56), label[:90], font=pil_font(14, True), fill=(40, 60, 80))
        d.text((x + 12, y + tile_h - 34), f"gray {r.get('gray_neutrality_before')} -> {r.get('gray_neutrality_after')}  patch error {r.get('mean_patch_error_before')} -> {r.get('mean_patch_error_after')}", font=pil_font(12), fill=(80, 95, 110))
    out = ensure_dir(output_dir / "cutsheets") / "color_correction_contact_sheet.jpg"
    sheet.save(out, quality=92)
    return out


# -----------------------------------------------------------------------------
# Main image processing
# -----------------------------------------------------------------------------


@dataclass
class ProcessResult:
    row: Dict[str, Any]
    metrics: Dict[str, Any]


def build_quality_status(metrics: Dict[str, Any]) -> str:
    if metrics.get("error"):
        return "ERROR"
    if not metrics.get("card_detected"):
        return "FAIL_NO_CARD"
    if metrics.get("correction_method") in {"none_failed_patch_validation", "none_failed_matrix_validation"}:
        return "WARN_NO_CORRECTION"
    if metrics.get("correction_method") == "none_requested":
        return "PASS_NO_CORRECTION_REQUESTED"
    return "PASS"


def process_one_image(
    img_path: Path,
    output_dir: Path,
    reference_img: Image.Image,
    reference_path: Path,
    layout: Dict[str, Any],
    analyzer_mod,
    args: argparse.Namespace,
) -> ProcessResult:
    stem = safe_name(img_path.name)
    image_out = ensure_dir(output_dir / stem)
    before_path = image_out / "before.jpg"
    after_path = image_out / "after_color_corrected.jpg"
    overlay_path = image_out / "detected_card_overlay.jpg"
    warp_path = image_out / "detected_card_warp.jpg"
    patch_debug_path = image_out / "detected_card_patch_overlay.jpg"
    ref_resized_path = image_out / "reference_card_template_resized.jpg"
    comparison_path = image_out / "reference_card_comparison.jpg"
    patch_samples_path = image_out / "patch_samples.json"
    correction_path = image_out / "color_correction_matrix.json"
    metrics_path = image_out / "metrics.json"
    cutsheet_path = image_out / "cutsheet.png"
    cutsheet_highres_path = image_out / "cutsheet_highres.png"
    card_detail_path = image_out / "card_patch_detail_1to1.png"

    metrics: Dict[str, Any] = {
        "image": img_path.name,
        "source_path": str(img_path),
        "output_folder": str(image_out),
        "script_version": SCRIPT_VERSION,
        "generated_at_utc": iso_utc(),
        "correction_method_requested": args.method,
    }
    try:
        pil = load_rgb_pil(img_path)
        img_rgb = np.asarray(pil, dtype=np.uint8)
        img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        pil.save(before_path, quality=94)
        metrics.update({
            "image_width": int(pil.width),
            "image_height": int(pil.height),
            "image_size_kb": round(img_path.stat().st_size / 1024.0, 3),
        })

        detect = detect_card(
            img_bgr, img_rgb, img_path.name, analyzer_mod, args,
            Path(args.manual_card_corners).expanduser().resolve() if args.manual_card_corners else None,
            overlay_path,
        )
        warp_bgr = detect.pop("warp_bgr")
        card_quad = detect.pop("card_quad")
        metrics.update({k: json_safe(v) for k, v in detect.items()})
        metrics["card_quad"] = json_safe(card_quad) if card_quad is not None else ""

        reference_img.save(ref_resized_path, quality=94)
        reference_rgb = np.asarray(reference_img, dtype=np.uint8)

        if warp_bgr is None:
            metrics.update({
                "warp_width_px": "",
                "warp_height_px": "",
                "sharpness_score": "",
                "mean_luma": "",
                "clip_percent_low": "",
                "clip_percent_high": "",
                "patch_count_total": len(layout.get("patches", [])),
                "patch_count_used": 0,
                "gray_neutrality_before": "",
                "gray_neutrality_after": "",
                "mean_patch_error_before": "",
                "mean_patch_error_after": "",
                "correction_method": "none_no_card",
                "correction_summary_compact": "no card warp",
            })
            pil.save(after_path, quality=94)
            make_card_comparison(None, reference_img, comparison_path)
            patch_samples_path.write_text(json.dumps([], indent=2), encoding="utf-8")
            correction_path.write_text(json.dumps({"ok": False, "reason": metrics.get("failure_reason", "no card")}, indent=2), encoding="utf-8")
            make_cutsheet(img_path.name, before_path, after_path, overlay_path, None, ref_resized_path, comparison_path, metrics, cutsheet_path)
            if args.make_highres_cutsheets:
                make_cutsheet(img_path.name, before_path, after_path, overlay_path, None, ref_resized_path, comparison_path, metrics, cutsheet_highres_path, scale=args.cutsheet_scale, title_suffix="high-res")
        else:
            warp_rgb = cv2.cvtColor(warp_bgr, cv2.COLOR_BGR2RGB)
            save_rgb_image(warp_rgb, warp_path, quality=94)
            gray = cv2.cvtColor(warp_bgr, cv2.COLOR_BGR2GRAY)
            metrics.update({
                "warp_width_px": int(warp_rgb.shape[1]),
                "warp_height_px": int(warp_rgb.shape[0]),
                "sharpness_score": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 4),
                "mean_luma": round(float(np.mean(gray)), 4),
                "clip_percent_low": round(float(np.mean(gray <= 3)) * 100.0, 4),
                "clip_percent_high": round(float(np.mean(gray >= 252)) * 100.0, 4),
            })

            samples_before = sample_all_patches(warp_rgb, layout, args.patch_inner_fraction)
            before_summary = patch_error_summary(samples_before)
            metrics.update({
                "patch_count_total": len(layout.get("patches", [])),
                "gray_neutrality_before": before_summary.get("gray_neutrality", ""),
                "mean_patch_error_before": before_summary.get("mean_patch_error", ""),
                "median_patch_error_before": before_summary.get("median_patch_error", ""),
            })
            make_patch_debug_image(warp_rgb, layout, samples_before, patch_debug_path)
            make_card_comparison(warp_rgb, reference_img, comparison_path)

            correction: Dict[str, Any] = {"ok": False, "method_requested": args.method}
            if args.method == "none":
                after_rgb = img_rgb.copy()
                correction.update({"ok": True, "method": "none_requested", "reason": "correction disabled by CLI"})
            elif args.method == "matrix":
                M, matrix_info = compute_matrix(samples_before, min_patches=args.min_matrix_patches)
                correction.update(matrix_info)
                if M is None:
                    after_rgb = img_rgb.copy()
                    correction["method"] = "none_failed_matrix_validation"
                else:
                    after_rgb = apply_matrix(img_rgb, M)
            elif args.method == "gray_balance":
                gains, gain_info = compute_gray_gains(samples_before, min_gray_patches=args.min_gray_patches)
                correction.update(gain_info)
                if gains is None:
                    after_rgb = img_rgb.copy()
                    correction["method"] = "none_failed_patch_validation"
                else:
                    after_rgb = apply_gray_balance(img_rgb, gains)
            else:
                gains, gain_info = compute_gray_chroma_gains(samples_before, min_gray_patches=args.min_gray_patches)
                correction.update(gain_info)
                if gains is None:
                    after_rgb = img_rgb.copy()
                    correction["method"] = "none_failed_patch_validation"
                else:
                    after_rgb = apply_gray_balance(img_rgb, gains)

            save_rgb_image(after_rgb, after_path, quality=94)

            # Rewarp corrected image using the same card quad for after metrics.
            after_bgr = cv2.cvtColor(after_rgb, cv2.COLOR_RGB2BGR)
            after_warp_bgr = analyzer_mod.rectify_quad(after_bgr, card_quad, args.rectified_width, args.rectified_height)
            after_warp_rgb = cv2.cvtColor(after_warp_bgr, cv2.COLOR_BGR2RGB)
            samples_after = sample_all_patches(after_warp_rgb, layout, args.patch_inner_fraction)
            after_summary = patch_error_summary(samples_after)

            metrics.update({
                "patch_count_used": correction.get("patch_count_used", 0),
                "gray_neutrality_after": after_summary.get("gray_neutrality", ""),
                "mean_patch_error_after": after_summary.get("mean_patch_error", ""),
                "median_patch_error_after": after_summary.get("median_patch_error", ""),
                "correction_method": correction.get("method", args.method),
                "correction_summary_compact": "gains=" + str(correction.get("gains_rgb", "")) if "gains_rgb" in correction else "matrix_3x3" if "matrix_rgb_3x3" in correction else str(correction.get("reason", "")),
            })

            patch_payload = {
                "before": samples_before,
                "after": samples_after,
                "layout_template_name": layout.get("template_name", ""),
            }
            patch_samples_path.write_text(json.dumps(json_safe(patch_payload), indent=2, sort_keys=True), encoding="utf-8")
            correction_path.write_text(json.dumps(json_safe(correction), indent=2, sort_keys=True), encoding="utf-8")
            make_cutsheet(img_path.name, before_path, after_path, overlay_path, warp_path, ref_resized_path, comparison_path, metrics, cutsheet_path)
            if args.make_highres_cutsheets:
                make_cutsheet(img_path.name, before_path, after_path, overlay_path, warp_path, ref_resized_path, comparison_path, metrics, cutsheet_highres_path, scale=args.cutsheet_scale, title_suffix="high-res")
            if args.make_card_detail_sheet:
                make_card_detail_sheet(img_path.name, warp_path, patch_debug_path, ref_resized_path, comparison_path, card_detail_path)

        metrics["quality_status"] = build_quality_status(metrics)
        metrics["paths"] = {
            "before": str(before_path),
            "after": str(after_path),
            "overlay": str(overlay_path),
            "warp": str(warp_path) if warp_path.exists() else "",
            "reference": str(ref_resized_path),
            "comparison": str(comparison_path),
            "patch_samples": str(patch_samples_path),
            "correction": str(correction_path),
            "cutsheet": str(cutsheet_path),
            "cutsheet_highres": str(cutsheet_highres_path) if cutsheet_highres_path.exists() else "",
            "card_detail_1to1": str(card_detail_path) if card_detail_path.exists() else "",
        }
        metrics_path.write_text(json.dumps(json_safe(metrics), indent=2, sort_keys=True), encoding="utf-8")

    except Exception as exc:
        metrics.update({
            "quality_status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": "\n".join(traceback.format_exc().splitlines()[-18:]),
            "correction_method": "error",
        })
        ensure_dir(image_out)
        metrics_path.write_text(json.dumps(json_safe(metrics), indent=2, sort_keys=True), encoding="utf-8")

    row = {
        "image": img_path.name,
        "quality_status": metrics.get("quality_status", ""),
        "card_detected": metrics.get("card_detected", ""),
        "detection_method": metrics.get("detection_method", ""),
        "tag_count": metrics.get("tag_count", ""),
        "tag_ids": metrics.get("tag_ids", ""),
        "homography_ok": metrics.get("homography_ok", ""),
        "warp_width_px": metrics.get("warp_width_px", ""),
        "warp_height_px": metrics.get("warp_height_px", ""),
        "sharpness_score": metrics.get("sharpness_score", ""),
        "mean_luma": metrics.get("mean_luma", ""),
        "clip_percent_low": metrics.get("clip_percent_low", ""),
        "clip_percent_high": metrics.get("clip_percent_high", ""),
        "patch_count_total": metrics.get("patch_count_total", ""),
        "patch_count_used": metrics.get("patch_count_used", ""),
        "gray_neutrality_before": metrics.get("gray_neutrality_before", ""),
        "gray_neutrality_after": metrics.get("gray_neutrality_after", ""),
        "mean_patch_error_before": metrics.get("mean_patch_error_before", ""),
        "mean_patch_error_after": metrics.get("mean_patch_error_after", ""),
        "correction_method": metrics.get("correction_method", ""),
        "failure_reason": metrics.get("failure_reason", ""),
        "error": metrics.get("error", ""),
        "output_folder": str(image_out),
        "cutsheet_path": str(cutsheet_path) if cutsheet_path.exists() else "",
        "cutsheet_highres_path": str(cutsheet_highres_path) if cutsheet_highres_path.exists() else "",
        "card_detail_path": str(card_detail_path) if card_detail_path.exists() else "",
        "metrics_path": str(metrics_path),
    }
    return ProcessResult(row=row, metrics=metrics)


# -----------------------------------------------------------------------------
# File discovery / summaries
# -----------------------------------------------------------------------------


def collect_images(input_dir: Path, globs: Sequence[str], max_images: Optional[int]) -> List[Path]:
    out: List[Path] = []
    seen = set()
    for pattern in globs:
        for p in sorted(input_dir.glob(pattern)):
            if not p.is_file():
                continue
            if p.suffix.lower() not in IMAGE_EXTS:
                continue
            # Avoid obvious template/reference files when user points at a mixed folder.
            lower = p.name.lower()
            if "reference_card_template" in lower or lower == "reference_card_high_res.png":
                continue
            rp = p.resolve()
            if rp not in seen:
                out.append(rp)
                seen.add(rp)
    if max_images is not None and max_images > 0:
        out = out[:max_images]
    return out


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    fields: List[str] = []
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def make_pdf_from_cutsheets(rows: List[Dict[str, Any]], out_path: Path) -> Optional[Path]:
    imgs: List[Image.Image] = []
    for r in rows:
        p = Path(str(r.get("cutsheet_path", "")))
        if p.exists():
            imgs.append(Image.open(p).convert("RGB"))
    if not imgs:
        return None
    ensure_dir(out_path.parent)
    first, rest = imgs[0], imgs[1:]
    first.save(out_path, save_all=True, append_images=rest)
    return out_path


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> int:
    script_path = Path(__file__).resolve()
    default_template_dir = script_path.parent / "reference_card_template_v2"
    default_reference = default_template_dir / "reference_card_template_3000x1000.png"
    default_layout = default_template_dir / "template_layout.json"
    default_quality = find_default_quality_script(script_path)

    ap = argparse.ArgumentParser(description="Sprint 05 local reference-card color correction smoke test")
    ap.add_argument("--input-dir", required=True, help="Folder containing BM images to process")
    ap.add_argument("--output-dir", required=True, help="Output folder")
    ap.add_argument("--reference-card", default=str(default_reference), help="Reference card template image/PNG. PDF accepted if PyMuPDF is installed.")
    ap.add_argument("--template-json", default=str(default_layout), help="Patch layout JSON")
    ap.add_argument("--quality-script", default=str(default_quality or ""), help="Path to bm_reference_card_quality_v2.py")
    ap.add_argument("--image-glob", action="append", default=None, help="Input glob. Can repeat. Default: common image extensions in top-level folder.")
    ap.add_argument("--max-images", type=int, default=20)
    ap.add_argument("--tag-family", default="DICT_APRILTAG_36h11")
    ap.add_argument("--scales", nargs="+", type=float, default=DEFAULT_SCALES)
    ap.add_argument("--corner-map", default=DEFAULT_CORNER_MAP)
    ap.add_argument("--rectified-width", type=int, default=DEFAULT_TEMPLATE_W)
    ap.add_argument("--rectified-height", type=int, default=DEFAULT_TEMPLATE_H)
    ap.add_argument("--card-expand-x", type=float, default=1.25)
    ap.add_argument("--card-expand-y", type=float, default=2.0)
    ap.add_argument("--patch-inner-fraction", type=float, default=0.60)
    ap.add_argument("--method", choices=["gray_chroma", "gray_balance", "matrix", "none"], default="gray_chroma")
    ap.add_argument("--min-gray-patches", type=int, default=3)
    ap.add_argument("--min-matrix-patches", type=int, default=8)
    ap.add_argument("--manual-card-corners", default="", help="Optional fallback JSON with card corners in source image coordinates")
    ap.add_argument("--make-pdf", nargs="?", const=True, default=False, type=str2bool)
    ap.add_argument("--make-highres-cutsheets", nargs="?", const=True, default=True, type=str2bool, help="Also write per-image cutsheet_highres.png. Default: true.")
    ap.add_argument("--cutsheet-scale", type=float, default=2.0, help="Scale factor for cutsheet_highres.png. Default: 2.0")
    ap.add_argument("--make-card-detail-sheet", nargs="?", const=True, default=True, type=str2bool, help="Also write card_patch_detail_1to1.png with large card panels. Default: true.")
    args = ap.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = ensure_dir(Path(args.output_dir).expanduser().resolve())
    reference_path = Path(args.reference_card).expanduser().resolve()
    layout_path = Path(args.template_json).expanduser().resolve()
    quality_path = Path(args.quality_script).expanduser().resolve() if args.quality_script else None

    if not input_dir.exists():
        raise FileNotFoundError(f"input-dir not found: {input_dir}")
    if not reference_path.exists():
        raise FileNotFoundError(f"reference-card not found: {reference_path}")
    if not layout_path.exists():
        raise FileNotFoundError(f"template-json not found: {layout_path}")
    if quality_path is None or not quality_path.exists():
        raise FileNotFoundError("bm_reference_card_quality_v2.py not found; pass --quality-script")

    analyzer_mod = load_quality_module(quality_path)
    layout = load_template_layout(layout_path)
    template_w = int(layout.get("template_width_px", args.rectified_width))
    template_h = int(layout.get("template_height_px", args.rectified_height))
    if (args.rectified_width, args.rectified_height) != (template_w, template_h):
        print(f"[COLOR_SMOKE] WARN: overriding rectified size to template layout {template_w}x{template_h}")
        args.rectified_width = template_w
        args.rectified_height = template_h

    reference_img = load_reference_image(reference_path, (args.rectified_width, args.rectified_height))

    if args.image_glob:
        globs = args.image_glob
    else:
        globs = ["*.jpg", "*.jpeg", "*.png", "*.tif", "*.tiff", "*.webp", "*.heic", "*.heif"]
    images = collect_images(input_dir, globs, args.max_images)
    if not images:
        raise SystemExit(f"No images found in {input_dir}")

    # Copy key inputs into the run folder for self-contained review.
    inputs_dir = ensure_dir(output_dir / "inputs")
    try:
        shutil.copy2(reference_path, inputs_dir / reference_path.name)
        shutil.copy2(layout_path, inputs_dir / layout_path.name)
        shutil.copy2(quality_path, inputs_dir / quality_path.name)
    except Exception:
        pass

    manifest = {
        "script_version": SCRIPT_VERSION,
        "generated_at_utc": iso_utc(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "reference_card": str(reference_path),
        "template_json": str(layout_path),
        "quality_script": str(quality_path),
        "image_count": len(images),
        "images": [str(p) for p in images],
        "corner_map": args.corner_map,
        "tag_family": args.tag_family,
        "scales": args.scales,
        "rectified_width": args.rectified_width,
        "rectified_height": args.rectified_height,
        "method": args.method,
        "make_highres_cutsheets": args.make_highres_cutsheets,
        "cutsheet_scale": args.cutsheet_scale,
        "make_card_detail_sheet": args.make_card_detail_sheet,
        "heif_support": HAS_HEIF,
        "notes": [
            "Local-only smoke test; no Pi runtime, BM serial, or backend changes.",
            "Targets are provisional rendered RGB values from the reference-card artwork.",
        ],
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(json_safe(manifest), indent=2, sort_keys=True), encoding="utf-8")

    print("============================================================")
    print("BM REFERENCE CARD COLOR SMOKE TEST")
    print("============================================================")
    print(f"input_dir={input_dir}")
    print(f"output_dir={output_dir}")
    print(f"images={len(images)}")
    print(f"reference_card={reference_path}")
    print(f"template_json={layout_path}")
    print(f"quality_script={quality_path}")
    print(f"method={args.method}")
    print("============================================================")

    results: List[ProcessResult] = []
    for i, img_path in enumerate(images, 1):
        print(f"[COLOR_SMOKE] {i}/{len(images)} {img_path.name}")
        res = process_one_image(img_path, output_dir, reference_img, reference_path, layout, analyzer_mod, args)
        results.append(res)
        print(f"[COLOR_SMOKE] status={res.row.get('quality_status')} correction={res.row.get('correction_method')} cutsheet={res.row.get('cutsheet_path')}")

    summary_rows = [r.row for r in results]
    write_csv(output_dir / "summary.csv", summary_rows)
    (output_dir / "summary.json").write_text(json.dumps(json_safe(summary_rows), indent=2, sort_keys=True), encoding="utf-8")
    contact = make_contact_sheet(summary_rows, output_dir)
    if args.make_pdf:
        pdf_path = make_pdf_from_cutsheets(summary_rows, output_dir / "cutsheets" / "color_correction_contact_sheet.pdf")
    else:
        pdf_path = None

    print("============================================================")
    print("DONE")
    print(f"summary_csv={output_dir / 'summary.csv'}")
    print(f"summary_json={output_dir / 'summary.json'}")
    if contact:
        print(f"contact_sheet={contact}")
    if pdf_path:
        print(f"pdf={pdf_path}")
    print("============================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
