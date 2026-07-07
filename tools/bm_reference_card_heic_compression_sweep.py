#!/usr/bin/env python3
"""
BM Reference Card HEIC Compression Sweep

Mac-side Sprint 03 wrapper for testing HEIC compression after the Sprint 02
spatial-density sweep identified the useful resolution band.

This script:
  1. Consumes the selected high-quality downsampled JPEGs from a Sprint 02
     spatial sweep output directory.
  2. Encodes each selected resolution to HEIC across a quality ladder.
  3. Decodes each HEIC back to PNG for AprilTag/card analysis.
  4. Runs bm_reference_card_quality_v2.py as the existing metrics engine.
  5. Applies strict all-required-tag PASS/WARN/FAIL logic.
  6. Adds HEIC payload size and cellular message-count estimates for 300-byte
     and 900-byte chunks.
  7. Writes per-mode CSVs, threshold summaries, run_manifest.json, and cut sheets.

Default input expectation:
  <spatial_sweep>/downsampled/fixed/fixed_3072x1728.jpg
  <spatial_sweep>/downsampled/fixed/fixed_2688x1512.jpg
  ...
  <spatial_sweep>/downsampled/auto/auto_3072x1728.jpg  # optional

Default resolutions:
  3072x1728 2688x1512 2304x1296 1920x1080 1600x900

Default HEIC qualities:
  10 20 30 40 50 60 70 80 90

Default message chunks:
  300 900

Important:
  Message counts are payload-only estimates: ceil(HEIC file bytes / chunk size).
  They do not include Bristlemouth/Sofar framing, headers, retransmits, queue
  metadata, or protocol overhead.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import re
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


DEFAULT_CORNER_MAP = "tl:0,tr:1,bl:2,br:3"
DEFAULT_RESOLUTIONS = [
    (3072, 1728),
    (2688, 1512),
    (2304, 1296),
    (1920, 1080),
    (1600, 900),
]
DEFAULT_QUALITIES = list(range(10, 100, 10))
DEFAULT_CHUNK_SIZES = [300, 900]
DEFAULT_ANALYZER_SCALES = [1, 2, 3, 4]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class SourceImageInfo:
    crop_mode: str
    output_width: int
    output_height: int
    resolution: str
    source_path: str
    local_source_path: str
    source_size_bytes: int
    source_size_kb: float


@dataclass
class HeicEncodeInfo:
    crop_mode: str
    output_width: int
    output_height: int
    resolution: str
    heic_quality: int
    source_path: str
    source_size_bytes: int
    heic_path: str
    heic_size_bytes: int
    heic_size_kb: float
    decoded_path: str
    codec_backend: str
    compression_ratio_vs_source_jpeg: float


# -----------------------------------------------------------------------------
# Basic helpers
# -----------------------------------------------------------------------------


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def expand_path(p: str) -> Path:
    return Path(p).expanduser().resolve()


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


def parse_resolution(s: str) -> Tuple[int, int]:
    parts = s.lower().replace("×", "x").split("x")
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
    ids = [v for v in corner_map.values() if v is not None]
    seen = set()
    out = []
    for tag_id in ids:
        if tag_id not in seen:
            seen.add(tag_id)
            out.append(tag_id)
    return out


def parse_tag_ids(s: str) -> List[int]:
    if not s:
        return []
    out = []
    for part in re.split(r"[\s,;]+", str(s).strip()):
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


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


def write_csv(path: Path, rows: List[Dict[str, object]], preferred_fields: Optional[List[str]] = None) -> None:
    ensure_dir(path.parent)
    fields: List[str] = []
    if preferred_fields:
        fields.extend(preferred_fields)
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def run_cmd(cmd: Sequence[str], *, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "Command failed with exit code "
            f"{proc.returncode}: {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


# -----------------------------------------------------------------------------
# Source discovery
# -----------------------------------------------------------------------------


def find_source_image(spatial_sweep: Path, crop_mode: str, resolution: Tuple[int, int]) -> Optional[Path]:
    w, h = resolution
    mode_dir = spatial_sweep / "downsampled" / crop_mode
    exact_names = [
        mode_dir / f"{crop_mode}_{w}x{h}.jpg",
        mode_dir / f"{crop_mode}_{w}x{h}.jpeg",
        mode_dir / f"{crop_mode}_{w}x{h}.png",
    ]
    for p in exact_names:
        if p.exists():
            return p.resolve()
    if mode_dir.exists():
        matches = []
        for p in mode_dir.glob(f"*_{w}x{h}.*"):
            if p.suffix.lower() in IMAGE_EXTS and p.is_file():
                matches.append(p)
        if matches:
            return sorted(matches)[0].resolve()
    return None


def collect_sources(
    spatial_sweep: Path,
    out_dir: Path,
    crop_modes: Sequence[str],
    resolutions: Sequence[Tuple[int, int]],
) -> Tuple[List[SourceImageInfo], List[str]]:
    source_infos: List[SourceImageInfo] = []
    warnings: List[str] = []
    for mode in crop_modes:
        mode_dir = spatial_sweep / "downsampled" / mode
        if not mode_dir.exists():
            warnings.append(f"Skipping crop_mode={mode!r}; missing directory: {mode_dir}")
            continue
        for res in resolutions:
            w, h = res
            src = find_source_image(spatial_sweep, mode, res)
            if src is None:
                warnings.append(f"Missing source image for crop_mode={mode} resolution={w}x{h}")
                continue
            local = ensure_dir(out_dir / "source_inputs" / mode) / f"{mode}_{w}x{h}{src.suffix.lower()}"
            shutil.copy2(src, local)
            size = local.stat().st_size
            source_infos.append(SourceImageInfo(
                crop_mode=mode,
                output_width=w,
                output_height=h,
                resolution=f"{w}x{h}",
                source_path=str(src),
                local_source_path=str(local),
                source_size_bytes=size,
                source_size_kb=round(size / 1024.0, 3),
            ))
    return source_infos, warnings


# -----------------------------------------------------------------------------
# HEIC encode/decode
# -----------------------------------------------------------------------------


def choose_codec_backend(requested: str) -> str:
    if requested != "auto":
        if requested == "sips" and not command_exists("sips"):
            raise RuntimeError("Requested --codec-backend sips, but the 'sips' command is not available.")
        if requested == "magick" and not command_exists("magick"):
            raise RuntimeError("Requested --codec-backend magick, but the 'magick' command is not available.")
        return requested

    if command_exists("sips"):
        return "sips"
    if command_exists("magick"):
        return "magick"
    raise RuntimeError(
        "No HEIC encoder found. On macOS, the built-in 'sips' command is usually available. "
        "Alternatively install ImageMagick with HEIC/libheif support and rerun with --codec-backend magick."
    )


def encode_heic_sips(source: Path, out_heic: Path, quality: int) -> None:
    ensure_dir(out_heic.parent)
    # Apple sips accepts formatOptions for quality. Prefer 'heic'; try 'heif' as a fallback
    # because supported format names can vary across macOS versions.
    errors = []
    for fmt in ("heic", "heif"):
        cmd = ["sips", "-s", "format", fmt, "-s", "formatOptions", str(quality), str(source), "--out", str(out_heic)]
        proc = run_cmd(cmd, check=False)
        if proc.returncode == 0 and out_heic.exists() and out_heic.stat().st_size > 0:
            return
        errors.append(f"fmt={fmt} rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    raise RuntimeError("sips HEIC encode failed:\n" + "\n---\n".join(errors))


def decode_heic_sips(source_heic: Path, out_png: Path) -> None:
    ensure_dir(out_png.parent)
    cmd = ["sips", "-s", "format", "png", str(source_heic), "--out", str(out_png)]
    run_cmd(cmd, check=True)
    if not out_png.exists() or out_png.stat().st_size <= 0:
        raise RuntimeError(f"sips decode did not create output PNG: {out_png}")


def encode_heic_magick(source: Path, out_heic: Path, quality: int) -> None:
    ensure_dir(out_heic.parent)
    cmd = ["magick", str(source), "-quality", str(quality), str(out_heic)]
    run_cmd(cmd, check=True)
    if not out_heic.exists() or out_heic.stat().st_size <= 0:
        raise RuntimeError(f"ImageMagick encode did not create output HEIC: {out_heic}")


def decode_heic_magick(source_heic: Path, out_png: Path) -> None:
    ensure_dir(out_png.parent)
    cmd = ["magick", str(source_heic), str(out_png)]
    run_cmd(cmd, check=True)
    if not out_png.exists() or out_png.stat().st_size <= 0:
        raise RuntimeError(f"ImageMagick decode did not create output PNG: {out_png}")


def encode_decode_one(source: SourceImageInfo, out_dir: Path, quality: int, backend: str) -> HeicEncodeInfo:
    mode = source.crop_mode
    res = source.resolution
    stem = f"{mode}_{res}_heic_q{quality:02d}"
    heic_path = ensure_dir(out_dir / "heic" / mode / res) / f"{stem}.heic"
    decoded_path = ensure_dir(out_dir / "decoded" / mode) / f"{stem}_decoded.png"
    source_path = Path(source.local_source_path)

    if backend == "sips":
        encode_heic_sips(source_path, heic_path, quality)
        decode_heic_sips(heic_path, decoded_path)
    elif backend == "magick":
        encode_heic_magick(source_path, heic_path, quality)
        decode_heic_magick(heic_path, decoded_path)
    else:
        raise ValueError(f"Unsupported codec backend: {backend}")

    heic_size = heic_path.stat().st_size
    ratio = heic_size / max(1, source.source_size_bytes)
    return HeicEncodeInfo(
        crop_mode=mode,
        output_width=source.output_width,
        output_height=source.output_height,
        resolution=res,
        heic_quality=quality,
        source_path=str(source_path),
        source_size_bytes=source.source_size_bytes,
        heic_path=str(heic_path),
        heic_size_bytes=heic_size,
        heic_size_kb=round(heic_size / 1024.0, 3),
        decoded_path=str(decoded_path),
        codec_backend=backend,
        compression_ratio_vs_source_jpeg=round(ratio, 6),
    )


# -----------------------------------------------------------------------------
# Image comparison helpers
# -----------------------------------------------------------------------------


def load_rgb_array(path: Path, size: Optional[Tuple[int, int]] = None) -> np.ndarray:
    with Image.open(path) as im:
        im = im.convert("RGB")
        if size is not None and im.size != size:
            im = im.resize(size, Image.Resampling.BICUBIC)
        return np.asarray(im, dtype=np.float32)


def psnr_rgb(test_path: Path, ref_path: Path) -> float:
    with Image.open(ref_path) as ref_im:
        ref_size = ref_im.size
    ref = load_rgb_array(ref_path)
    test = load_rgb_array(test_path, ref_size)
    mse = float(np.mean((ref - test) ** 2))
    if mse <= 1e-9:
        return 99.0
    return float(20 * math.log10(255.0 / math.sqrt(mse)))


def laplacian_corr(test_path: Path, ref_path: Path) -> float:
    with Image.open(ref_path) as ref_im:
        ref_size = ref_im.size
    ref = load_rgb_array(ref_path)
    test = load_rgb_array(test_path, ref_size)
    ref_g = (0.299 * ref[:, :, 0] + 0.587 * ref[:, :, 1] + 0.114 * ref[:, :, 2]).astype(np.float32)
    test_g = (0.299 * test[:, :, 0] + 0.587 * test[:, :, 1] + 0.114 * test[:, :, 2]).astype(np.float32)

    def lap(g: np.ndarray) -> np.ndarray:
        out = np.zeros_like(g, dtype=np.float32)
        out[1:-1, 1:-1] = (
            g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4.0 * g[1:-1, 1:-1]
        )
        return out.reshape(-1)

    a = lap(ref_g)
    b = lap(test_g)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom > 1e-9 else 0.0


# -----------------------------------------------------------------------------
# Analyzer and Sprint status
# -----------------------------------------------------------------------------


def run_quality_analyzer(
    quality_script: Path,
    decoded_dir: Path,
    quality_out: Path,
    corner_map: str,
    tag_family: str,
    scales: Sequence[float],
    reference: Optional[Path],
) -> Path:
    ensure_dir(quality_out)
    cmd = [
        sys.executable,
        str(quality_script),
        "--input",
        str(decoded_dir),
        "--output",
        str(quality_out),
        "--corner-map",
        corner_map,
        "--tag-family",
        tag_family,
        "--scales",
    ]
    cmd.extend(str(x) for x in scales)
    if reference is not None and reference.exists():
        cmd.extend(["--reference", str(reference)])
    run_cmd(cmd, check=True)
    csv_path = quality_out / "reference_card_quality_results.csv"
    if not csv_path.exists():
        raise RuntimeError(f"Quality analyzer did not create expected CSV: {csv_path}")
    return csv_path


def sprint_status(row: Dict[str, object], req_ids: Sequence[int]) -> Tuple[str, str]:
    detected = set(parse_tag_ids(str(row.get("tag_ids", ""))))
    missing = [x for x in req_ids if x not in detected]
    min_side = safe_float(row.get("tag_side_px_min", ""), 0.0)
    if missing:
        return "FAIL", "missing required tag IDs: " + " ".join(str(x) for x in missing)
    if min_side < 10.0:
        return "FAIL", f"all tags detected but min tag side {min_side:.3f}px < 10px"
    if min_side < 18.0:
        return "WARN", f"all tags detected but min tag side {min_side:.3f}px is 10-18px"
    return "PASS", f"all required tags detected and min tag side {min_side:.3f}px >= 18px"


def parse_decoded_name(path_or_name: str) -> Optional[Tuple[str, int, int, int]]:
    name = Path(path_or_name).name
    m = re.match(r"^(?P<mode>.+)_(?P<w>\d+)x(?P<h>\d+)_heic_q(?P<q>\d+)_decoded\.(png|jpg|jpeg)$", name, re.IGNORECASE)
    if not m:
        return None
    return m.group("mode"), int(m.group("w")), int(m.group("h")), int(m.group("q"))


def merge_results(
    analyzer_csv: Path,
    encode_infos: List[HeicEncodeInfo],
    sources_by_key: Dict[Tuple[str, int, int], SourceImageInfo],
    req_ids: Sequence[int],
    chunk_sizes: Sequence[int],
) -> List[Dict[str, object]]:
    rows = read_csv_rows(analyzer_csv)
    encode_by_decoded = {Path(e.decoded_path).name: e for e in encode_infos}
    merged: List[Dict[str, object]] = []

    for r in rows:
        decoded_name = Path(r.get("source_path") or r.get("source_name") or "").name
        enc = encode_by_decoded.get(decoded_name)
        parsed = parse_decoded_name(decoded_name)
        if enc is None and parsed is not None:
            mode, w, h, q = parsed
            enc = next((x for x in encode_infos if x.crop_mode == mode and x.output_width == w and x.output_height == h and x.heic_quality == q), None)
        if enc is None:
            # Keep unknown analyzer rows, but mark them so the user can diagnose path/name issues.
            rr: Dict[str, object] = dict(r)
            rr["quality_status"] = "ERROR"
            rr["status_reason"] = "could not match analyzer row to HEIC encode metadata"
            merged.append(rr)
            continue

        source_key = (enc.crop_mode, enc.output_width, enc.output_height)
        src = sources_by_key[source_key]
        status, reason = sprint_status(r, req_ids)

        source_same_res = Path(src.local_source_path)
        decoded_path = Path(enc.decoded_path)
        same_res_psnr = float("nan")
        same_res_lap_corr = float("nan")
        try:
            same_res_psnr = psnr_rgb(decoded_path, source_same_res)
            same_res_lap_corr = laplacian_corr(decoded_path, source_same_res)
        except Exception:
            pass

        rr = dict(r)
        rr["analyzer_quality_status"] = r.get("quality_status", "")
        rr["quality_status"] = status
        rr["status_reason"] = reason
        rr.update(asdict(enc))
        rr["source_size_kb"] = round(enc.source_size_bytes / 1024.0, 3)
        rr["output_pixels_total"] = enc.output_width * enc.output_height
        rr["megapixels"] = round((enc.output_width * enc.output_height) / 1_000_000.0, 6)
        rr["heic_bytes_per_megapixel"] = round(enc.heic_size_bytes / max(1.0, (enc.output_width * enc.output_height) / 1_000_000.0), 3)
        rr["decoded_analysis_path"] = enc.decoded_path
        rr["source_same_resolution_path"] = src.local_source_path
        rr["same_resolution_ref_psnr_rgb"] = round(same_res_psnr, 4) if not math.isnan(same_res_psnr) else ""
        rr["same_resolution_ref_laplacian_corr"] = round(same_res_lap_corr, 6) if not math.isnan(same_res_lap_corr) else ""
        for chunk in chunk_sizes:
            rr[f"messages_{chunk}b"] = int(math.ceil(enc.heic_size_bytes / float(chunk)))
        # Preserve explicit common message fields even if user changes chunk list.
        if 300 not in chunk_sizes:
            rr["messages_300b"] = int(math.ceil(enc.heic_size_bytes / 300.0))
        if 900 not in chunk_sizes:
            rr["messages_900b"] = int(math.ceil(enc.heic_size_bytes / 900.0))
        merged.append(rr)
    return merged


# -----------------------------------------------------------------------------
# Threshold summaries
# -----------------------------------------------------------------------------


def quality_order(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted(rows, key=lambda r: safe_int(r.get("heic_quality"), 0))


def summarize_one_resolution(crop_mode: str, resolution: str, rows: List[Dict[str, object]]) -> Dict[str, object]:
    rows = quality_order(rows)
    statuses = [(safe_int(r.get("heic_quality")), str(r.get("quality_status", ""))) for r in rows]
    pass_rows = [r for r in rows if r.get("quality_status") == "PASS"]
    warn_rows = [r for r in rows if r.get("quality_status") == "WARN"]
    fail_rows = [r for r in rows if r.get("quality_status") == "FAIL"]

    lowest_pass = min((safe_int(r.get("heic_quality")) for r in pass_rows), default="")
    first_warn = min((safe_int(r.get("heic_quality")) for r in warn_rows), default="")
    first_fail = min((safe_int(r.get("heic_quality")) for r in fail_rows), default="")
    highest_quality = max((safe_int(r.get("heic_quality")) for r in rows), default="")

    notes: List[str] = []
    recommended_quality: object = ""
    recommended_row: Optional[Dict[str, object]] = None
    if not pass_rows:
        notes.append("no PASS at any tested HEIC quality")
    else:
        pass_qualities = sorted(safe_int(r.get("heic_quality")) for r in pass_rows)
        lowest = pass_qualities[0]
        lower_tested = [q for q, _ in statuses if q < lowest]
        # Margin rule: if the lowest PASS is immediately above WARN/FAIL at the next lower quality,
        # recommend one quality step higher when available.
        q_values = sorted(q for q, _ in statuses)
        idx = q_values.index(lowest)
        recommend = lowest
        if idx > 0:
            next_lower_q = q_values[idx - 1]
            next_lower_status = next((s for q, s in statuses if q == next_lower_q), "")
            if next_lower_status in {"WARN", "FAIL", "ERROR"}:
                higher_passes = [q for q in pass_qualities if q > lowest]
                if higher_passes:
                    recommend = higher_passes[0]
                    notes.append("lowest PASS is immediately above WARN/FAIL; recommended one HEIC step higher for margin")
                else:
                    notes.append("lowest PASS is immediately above WARN/FAIL but no higher PASS was available")
        recommended_quality = recommend
        recommended_row = next((r for r in rows if safe_int(r.get("heic_quality")) == recommend), None)

        # Non-monotonic check: as quality increases, status should not get worse.
        rank = {"FAIL": 0, "WARN": 1, "PASS": 2, "ERROR": -1}
        ranks = [rank.get(s, -1) for _, s in statuses]
        if any(ranks[i] > ranks[i + 1] for i in range(len(ranks) - 1)):
            notes.append("non-monotonic detection across HEIC quality ladder; use conservative recommendation")

    out: Dict[str, object] = {
        "crop_mode": crop_mode,
        "output_resolution": resolution,
        "highest_heic_quality": highest_quality,
        "lowest_pass_heic_quality": lowest_pass,
        "first_warn_heic_quality": first_warn,
        "first_fail_heic_quality": first_fail,
        "recommended_min_heic_quality": recommended_quality,
        "notes": "; ".join(notes),
        "status_sequence_low_to_high_quality": " ".join(f"q{q}:{s}" for q, s in statuses),
    }
    if recommended_row:
        out.update({
            "recommended_heic_size_bytes": recommended_row.get("heic_size_bytes", ""),
            "recommended_heic_size_kb": recommended_row.get("heic_size_kb", ""),
            "recommended_messages_300b": recommended_row.get("messages_300b", ""),
            "recommended_messages_900b": recommended_row.get("messages_900b", ""),
            "recommended_tag_side_px_min": recommended_row.get("tag_side_px_min", ""),
            "recommended_same_res_psnr_rgb": recommended_row.get("same_resolution_ref_psnr_rgb", ""),
            "recommended_card_psnr_rgb": recommended_row.get("ref_psnr_rgb", ""),
        })
    return out


def build_threshold_summary(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for r in rows:
        key = (str(r.get("crop_mode", "")), str(r.get("resolution", "")))
        groups.setdefault(key, []).append(r)
    out = []
    # Sort high spatial density to low, but threshold within each group is quality-low-to-high.
    def res_pixels(res: str) -> int:
        m = re.match(r"(\d+)x(\d+)", res)
        return int(m.group(1)) * int(m.group(2)) if m else 0
    for (mode, res), group in sorted(groups.items(), key=lambda kv: (kv[0][0], -res_pixels(kv[0][1]))):
        out.append(summarize_one_resolution(mode, res, group))
    return out


def build_global_best_summary(threshold_rows: List[Dict[str, object]], all_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Pick the smallest recommended payload per crop mode among resolution-level recommendations."""
    rows_by_key = {(str(r.get("crop_mode")), str(r.get("resolution")), safe_int(r.get("heic_quality"))): r for r in all_rows}
    by_mode: Dict[str, List[Dict[str, object]]] = {}
    for tr in threshold_rows:
        if tr.get("recommended_min_heic_quality") == "":
            continue
        by_mode.setdefault(str(tr.get("crop_mode")), []).append(tr)

    out: List[Dict[str, object]] = []
    for mode, trs in sorted(by_mode.items()):
        candidates = []
        for tr in trs:
            q = safe_int(tr.get("recommended_min_heic_quality"), -1)
            key = (mode, str(tr.get("output_resolution")), q)
            rr = rows_by_key.get(key)
            if rr:
                candidates.append(rr)
        if not candidates:
            continue
        best = sorted(candidates, key=lambda r: (safe_int(r.get("heic_size_bytes"), 10**18), -safe_int(r.get("output_width"), 0)))[0]
        out.append({
            "crop_mode": mode,
            "recommended_output_resolution_by_smallest_payload": best.get("resolution", ""),
            "recommended_heic_quality": best.get("heic_quality", ""),
            "heic_size_bytes": best.get("heic_size_bytes", ""),
            "heic_size_kb": best.get("heic_size_kb", ""),
            "messages_300b": best.get("messages_300b", ""),
            "messages_900b": best.get("messages_900b", ""),
            "tag_side_px_min": best.get("tag_side_px_min", ""),
            "same_resolution_ref_psnr_rgb": best.get("same_resolution_ref_psnr_rgb", ""),
            "card_ref_psnr_rgb": best.get("ref_psnr_rgb", ""),
            "notes": "smallest HEIC payload among per-resolution recommended PASS settings",
        })
    return out


