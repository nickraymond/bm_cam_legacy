#!/usr/bin/env python3
"""
BM Reference Card JPEG (baseline/progressive) + Partial-Transmission Sweep

Sprint 06 (Mac-side DOE). See sprints/Sprint06_jpeg_partial_transmission_sweep.md.

Purpose
-------
Find the JPEG (mode, quality) setting to deploy on BMCAM so that a tail-cut
transmission (backend bug register B6) still yields a usable partial image.
This script:

  1. Loads a native 4608x2592 source, applies the native crop (default: the
     Sprint02 fixed crop, native coords 768,432,3072,1728), and lanczos-
     downsamples to the output size (default 1600x900). Geometry is
     overridable via --crop-native / --output-width (added for the geometry
     axis probe); defaults reproduce the sprint-fixed behavior exactly.
     Run ONE geometry per run folder (file stems do not encode geometry).
  2. Encodes the 1600x900 source to JPEG across a quality ladder, in
     baseline and/or progressive mode (Pillow, no subprocess).
  3. Computes the realistic transmit budget on the BASE64 stream:
         base64_len    = len(base64(jpeg_bytes))
         message_count = ceil(base64_len / 300)      # 300 b64 chars/message
         est_minutes   = message_count * 5 / 60      # 5 s per message
     (The Sprint03 HEIC sweep divided raw bytes by 300 and undercounted ~33%.)
  4. Truncation harness (tail-loss model, spec decision D2): keep the FIRST
     M of N 300-base64-char chunks, map to a raw-byte prefix (300 b64 chars
     = exactly 225 bytes), decode with PIL LOAD_TRUNCATED_IMAGES, estimate
     the recovered frame fraction, and reject decodes below a minimum.
  5. Scores every (image, mode, quality, fraction) cell:
       - full-frame sharpness/contrast + PSNR/MSE/laplacian-corr vs the
         lossless 1600x900 source (both images, computed in-script), and
       - AprilTag detection via the unchanged bm_reference_card_quality_v2.py
         analyzer (card image only; the coral has no tags).
  6. Writes a self-contained timestamped run folder: run_manifest.json,
     results CSVs, decoded frames, cut sheets, log.

Coordinate systems
------------------
  native  : 4608x2592 sensor-equivalent pixels (crop box is in these).
  output  : 1600x900 downsampled pixels (all metrics/detection happen here).

Inputs (defaults)
-----------------
  card  : reference_images/reference_card_native_imx708.jpg          (has tags)
  coral : reference_images/prepared/P7071008/synthetic_native_4608x2592.jpg
          (prepared from reference_reef_coral_primary.jpg; no tags)

Example (P0 smoke)
------------------
  .venv/bin/python3 tools/bm_reference_card_jpeg_partial_sweep.py \
      --images card --qualities 50 --modes baseline progressive \
      --fractions 50 100 --output ~/Downloads/bm_jpeg_partial_sweep/smoke

Example (P1 coarse quality sweep, complete images)
--------------------------------------------------
  .venv/bin/python3 tools/bm_reference_card_jpeg_partial_sweep.py \
      --images card coral --qualities 10 30 50 70 90 \
      --modes baseline --fractions 100

Assumptions / known limitations
-------------------------------
  - Pillow encode with optimize=True for BOTH modes (progressive implies
    optimized Huffman tables; using optimize for baseline keeps the size
    comparison fair). Chroma subsampling is Pillow's default (4:2:0).
    Pi-side encoder parity is validated in the P4 fast-follow sprint.
  - Recovered-fraction estimate is a heuristic: rows whose grayscale
    std < 1.0 (libjppeg's uniform gray fill for undecoded regions) counted
    from the bottom up. Real reef/card rows are never that flat at 1600 px.
  - Message counts are payload-only: no Bristlemouth/Sofar framing,
    headers, retransmits, or protocol overhead.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import platform
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFile, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent

# Sprint-fixed geometry (native sensor-equivalent coords, Sprint02 fixed crop).
FIXED_CROP_NATIVE = (768, 432, 3072, 1728)  # x, y, w, h
OUTPUT_SIZE = (1600, 900)                   # output coords
NATIVE_SIZE = (4608, 2592)

# Transmission model (spec section 2).
CHUNK_B64_CHARS = 300      # base64 chars per BM message
SECONDS_PER_MESSAGE = 5.0

# Budget bands in messages (spec section 3, coral-anchored).
BAND_IDEAL_MAX = 75
BAND_FEASIBLE_MAX = 125
BAND_GATED_MAX = 180       # hard cap ~15 min

DEFAULT_IMAGES = {
    "card": REPO_ROOT / "reference_images" / "reference_card_native_imx708.jpg",
    "coral": REPO_ROOT / "reference_images" / "prepared" / "P7071008" / "synthetic_native_4608x2592.jpg",
}
IMAGES_WITH_TAGS = {"card"}

DEFAULT_CORNER_MAP = "tl:0,tr:1,bl:2,br:3"   # verified on the 1600x900 card crop
REQUIRED_TAG_IDS = [0, 1, 2, 3]              # all 4 required for PASS (spec)


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


class RunLog:
    """Print progress lines and mirror them into the run folder."""

    def __init__(self) -> None:
        self.lines: List[str] = []
        self.path: Optional[Path] = None

    def attach(self, path: Path) -> None:
        self.path = path
        if self.lines:
            path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"[jpeg-sweep] {msg}"
        print(line, flush=True)
        self.lines.append(line)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


log = RunLog()


def pil_font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def write_csv(path: Path, rows: List[Dict[str, object]], preferred_fields: Optional[List[str]] = None) -> None:
    ensure_dir(path.parent)
    fields: List[str] = list(preferred_fields or [])
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# -----------------------------------------------------------------------------
# Source preparation: fixed native crop -> 1600x900 lossless working source
# -----------------------------------------------------------------------------


def prepare_source(label: str, native_path: Path, out_dir: Path) -> Path:
    """Native 4608x2592 -> fixed crop (native coords) -> 1600x900 lanczos PNG.

    PNG keeps the working source lossless so PSNR is measured against the
    true encode input, not a second JPEG generation.
    """
    with Image.open(native_path) as im:
        im = im.convert("RGB")
        if im.size != NATIVE_SIZE:
            raise SystemExit(
                f"{label}: expected native {NATIVE_SIZE[0]}x{NATIVE_SIZE[1]}, got {im.size[0]}x{im.size[1]}: {native_path}"
            )
        x, y, w, h = FIXED_CROP_NATIVE
        cropped = im.crop((x, y, x + w, y + h))
        source = cropped.resize(OUTPUT_SIZE, Image.Resampling.LANCZOS)
    out = ensure_dir(out_dir / f"source_{OUTPUT_SIZE[0]}") / f"{label}_source_{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}.png"
    source.save(out, format="PNG")
    log(f"source ready: {label} native={native_path.name} crop_native={FIXED_CROP_NATIVE} -> {OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]} ({out.stat().st_size/1024:.0f} KB PNG)")
    return out


# -----------------------------------------------------------------------------
# JPEG encode + transmission budget
# -----------------------------------------------------------------------------


@dataclass
class EncodeInfo:
    image_label: str
    mode: str                 # baseline | progressive
    jpeg_quality: int
    jpeg_path: str
    jpeg_bytes: int
    jpeg_kb: float
    base64_len: int
    message_count: int
    est_minutes: float
    duration_band: str


def duration_band(message_count: int) -> str:
    if message_count <= BAND_IDEAL_MAX:
        return "ideal"
    if message_count <= BAND_FEASIBLE_MAX:
        return "feasible"
    if message_count <= BAND_GATED_MAX:
        return "gated"
    return "over_cap"


def encode_jpeg(label: str, source_png: Path, mode: str, quality: int, out_dir: Path) -> EncodeInfo:
    stem = f"{label}_{mode}_q{quality:02d}"
    jpeg_path = ensure_dir(out_dir / "jpeg" / label) / f"{stem}.jpg"
    with Image.open(source_png) as im:
        im.convert("RGB").save(
            jpeg_path,
            format="JPEG",
            quality=quality,
            progressive=(mode == "progressive"),
            optimize=True,  # progressive implies optimized tables; keep baseline comparable
        )
    raw = jpeg_path.read_bytes()
    b64_len = len(base64.b64encode(raw))
    msgs = math.ceil(b64_len / CHUNK_B64_CHARS)
    return EncodeInfo(
        image_label=label,
        mode=mode,
        jpeg_quality=quality,
        jpeg_path=str(jpeg_path),
        jpeg_bytes=len(raw),
        jpeg_kb=round(len(raw) / 1024.0, 3),
        base64_len=b64_len,
        message_count=msgs,
        est_minutes=round(msgs * SECONDS_PER_MESSAGE / 60.0, 3),
        duration_band=duration_band(msgs),
    )


# -----------------------------------------------------------------------------
# Truncation harness (tail loss: keep first M of N chunks)
# -----------------------------------------------------------------------------


@dataclass
class PartialInfo:
    received_fraction_pct: int
    messages_kept: int
    messages_total: int
    truncated_bytes: int
    decode_ok: bool
    recovered_fraction_est: float
    recovered_status: str     # OK | REJECTED_LOW_RECOVERY | DECODE_FAIL
    decoded_path: str


def truncate_to_chunks(raw: bytes, base64_len: int, fraction_pct: int) -> Tuple[bytes, int, int]:
    """Keep the first M of N 300-base64-char chunks; return the raw-byte prefix.

    300 base64 chars encode exactly 225 raw bytes (300 divisible by 4), so the
    kept prefix is messages_kept * 225 bytes, clamped to the file size.
    """
    total_msgs = math.ceil(base64_len / CHUNK_B64_CHARS)
    kept_msgs = total_msgs if fraction_pct >= 100 else max(1, math.floor(total_msgs * fraction_pct / 100.0))
    bytes_per_msg = CHUNK_B64_CHARS * 3 // 4
    kept_bytes = min(len(raw), kept_msgs * bytes_per_msg)
    return raw[:kept_bytes], kept_msgs, total_msgs


def estimate_recovered_fraction(decoded: Image.Image) -> float:
    """Fraction of frame rows that decoded to real content (heuristic).

    libjpeg fills undecoded regions with uniform gray; count rows from the
    bottom whose grayscale std < 1.0 as unrecovered. Real 1600-px reef/card
    rows always have texture, so flat rows mean missing data.
    """
    gray = np.asarray(decoded.convert("L"), dtype=np.float32)
    row_std = gray.std(axis=1)
    content = row_std >= 1.0
    if not content.any():
        return 0.0
    last_content_row = int(np.where(content)[0][-1])
    return round((last_content_row + 1) / gray.shape[0], 4)


def decode_partial(
    enc: EncodeInfo,
    fraction_pct: int,
    out_dir: Path,
    min_recovered_fraction: float,
) -> PartialInfo:
    raw = Path(enc.jpeg_path).read_bytes()
    truncated, kept_msgs, total_msgs = truncate_to_chunks(raw, enc.base64_len, fraction_pct)
    stem = f"{enc.image_label}_{enc.mode}_q{enc.jpeg_quality:02d}_f{fraction_pct:03d}"
    decoded_path = ensure_dir(out_dir / "decoded" / enc.image_label) / f"{stem}_decoded.png"

    tmp = decoded_path.with_suffix(".partial.jpg")
    tmp.write_bytes(truncated)
    decode_ok = False
    recovered = 0.0
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        with Image.open(tmp) as im:
            frame = im.convert("RGB")
            if frame.size != OUTPUT_SIZE:
                raise ValueError(f"partial decode size {frame.size} != {OUTPUT_SIZE}")
            frame.save(decoded_path, format="PNG")
        decode_ok = True
        with Image.open(decoded_path) as im:
            recovered = estimate_recovered_fraction(im)
    except Exception as exc:
        log(f"WARNING decode failed {stem}: {type(exc).__name__}: {exc}")
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = False
        tmp.unlink(missing_ok=True)

    if not decode_ok:
        status = "DECODE_FAIL"
    elif recovered < min_recovered_fraction:
        status = "REJECTED_LOW_RECOVERY"
    else:
        status = "OK"

    return PartialInfo(
        received_fraction_pct=fraction_pct,
        messages_kept=kept_msgs,
        messages_total=total_msgs,
        truncated_bytes=len(truncated),
        decode_ok=decode_ok,
        recovered_fraction_est=recovered,
        recovered_status=status,
        decoded_path=str(decoded_path) if decode_ok else "",
    )


# -----------------------------------------------------------------------------
# Full-frame metrics (both images; same formulas as the v2 analyzer)
# -----------------------------------------------------------------------------


def full_frame_metrics(decoded_path: Path, source_png: Path) -> Dict[str, object]:
    test = cv2.cvtColor(np.asarray(Image.open(decoded_path).convert("RGB")), cv2.COLOR_RGB2BGR)
    ref = cv2.cvtColor(np.asarray(Image.open(source_png).convert("RGB")), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(test, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    p05, p95 = np.percentile(gray, [5, 95])

    ref_f, tst_f = ref.astype(np.float32), test.astype(np.float32)
    mse = float(np.mean((ref_f - tst_f) ** 2))
    psnr = 99.0 if mse <= 1e-9 else float(20 * math.log10(255.0 / math.sqrt(mse)))
    ref_lap = cv2.Laplacian(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32), cv2.CV_32F).reshape(-1)
    tst_lap = cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F).reshape(-1)
    denom = float(np.linalg.norm(ref_lap) * np.linalg.norm(tst_lap))
    corr = float(np.dot(ref_lap, tst_lap) / denom) if denom > 1e-9 else 0.0

    # Local chroma variation: mean(|R-G| + |G-B|). Low-quality JPEG (heavy
    # chroma quantization + 4:2:0) crushes this while leaving mean RGB intact,
    # which is why q5-q10 frames look color-posterized without a global shift.
    r, g, b = test[..., 2].astype(np.float32), test[..., 1].astype(np.float32), test[..., 0].astype(np.float32)
    chroma = float(np.mean(np.abs(r - g) + np.abs(g - b)))

    return {
        "ff_laplacian_var": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 4),
        "ff_tenengrad": round(float(np.mean(gx * gx + gy * gy)), 4),
        "ff_contrast_p95_p05": round(float(p95 - p05), 4),
        "ff_chroma_sat": round(chroma, 4),
        "ref_psnr_rgb": round(psnr, 4),
        "ref_mse_rgb": round(mse, 4),
        "ref_laplacian_corr": round(corr, 6),
    }


# -----------------------------------------------------------------------------
# AprilTag analyzer (card only), reused unchanged as a subprocess
# -----------------------------------------------------------------------------


def run_quality_analyzer(
    quality_script: Path,
    decoded_dir: Path,
    quality_out: Path,
    corner_map: str,
    scales: Sequence[float],
    reference: Path,
) -> Dict[str, Dict[str, str]]:
    """Run bm_reference_card_quality_v2.py; return rows keyed by file stem."""
    ensure_dir(quality_out)
    cmd = [
        sys.executable, str(quality_script),
        "--input", str(decoded_dir),
        "--output", str(quality_out),
        "--corner-map", corner_map,
        "--scales", *[str(x) for x in scales],
        "--reference", str(reference),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"analyzer failed rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    csv_path = quality_out / "reference_card_quality_results.csv"
    if not csv_path.exists():
        raise RuntimeError(f"analyzer did not create {csv_path}")
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        return {Path(r["source_name"]).stem: r for r in csv.DictReader(f)}


def parse_tag_ids(s: str) -> List[int]:
    out = []
    for part in str(s or "").split():
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def sprint_status(analyzer_row: Dict[str, str]) -> Tuple[str, str]:
    """All-4-required-tags PASS/WARN/FAIL (same rule as the Sprint03 sweep)."""
    detected = set(parse_tag_ids(analyzer_row.get("tag_ids", "")))
    missing = [t for t in REQUIRED_TAG_IDS if t not in detected]
    try:
        min_side = float(analyzer_row.get("tag_side_px_min") or 0.0)
    except ValueError:
        min_side = 0.0
    if missing:
        return "FAIL", "missing required tag IDs: " + " ".join(str(x) for x in missing)
    if min_side < 10.0:
        return "FAIL", f"all tags detected but min tag side {min_side:.1f}px < 10px"
    if min_side < 18.0:
        return "WARN", f"all tags detected but min tag side {min_side:.1f}px is 10-18px"
    return "PASS", f"all 4 tags detected, min tag side {min_side:.1f}px >= 18px"


ANALYZER_CARRY_FIELDS = [
    "tag_count", "tag_ids", "tag_side_px_min", "tag_side_px_mean",
    "tag_laplacian_var_mean", "tag_tenengrad_mean", "tag_contrast_mean",
    "fiducial_geometry_residual_px",
    "card_laplacian_var", "card_tenengrad", "card_contrast_p95_p05",
]


# -----------------------------------------------------------------------------
# Cut sheets
# -----------------------------------------------------------------------------


def status_fill(status: str) -> Tuple[int, int, int]:
    return {
        "PASS": (220, 245, 226),
        "WARN": (255, 244, 210),
        "FAIL": (255, 224, 224),
        "REJECTED_LOW_RECOVERY": (255, 224, 224),
        "DECODE_FAIL": (240, 220, 240),
    }.get(status, (235, 238, 242))


def make_tile_sheet(rows: List[Dict[str, object]], out_path: Path, title: str, subtitle: str) -> None:
    """Grid of decoded frames + metric labels. Tiles are display-normalized
    (all thumbnails scaled to the same tile size; NOT 1:1 pixels)."""
    ensure_dir(out_path.parent)
    f_title, f_sub, f_small = pil_font(26, True), pil_font(14), pil_font(12)
    tile_img = (360, 203)
    label_h = 152
    cols = min(3, max(1, len(rows)))
    margin = 24
    tile_w, tile_h = tile_img[0] + 28, tile_img[1] + label_h
    sheet_w = margin * 2 + cols * tile_w
    sheet_h = margin * 2 + 84 + max(1, math.ceil(len(rows) / cols)) * tile_h
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    d.text((margin, margin), title, font=f_title, fill=(30, 50, 70))
    d.text((margin, margin + 34), subtitle + "  |  tiles display-normalized (not 1:1)", font=f_sub, fill=(90, 100, 110))

    for i, r in enumerate(rows):
        x = margin + (i % cols) * tile_w
        y = margin + 84 + (i // cols) * tile_h
        status = str(r.get("sprint_status") or r.get("recovered_status") or "")
        d.rectangle((x, y, x + tile_w - 10, y + tile_h - 8), fill=status_fill(status), outline=(195, 205, 215))
        img_path = Path(str(r.get("decoded_path", "")))
        if img_path.is_file():
            with Image.open(img_path) as im:
                im = im.convert("RGB")
                im.thumbnail(tile_img, Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", tile_img, (230, 235, 240))
                canvas.paste(im, ((tile_img[0] - im.width) // 2, (tile_img[1] - im.height) // 2))
                sheet.paste(canvas, (x + 8, y + 8))
        else:
            d.text((x + 14, y + 18), "no decoded frame", font=f_small, fill=(120, 40, 40))
        ly = y + tile_img[1] + 14
        lines = [
            f"{r.get('mode')} q={r.get('jpeg_quality')} recv={r.get('received_fraction_pct')}%  {status}",
            f"full file: {r.get('jpeg_kb')} KB  b64={r.get('base64_len')}",
            f"msgs={r.get('message_count')} ({r.get('est_minutes')} min, {r.get('duration_band')})",
            f"kept {r.get('messages_kept')}/{r.get('messages_total')} msgs  recovered~{r.get('recovered_fraction_est')}",
            f"PSNR={r.get('ref_psnr_rgb')}  ff_sharp={r.get('ff_laplacian_var')}",
            f"tags={r.get('tag_count','-')} min_tag_px={r.get('tag_side_px_min','-')}",
        ]
        for j, line in enumerate(lines):
            d.text((x + 12, ly + j * 21), line, font=f_small, fill=(35, 50, 65))
    sheet.save(out_path, quality=94)
    log(f"cut sheet: {out_path}")


def make_source_compare_sheet(rows: List[Dict[str, object]], source_png: Path, out_path: Path, title: str, subtitle: str) -> None:
    """Side-by-side sheet: lossless source vs compressed, per quality.

    Per row: [source full frame | compressed full frame | source 1:1 detail |
    compressed 1:1 detail]. Full-frame tiles are display-normalized (NOT 1:1);
    detail panels are unscaled 1:1 pixel crops from the frame center, where
    chroma posterization is easiest to judge.
    """
    ensure_dir(out_path.parent)
    f_title, f_sub, f_small, f_tag = pil_font(26, True), pil_font(14), pil_font(12), pil_font(13, True)
    full_tile = (400, 225)
    detail_box = 225  # 1:1 crop side, matches tile height
    margin, gap, label_h = 24, 12, 76
    header_h = 84 + 22  # title/subtitle + column headers

    with Image.open(source_png) as im:
        src = im.convert("RGB")
    sw, sh = src.size
    dx0, dy0 = (sw - detail_box) // 2, (sh - detail_box) // 2
    src_full = src.copy()
    src_full.thumbnail(full_tile, Image.Resampling.LANCZOS)
    src_detail = src.crop((dx0, dy0, dx0 + detail_box, dy0 + detail_box))
    a = np.asarray(src, dtype=np.float32)
    src_sat = float(np.mean(np.abs(a[..., 0] - a[..., 1]) + np.abs(a[..., 1] - a[..., 2])))

    row_h = full_tile[1] + label_h
    cols_w = [full_tile[0], full_tile[0], detail_box, detail_box]
    sheet_w = margin * 2 + sum(cols_w) + gap * 3
    sheet_h = margin * 2 + header_h + len(rows) * (row_h + gap)
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    d.text((margin, margin), title, font=f_title, fill=(30, 50, 70))
    d.text((margin, margin + 34), subtitle, font=f_sub, fill=(90, 100, 110))
    col_x = [margin]
    for wcol in cols_w[:-1]:
        col_x.append(col_x[-1] + wcol + gap)
    for cx, htxt in zip(col_x, ["SOURCE (lossless) — normalized", "COMPRESSED — normalized",
                                "SOURCE 1:1 detail (center)", "COMPRESSED 1:1 detail"]):
        d.text((cx, margin + 62), htxt, font=f_tag, fill=(60, 75, 95))

    for i, r in enumerate(sorted(rows, key=lambda r: int(r["jpeg_quality"]))):
        y = margin + header_h + i * (row_h + gap)
        d.rectangle((margin - 8, y - 4, sheet_w - margin + 8, y + row_h - 6),
                    fill=status_fill(str(r.get("sprint_status") or "")), outline=(205, 212, 220))
        dec_path = Path(str(r.get("decoded_path", "")))
        panels: List[Optional[Image.Image]] = [src_full, None, src_detail, None]
        dec_sat = None
        if dec_path.is_file():
            with Image.open(dec_path) as im:
                dec = im.convert("RGB")
            b = np.asarray(dec, dtype=np.float32)
            dec_sat = float(np.mean(np.abs(b[..., 0] - b[..., 1]) + np.abs(b[..., 1] - b[..., 2])))
            dec_full = dec.copy()
            dec_full.thumbnail(full_tile, Image.Resampling.LANCZOS)
            panels[1] = dec_full
            panels[3] = dec.crop((dx0, dy0, dx0 + detail_box, dy0 + detail_box))
        for cx, wcol, panel in zip(col_x, cols_w, panels):
            if panel is None:
                d.text((cx + 10, y + 20), "no decoded frame", font=f_small, fill=(120, 40, 40))
            else:
                canvas = Image.new("RGB", (wcol, full_tile[1]), (230, 235, 240))
                canvas.paste(panel, ((wcol - panel.width) // 2, (full_tile[1] - panel.height) // 2))
                sheet.paste(canvas, (cx, y))
        sat_txt = f"chroma sat {dec_sat:.1f} vs source {src_sat:.1f} ({100.0 * dec_sat / src_sat:.0f}% retained)" if dec_sat else ""
        lines = [
            f"{r.get('mode')} q={r.get('jpeg_quality')}  {r.get('jpeg_kb')} KB  b64={r.get('base64_len')}  "
            f"msgs={r.get('message_count')} ({r.get('est_minutes')} min, {r.get('duration_band')})",
            f"PSNR={r.get('ref_psnr_rgb')}  ff_sharp={r.get('ff_laplacian_var')}  {sat_txt}",
            f"tags={r.get('tag_count', '-')}  status={r.get('sprint_status') or r.get('recovered_status')}",
        ]
        for j, line in enumerate(lines):
            d.text((margin + 4, y + full_tile[1] + 6 + j * 21), line, font=f_small, fill=(35, 50, 65))
    sheet.save(out_path, quality=94)
    log(f"cut sheet: {out_path}")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

RESULT_FIELDS = [
    "image_label", "mode", "jpeg_quality", "received_fraction_pct",
    "sprint_status", "status_reason", "recovered_status",
    "jpeg_bytes", "jpeg_kb", "base64_len", "message_count", "est_minutes", "duration_band",
    "messages_kept", "messages_total", "truncated_bytes",
    "decode_ok", "recovered_fraction_est",
    "ff_laplacian_var", "ff_tenengrad", "ff_contrast_p95_p05", "ff_chroma_sat",
    "ref_psnr_rgb", "ref_mse_rgb", "ref_laplacian_corr",
    *ANALYZER_CARRY_FIELDS,
    "jpeg_path", "decoded_path",
]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Sprint06 JPEG baseline/progressive + partial-transmission sweep (Mac-side DOE).")
    ap.add_argument("--images", nargs="+", choices=sorted(DEFAULT_IMAGES), default=["card", "coral"],
                    help="Which fixed inputs to run. Default: card coral")
    ap.add_argument("--card-path", type=Path, default=DEFAULT_IMAGES["card"])
    ap.add_argument("--coral-path", type=Path, default=DEFAULT_IMAGES["coral"])
    ap.add_argument("--modes", nargs="+", choices=["baseline", "progressive"], default=["baseline"],
                    help="JPEG modes. Default: baseline")
    ap.add_argument("--qualities", nargs="+", type=int, default=[10, 30, 50, 70, 90],
                    help="JPEG quality ladder. Default: 10 30 50 70 90")
    ap.add_argument("--fractions", nargs="+", type=int, default=[100],
                    help="Received-fraction pct (tail-loss model keeps first X%% of messages). Default: 100")
    ap.add_argument("--min-recovered-fraction", type=float, default=0.10,
                    help="Reject partial decodes recovering less than this frame fraction. Default: 0.10")
    ap.add_argument("--quality-script", type=Path, default=Path(__file__).resolve().parent / "bm_reference_card_quality_v2.py")
    ap.add_argument("--corner-map", default=DEFAULT_CORNER_MAP)
    ap.add_argument("--scales", nargs="+", type=float, default=[1, 2, 3],
                    help="Analyzer detection scales. Default: 1 2 3 (all 4 tags detect at 1 on the 1600px card)")
    ap.add_argument("--crop-native", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                    default=list(FIXED_CROP_NATIVE),
                    help="Native crop (ROI) in native 4608x2592 coords; sets the FOV. "
                         f"Default: {' '.join(str(v) for v in FIXED_CROP_NATIVE)} (Sprint02 fixed crop)")
    ap.add_argument("--output-width", type=int, default=OUTPUT_SIZE[0],
                    help="Output width in px; height follows the crop aspect ratio. "
                         "Messages scale ~linearly with output pixel area. Default: 1600")
    ap.add_argument("--output", type=Path, default=None,
                    help="Run folder. Default: ~/Downloads/bm_jpeg_partial_sweep/jpeg_<UTC>")
    ap.add_argument("--overwrite", action="store_true", help="Remove the output folder first if it exists")
    return ap


def main() -> int:
    args = build_parser().parse_args()

    # Geometry overrides (defaults reproduce the sprint-fixed values exactly).
    # Module globals are rebound so every downstream user (prepare_source,
    # decode size check, cut-sheet labels, manifest) sees one consistent geometry.
    global FIXED_CROP_NATIVE, OUTPUT_SIZE
    cx, cy, cw, ch = args.crop_native
    if not (0 <= cx and 0 <= cy and cx + cw <= NATIVE_SIZE[0] and cy + ch <= NATIVE_SIZE[1] and cw > 0 and ch > 0):
        raise SystemExit(f"--crop-native {args.crop_native} outside native {NATIVE_SIZE[0]}x{NATIVE_SIZE[1]}")
    if not 200 <= args.output_width <= cw:
        raise SystemExit(f"--output-width {args.output_width} must be 200..{cw} (no upsampling beyond the crop)")
    FIXED_CROP_NATIVE = (cx, cy, cw, ch)
    OUTPUT_SIZE = (args.output_width, round(args.output_width * ch / cw))

    qualities = sorted(set(args.qualities))
    fractions = sorted(set(args.fractions))
    for q in qualities:
        if not 1 <= q <= 95:
            raise SystemExit(f"Invalid JPEG quality {q}; expected 1-95 (Pillow >95 disables useful quantization)")
    for f in fractions:
        if not 1 <= f <= 100:
            raise SystemExit(f"Invalid received fraction {f}; expected 1-100")
    if not args.quality_script.exists():
        raise SystemExit(f"Quality script not found: {args.quality_script}")

    run_tag = f"jpeg_{utc_stamp()}"
    out_dir = (args.output or (Path.home() / "Downloads" / "bm_jpeg_partial_sweep" / run_tag)).expanduser().resolve()
    if out_dir.exists() and args.overwrite:
        shutil.rmtree(out_dir)
    ensure_dir(out_dir)
    log.attach(out_dir / "sweep_log.txt")
    log(f"run_tag={run_tag}")
    log(f"output={out_dir}")
    log(f"images={args.images} modes={args.modes} qualities={qualities} fractions={fractions}")

    image_paths = {"card": args.card_path.resolve(), "coral": args.coral_path.resolve()}
    manifest: Dict[str, object] = {
        "tool": "bm_reference_card_jpeg_partial_sweep.py",
        "sprint": "Sprint06 JPEG partial-transmission sweep (P0/P1/P2)",
        "run_tag": run_tag,
        "created_utc": utc_stamp(),
        "platform": platform.platform(),
        "python": sys.version,
        "pillow": Image.__version__ if hasattr(Image, "__version__") else "",
        "opencv": cv2.__version__,
        "coordinate_systems": {
            "native": f"{NATIVE_SIZE[0]}x{NATIVE_SIZE[1]} sensor-equivalent px; fixed_crop_xywh={FIXED_CROP_NATIVE}",
            "output": f"{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]} lanczos-downsampled px; all metrics/detection in these coords",
        },
        "transmission_model": {
            "chunk_base64_chars": CHUNK_B64_CHARS,
            "raw_bytes_per_message": CHUNK_B64_CHARS * 3 // 4,
            "seconds_per_message": SECONDS_PER_MESSAGE,
            "message_count_formula": "ceil(len(base64(jpeg_bytes)) / 300)",
            "bands_messages": {"ideal_max": BAND_IDEAL_MAX, "feasible_max": BAND_FEASIBLE_MAX, "gated_max_hard_cap": BAND_GATED_MAX},
            "truncation_model": "tail loss: keep first M of N chunks (spec decision D2)",
        },
        "encode_settings": {
            "encoder": "Pillow Image.save JPEG",
            "optimize": True,
            "subsampling": "Pillow default (4:2:0)",
            "note": "Pi-side encoder parity is validated in the P4 fast-follow sprint",
        },
        "inputs": {k: str(v) for k, v in image_paths.items() if k in args.images},
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "warnings": [],
        "errors": [],
    }

    try:
        rows: List[Dict[str, object]] = []
        for label in args.images:
            source_png = prepare_source(label, image_paths[label], out_dir)
            label_rows: List[Dict[str, object]] = []
            for mode in args.modes:
                for q in qualities:
                    enc = encode_jpeg(label, source_png, mode, q, out_dir)
                    log(f"encode {label} {mode} q{q}: {enc.jpeg_kb} KB -> b64={enc.base64_len} msgs={enc.message_count} ({enc.est_minutes} min, {enc.duration_band})")
                    for frac in fractions:
                        part = decode_partial(enc, frac, out_dir, args.min_recovered_fraction)
                        row: Dict[str, object] = {**asdict(enc), **asdict(part)}
                        if part.decode_ok and part.recovered_status == "OK":
                            row.update(full_frame_metrics(Path(part.decoded_path), source_png))
                        label_rows.append(row)
            # AprilTag detection only where tags exist; analyzer scores accepted decodes.
            if label in IMAGES_WITH_TAGS:
                decoded_dir = out_dir / "decoded" / label
                scored = [r for r in label_rows if r.get("recovered_status") == "OK"]
                if scored:
                    log(f"running AprilTag analyzer on {len(scored)} decoded frames for {label}")
                    analyzer_rows = run_quality_analyzer(
                        args.quality_script, decoded_dir, out_dir / "quality" / label,
                        args.corner_map, args.scales, source_png,
                    )
                    for r in label_rows:
                        stem = Path(str(r.get("decoded_path") or "x")).stem
                        ar = analyzer_rows.get(stem)
                        if ar is None:
                            continue
                        for k in ANALYZER_CARRY_FIELDS:
                            r[k] = ar.get(k, "")
                        status, reason = sprint_status(ar)
                        r["sprint_status"], r["status_reason"] = status, reason
                for r in label_rows:
                    if "sprint_status" not in r:
                        r["sprint_status"] = "FAIL"
                        r["status_reason"] = f"no scoreable decode ({r.get('recovered_status')})"
            else:
                for r in label_rows:
                    r["sprint_status"] = ""
                    r["status_reason"] = "no tags on this image; scored on sharpness/contrast/PSNR"
            rows.extend(label_rows)

        write_csv(out_dir / "results" / "results_jpeg_partial_sweep.csv", rows, RESULT_FIELDS)
        log(f"results CSV: {out_dir / 'results' / 'results_jpeg_partial_sweep.csv'} ({len(rows)} rows)")

        # Cut sheets: one quality ladder per (image, mode) at full receipt,
        # and one partial ladder per (image, mode, quality) across fractions.
        for label in args.images:
            for mode in args.modes:
                ladder = [r for r in rows if r["image_label"] == label and r["mode"] == mode and r["received_fraction_pct"] == 100]
                if ladder:
                    make_tile_sheet(
                        sorted(ladder, key=lambda r: int(r["jpeg_quality"])),
                        out_dir / "cut_sheets" / f"{run_tag}_{label}_{mode}_quality_ladder.jpg",
                        f"JPEG quality ladder: {label} {mode} (100% received)",
                        f"run={run_tag}  crop_native={FIXED_CROP_NATIVE} -> {OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}",
                    )
                    make_source_compare_sheet(
                        ladder,
                        out_dir / f"source_{OUTPUT_SIZE[0]}" / f"{label}_source_{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}.png",
                        out_dir / "cut_sheets" / f"{run_tag}_{label}_{mode}_source_vs_compressed.jpg",
                        f"Source vs compressed: {label} {mode} (100% received)",
                        f"run={run_tag}  crop_native={FIXED_CROP_NATIVE} -> {OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}"
                        "  |  full-frame tiles display-normalized (not 1:1); detail panels are 1:1 center crops",
                    )
                if len(fractions) > 1:
                    for q in qualities:
                        partial = [r for r in rows if r["image_label"] == label and r["mode"] == mode and int(r["jpeg_quality"]) == q]
                        if len(partial) > 1:
                            make_tile_sheet(
                                sorted(partial, key=lambda r: int(r["received_fraction_pct"])),
                                out_dir / "cut_sheets" / f"{run_tag}_{label}_{mode}_q{q:02d}_partial_ladder.jpg",
                                f"Partial transmission: {label} {mode} q{q}",
                                f"run={run_tag}  tail-loss model: first M of N 300-b64-char msgs kept",
                            )

        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        log("complete")
        log(f"results={out_dir / 'results'}")
        log(f"cut_sheets={out_dir / 'cut_sheets'}")
        return 0
    except Exception as exc:
        manifest["errors"].append({"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        log(f"ERROR: {type(exc).__name__}: {exc}")
        log(f"manifest written: {out_dir / 'run_manifest.json'}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