# -----------------------------------------------------------------------------
# Cut sheets
# -----------------------------------------------------------------------------


def status_fill(status: str) -> Tuple[int, int, int]:
    if status == "PASS":
        return (220, 245, 226)
    if status == "WARN":
        return (255, 244, 210)
    if status == "FAIL":
        return (255, 224, 224)
    return (235, 238, 242)


def make_quality_ladder_sheet(
    rows: List[Dict[str, object]],
    out_path: Path,
    title: str,
    *,
    tile_image_size: Tuple[int, int] = (300, 169),
) -> None:
    ensure_dir(out_path.parent)
    rows = quality_order(rows)
    f_title = pil_font(26, True)
    f_sub = pil_font(14)
    f_small = pil_font(12)
    margin = 24
    cols = 3
    label_h = 130
    tile_w = tile_image_size[0] + 28
    tile_h = tile_image_size[1] + label_h
    sheet_w = margin * 2 + cols * tile_w
    sheet_h = margin * 2 + 84 + max(1, math.ceil(len(rows) / cols)) * tile_h
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    d.text((margin, margin), title, font=f_title, fill=(30, 50, 70))
    d.text((margin, margin + 34), "HEIC decoded to PNG for analysis; tiles normalized to same display size.", font=f_sub, fill=(90, 100, 110))

    for i, r in enumerate(rows):
        x = margin + (i % cols) * tile_w
        y = margin + 84 + (i // cols) * tile_h
        status = str(r.get("quality_status", ""))
        d.rectangle((x, y, x + tile_w - 10, y + tile_h - 8), fill=status_fill(status), outline=(195, 205, 215))
        img_path = Path(str(r.get("decoded_analysis_path", "")))
        if img_path.exists():
            try:
                with Image.open(img_path) as im:
                    im = im.convert("RGB")
                    im.thumbnail(tile_image_size, Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", tile_image_size, (230, 235, 240))
                    canvas.paste(im, ((tile_image_size[0] - im.width) // 2, (tile_image_size[1] - im.height) // 2))
                    sheet.paste(canvas, (x + 8, y + 8))
            except Exception:
                d.text((x + 14, y + 18), "image load error", font=f_small, fill=(120, 40, 40))
        ly = y + tile_image_size[1] + 16
        lines = [
            f"Q={r.get('heic_quality')}  {status}",
            f"HEIC={r.get('heic_size_kb')} KB",
            f"msgs: 300B={r.get('messages_300b')}  900B={r.get('messages_900b')}",
            f"tags={r.get('tag_count')} min_tag={r.get('tag_side_px_min')}",
            f"PSNR same-res={r.get('same_resolution_ref_psnr_rgb')}",
        ]
        for j, line in enumerate(lines):
            d.text((x + 12, ly + j * 20), line, font=f_small, fill=(35, 50, 65))
    sheet.save(out_path, quality=94)


def make_threshold_summary_sheet(threshold_rows: List[Dict[str, object]], global_rows: List[Dict[str, object]], out_path: Path) -> None:
    ensure_dir(out_path.parent)
    f_title = pil_font(26, True)
    f_head = pil_font(15, True)
    f_small = pil_font(12)
    margin = 28
    row_h = 28
    width = 1500
    height = margin * 2 + 90 + max(1, len(threshold_rows) + len(global_rows) + 4) * row_h
    sheet = Image.new("RGB", (width, height), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    d.text((margin, margin), "HEIC compression threshold summary", font=f_title, fill=(30, 50, 70))
    d.text((margin, margin + 36), "Message counts are payload-only ceil(HEIC bytes / chunk size).", font=f_small, fill=(90, 100, 110))

    y = margin + 82
    d.text((margin, y), "Per-resolution recommendation", font=f_head, fill=(35, 50, 65))
    y += row_h
    headers = ["mode", "resolution", "lowest PASS Q", "recommended Q", "HEIC KB", "300B msgs", "900B msgs", "sequence"]
    xs = [margin, 120, 270, 405, 535, 650, 770, 890]
    for x, h in zip(xs, headers):
        d.text((x, y), h, font=f_head, fill=(35, 50, 65))
    y += row_h
    for r in threshold_rows:
        vals = [
            r.get("crop_mode", ""),
            r.get("output_resolution", ""),
            r.get("lowest_pass_heic_quality", ""),
            r.get("recommended_min_heic_quality", ""),
            r.get("recommended_heic_size_kb", ""),
            r.get("recommended_messages_300b", ""),
            r.get("recommended_messages_900b", ""),
            r.get("status_sequence_low_to_high_quality", ""),
        ]
        for x, val in zip(xs, vals):
            d.text((x, y), str(val)[:70], font=f_small, fill=(35, 50, 65))
        y += row_h

    y += row_h
    d.text((margin, y), "Smallest recommended payload by crop mode", font=f_head, fill=(35, 50, 65))
    y += row_h
    headers = ["mode", "resolution", "Q", "HEIC KB", "300B msgs", "900B msgs", "min tag px", "same-res PSNR"]
    xs = [margin, 120, 270, 330, 455, 580, 705, 830]
    for x, h in zip(xs, headers):
        d.text((x, y), h, font=f_head, fill=(35, 50, 65))
    y += row_h
    for r in global_rows:
        vals = [
            r.get("crop_mode", ""),
            r.get("recommended_output_resolution_by_smallest_payload", ""),
            r.get("recommended_heic_quality", ""),
            r.get("heic_size_kb", ""),
            r.get("messages_300b", ""),
            r.get("messages_900b", ""),
            r.get("tag_side_px_min", ""),
            r.get("same_resolution_ref_psnr_rgb", ""),
        ]
        for x, val in zip(xs, vals):
            d.text((x, y), str(val), font=f_small, fill=(35, 50, 65))
        y += row_h
    sheet.save(out_path, quality=94)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Run a HEIC quality sweep on selected Sprint 02 spatial-density outputs and rerun AprilTag/card quality analysis."
    )
    ap.add_argument("--spatial-sweep", "--input", dest="spatial_sweep", required=True,
                    help="Path to Sprint 02 spatial sweep output directory containing downsampled/<mode>/<mode>_<res>.jpg")
    ap.add_argument("--output", default="",
                    help="Output directory. Default: ~/Downloads/bm_heic_compression_sweep/heic_<UTC>")
    ap.add_argument("--quality-script", default="./bm_reference_card_quality_v2.py",
                    help="Path to existing bm_reference_card_quality_v2.py analyzer")
    ap.add_argument("--crop-modes", nargs="+", default=["fixed", "auto"],
                    help="Crop modes to process if present. Default: fixed auto")
    ap.add_argument("--resolutions", nargs="+", type=parse_resolution,
                    default=DEFAULT_RESOLUTIONS,
                    help="Spatial ladder to test. Default: 3072x1728 2688x1512 2304x1296 1920x1080 1600x900")
    ap.add_argument("--qualities", nargs="+", type=int, default=DEFAULT_QUALITIES,
                    help="HEIC quality ladder. Default: 10 20 30 40 50 60 70 80 90")
    ap.add_argument("--chunk-sizes", nargs="+", type=int, default=DEFAULT_CHUNK_SIZES,
                    help="Payload chunk sizes for message estimates. Default: 300 900")
    ap.add_argument("--corner-map", default=DEFAULT_CORNER_MAP,
                    help="AprilTag corner map. Default: tl:0,tr:1,bl:2,br:3")
    ap.add_argument("--tag-family", default="DICT_APRILTAG_36h11")
    ap.add_argument("--scales", nargs="+", type=float, default=DEFAULT_ANALYZER_SCALES,
                    help="Analyzer detection scales. Default: 1 2 3 4")
    ap.add_argument("--codec-backend", choices=["auto", "sips", "magick"], default="auto",
                    help="HEIC encode/decode backend. Default: auto, preferring macOS sips then ImageMagick.")
    ap.add_argument("--prefix", default="test",
                    help="Prefix for cut sheet filenames. Default: test")
    ap.add_argument("--overwrite", action="store_true",
                    help="Remove output directory first if it already exists")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    spatial_sweep = expand_path(args.spatial_sweep)
    if not spatial_sweep.exists():
        raise SystemExit(f"Spatial sweep directory not found: {spatial_sweep}")
    quality_script = expand_path(args.quality_script)
    if not quality_script.exists():
        raise SystemExit(f"Quality script not found: {quality_script}")
    qualities = sorted(set(int(q) for q in args.qualities))
    for q in qualities:
        if q < 1 or q > 100:
            raise SystemExit(f"Invalid HEIC quality {q}; expected 1-100")
    chunk_sizes = sorted(set(int(x) for x in args.chunk_sizes))
    for c in chunk_sizes:
        if c <= 0:
            raise SystemExit(f"Invalid chunk size {c}; must be positive")
    corner_map = parse_corner_map(args.corner_map)
    req_ids = required_tag_ids(corner_map)

    out_dir = expand_path(args.output) if args.output else expand_path(f"~/Downloads/bm_heic_compression_sweep/heic_{utc_stamp()}")
    if out_dir.exists() and args.overwrite:
        shutil.rmtree(out_dir)
    ensure_dir(out_dir)

    manifest: Dict[str, object] = {
        "created_utc": utc_stamp(),
        "platform": platform.platform(),
        "spatial_sweep": str(spatial_sweep),
        "output_dir": str(out_dir),
        "quality_script": str(quality_script),
        "crop_modes_requested": args.crop_modes,
        "resolutions": [f"{w}x{h}" for w, h in args.resolutions],
        "qualities": qualities,
        "corner_map": args.corner_map,
        "required_tag_ids": req_ids,
        "chunk_sizes": chunk_sizes,
        "warnings": [],
        "errors": [],
    }

    print(f"[heic-sweep] output={out_dir}")
    print(f"[heic-sweep] spatial_sweep={spatial_sweep}")
    print(f"[heic-sweep] quality_script={quality_script}")

    try:
        backend = choose_codec_backend(args.codec_backend)
        manifest["codec_backend"] = backend
        print(f"[heic-sweep] codec_backend={backend}")

        print("[heic-sweep] collecting source images from Sprint 02 downsample ladder")
        sources, warnings = collect_sources(spatial_sweep, out_dir, args.crop_modes, args.resolutions)
        manifest["warnings"] = warnings
        manifest["source_images"] = [asdict(x) for x in sources]
        for w in warnings:
            print(f"[heic-sweep] WARNING: {w}")
        if not sources:
            raise RuntimeError("No source images found. Check --spatial-sweep and --crop-modes/--resolutions.")
        sources_by_key = {(s.crop_mode, s.output_width, s.output_height): s for s in sources}

        print("[heic-sweep] encoding HEIC quality ladder and decoding to PNG for analysis")
        encode_infos: List[HeicEncodeInfo] = []
        for src in sources:
            for q in qualities:
                print(f"[heic-sweep] encode/decode mode={src.crop_mode} res={src.resolution} q={q}")
                enc = encode_decode_one(src, out_dir, q, backend)
                encode_infos.append(enc)
        manifest["heic_outputs"] = [asdict(x) for x in encode_infos]

        all_merged_rows: List[Dict[str, object]] = []
        modes_with_outputs = sorted(set(e.crop_mode for e in encode_infos))
        for mode in modes_with_outputs:
            decoded_dir = out_dir / "decoded" / mode
            quality_out = out_dir / "quality" / mode
            # Reference for card-level analyzer comparison: the uncompressed/high-quality Sprint 02 3072x1728 source for this mode.
            reference = out_dir / "source_inputs" / mode / f"{mode}_3072x1728.jpg"
            if not reference.exists():
                # Fall back to whatever extension was copied.
                refs = sorted((out_dir / "source_inputs" / mode).glob(f"{mode}_3072x1728.*"))
                reference = refs[0] if refs else None
            print(f"[heic-sweep] running quality analyzer for mode={mode}")
            csv_path = run_quality_analyzer(
                quality_script=quality_script,
                decoded_dir=decoded_dir,
                quality_out=quality_out,
                corner_map=args.corner_map,
                tag_family=args.tag_family,
                scales=args.scales,
                reference=reference if isinstance(reference, Path) else None,
            )
            mode_encodes = [e for e in encode_infos if e.crop_mode == mode]
            print(f"[heic-sweep] merging HEIC/message metadata for mode={mode}")
            merged = merge_results(csv_path, mode_encodes, sources_by_key, req_ids, chunk_sizes)
            all_merged_rows.extend(merged)
            write_csv(out_dir / "results" / f"heic_compression_quality_{mode}.csv", merged, preferred_result_fields(chunk_sizes))

            # One visual sheet per mode + resolution. Useful because 5x9 in one sheet is too dense.
            for res in sorted(set(str(r.get("resolution", "")) for r in merged), key=lambda s: -res_pixels(s)):
                res_rows = [r for r in merged if str(r.get("resolution")) == res]
                if not res_rows:
                    continue
                sheet_path = out_dir / "cut_sheets" / f"{args.prefix}_heic_quality_ladder_{mode}_{res}.jpg"
                make_quality_ladder_sheet(res_rows, sheet_path, f"HEIC quality ladder: {mode} {res}")

        print("[heic-sweep] writing summaries")
        write_csv(out_dir / "results" / "heic_compression_quality_all.csv", all_merged_rows, preferred_result_fields(chunk_sizes))
        threshold_rows = build_threshold_summary(all_merged_rows)
        global_rows = build_global_best_summary(threshold_rows, all_merged_rows)
        write_csv(out_dir / "results" / "heic_threshold_summary.csv", threshold_rows, preferred_threshold_fields())
        write_csv(out_dir / "results" / "heic_global_recommendation_summary.csv", global_rows, preferred_global_fields())
        make_threshold_summary_sheet(threshold_rows, global_rows, out_dir / "cut_sheets" / f"{args.prefix}_heic_threshold_summary.jpg")

        manifest["result_files"] = {
            "all_results": str(out_dir / "results" / "heic_compression_quality_all.csv"),
            "threshold_summary": str(out_dir / "results" / "heic_threshold_summary.csv"),
            "global_recommendation_summary": str(out_dir / "results" / "heic_global_recommendation_summary.csv"),
            "threshold_summary_sheet": str(out_dir / "cut_sheets" / f"{args.prefix}_heic_threshold_summary.jpg"),
        }
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        print("[heic-sweep] complete")
        print(f"[heic-sweep] results={out_dir / 'results'}")
        print(f"[heic-sweep] cut_sheets={out_dir / 'cut_sheets'}")
        return 0
    except Exception as exc:
        manifest["errors"].append({
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })
        ensure_dir(out_dir)
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[heic-sweep] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"[heic-sweep] manifest written: {out_dir / 'run_manifest.json'}", file=sys.stderr)
        return 1


def res_pixels(res: str) -> int:
    m = re.match(r"(\d+)x(\d+)", str(res))
    return int(m.group(1)) * int(m.group(2)) if m else 0


def preferred_result_fields(chunk_sizes: Sequence[int]) -> List[str]:
    fields = [
        "crop_mode", "resolution", "output_width", "output_height", "heic_quality",
        "quality_status", "status_reason", "analyzer_quality_status",
        "heic_size_bytes", "heic_size_kb", "source_size_bytes", "source_size_kb",
        "compression_ratio_vs_source_jpeg",
        "messages_300b", "messages_900b",
    ]
    for c in chunk_sizes:
        k = f"messages_{c}b"
        if k not in fields:
            fields.append(k)
    fields.extend([
        "tag_count", "tag_ids", "tag_side_px_min", "tag_side_px_mean",
        "tag_laplacian_var_mean", "tag_tenengrad_mean", "tag_contrast_mean",
        "fiducial_geometry_residual_px",
        "card_laplacian_var", "card_tenengrad", "card_contrast_p95_p05",
        "card_clipped_dark_frac", "card_clipped_bright_frac",
        "same_resolution_ref_psnr_rgb", "same_resolution_ref_laplacian_corr",
        "ref_psnr_rgb", "ref_laplacian_corr", "ref_mse_rgb",
        "heic_bytes_per_megapixel", "output_pixels_total", "megapixels",
        "source_same_resolution_path", "heic_path", "decoded_analysis_path",
        "codec_backend", "detector_best_scale", "rejected_candidates", "corner_status",
        "has_card_rectified", "reference_name", "source_name", "source_path", "error",
    ])
    return fields


def preferred_threshold_fields() -> List[str]:
    return [
        "crop_mode", "output_resolution", "highest_heic_quality",
        "lowest_pass_heic_quality", "first_warn_heic_quality", "first_fail_heic_quality",
        "recommended_min_heic_quality", "recommended_heic_size_bytes", "recommended_heic_size_kb",
        "recommended_messages_300b", "recommended_messages_900b",
        "recommended_tag_side_px_min", "recommended_same_res_psnr_rgb", "recommended_card_psnr_rgb",
        "notes", "status_sequence_low_to_high_quality",
    ]


def preferred_global_fields() -> List[str]:
    return [
        "crop_mode", "recommended_output_resolution_by_smallest_payload", "recommended_heic_quality",
        "heic_size_bytes", "heic_size_kb", "messages_300b", "messages_900b",
        "tag_side_px_min", "same_resolution_ref_psnr_rgb", "card_ref_psnr_rgb", "notes",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
