#!/usr/bin/env python3
"""
Create visual comparison sheets for a reef spatial-density sweep.

Expected run structure:
  <run_dir>/
    01_synthetic_native_4608x2592.jpg
    results.csv
    up_2304/
    base_2030/
    down_1920/
    down_1600/

Outputs are written to <run_dir>/cut_sheets/.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROFILE_ORDER = ("up_2304", "base_2030", "down_1920", "down_1600")
EXPECTED_SIZES = {
    "up_2304": (2304, 1296),
    "base_2030": (2030, 1142),
    "down_1920": (1920, 1080),
    "down_1600": (1600, 900),
}


def get_font(size: int):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def load_results(path: Path):
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows found in {path}")
    return {row["profile"]: row for row in rows}


def locate_profile_files(run_dir: Path, profile: str):
    folder = run_dir / profile
    if not folder.is_dir():
        raise FileNotFoundError(f"Missing profile folder: {folder}")

    jpgs = sorted(folder.glob("02_*.jpg"))
    heics = sorted(folder.glob("03_*.heic"))

    if len(jpgs) != 1:
        raise RuntimeError(f"Expected one 02_*.jpg in {folder}, found {len(jpgs)}")
    if len(heics) != 1:
        raise RuntimeError(f"Expected one 03_*.heic in {folder}, found {len(heics)}")

    return jpgs[0], heics[0]


def decode_heic(path: Path, temp_dir: Path):
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        with Image.open(path) as image:
            return image.convert("RGB")
    except Exception:
        pass

    sips = shutil.which("sips")
    if not sips:
        raise RuntimeError(
            "HEIC decoder not available. On macOS, `sips` should exist. "
            "Otherwise run: python3 -m pip install pillow-heif"
        )

    out = temp_dir / f"{path.stem}.png"
    result = subprocess.run(
        [sips, "-s", "format", "png", str(path), "--out", str(out)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"Failed to decode {path}:\n{result.stdout}")

    with Image.open(out) as image:
        return image.convert("RGB")


def metrics(row):
    return (
        f"{row['profile']}  "
        f"{row['output_width']}×{row['output_height']}  "
        f"HEIC {int(row['heic_bytes']) / 1024:.1f} KiB  "
        f"{row['data_messages']} buffers  "
        f"{float(row['pace_minutes']):.2f} min"
    )


def centered_fraction(image: Image.Image, fraction: float):
    width = max(1, round(image.width * fraction))
    height = max(1, round(image.height * fraction))
    left = (image.width - width) // 2
    top = (image.height - height) // 2
    return image.crop((left, top, left + width, top + height))


def make_overlay(source: Image.Image, out: Path):
    image = source.convert("RGB").copy()
    draw = ImageDraw.Draw(image)
    font = get_font(46)

    x, y, w, h = 768, 432, 3072, 1728
    draw.rectangle((x, y, x + w - 1, y + h - 1), outline="red", width=14)

    label = "crop_67: x=768, y=432, 3072×1728"
    bbox = draw.textbbox((x + 24, y + 20), label, font=font)
    draw.rectangle(
        (bbox[0] - 10, bbox[1] - 8, bbox[2] + 10, bbox[3] + 8),
        fill="white",
    )
    draw.text((x + 24, y + 20), label, fill="red", font=font)
    image.save(out, "PNG")


def make_full_frame_sheet(images, results, title, out):
    panel_w, panel_h = 1100, 620
    margin, gap = 40, 30
    title_h, label_h = 90, 80

    sheet_w = margin * 2 + panel_w * 2 + gap
    sheet_h = margin * 2 + title_h + (panel_h + label_h) * 2 + gap

    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = get_font(34)
    label_font = get_font(23)

    draw.text((margin, margin), title, fill="black", font=title_font)

    for index, profile in enumerate(PROFILE_ORDER):
        row = index // 2
        col = index % 2
        x = margin + col * (panel_w + gap)
        y = margin + title_h + row * (panel_h + label_h + gap)

        preview = images[profile].copy()
        preview.thumbnail((panel_w, panel_h), Image.Resampling.LANCZOS)

        image_x = x + (panel_w - preview.width) // 2
        image_y = y + label_h

        draw.text((x, y), metrics(results[profile]), fill="black", font=label_font)
        sheet.paste(preview, (image_x, image_y))
        draw.rectangle(
            (image_x, image_y, image_x + preview.width - 1, image_y + preview.height - 1),
            outline="black",
            width=2,
        )

    sheet.save(out, "PNG")


def make_native_detail_sheet(images, results, title, out, fraction=0.30):
    details = {profile: centered_fraction(images[profile], fraction) for profile in PROFILE_ORDER}
    cell_w = max(image.width for image in details.values())
    cell_h = max(image.height for image in details.values())

    margin, gap = 40, 35
    title_h, label_h = 110, 80

    sheet_w = margin * 2 + cell_w * 2 + gap
    sheet_h = margin * 2 + title_h + (cell_h + label_h) * 2 + gap

    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = get_font(32)
    label_font = get_font(22)

    draw.text((margin, margin), title, fill="black", font=title_font)

    for index, profile in enumerate(PROFILE_ORDER):
        row = index // 2
        col = index % 2
        x = margin + col * (cell_w + gap)
        y = margin + title_h + row * (cell_h + label_h + gap)

        detail = details[profile]
        image_x = x + (cell_w - detail.width) // 2
        image_y = y + label_h

        label = f"{metrics(results[profile])}  detail {detail.width}×{detail.height}px"
        draw.text((x, y), label, fill="black", font=label_font)
        sheet.paste(detail, (image_x, image_y))
        draw.rectangle(
            (image_x, image_y, image_x + detail.width - 1, image_y + detail.height - 1),
            outline="black",
            width=2,
        )

    sheet.save(out, "PNG")


def make_pre_vs_heic_sheet(profile, pre, decoded, result, out):
    if pre.size != decoded.size:
        raise RuntimeError(f"{profile}: pre-HEIC {pre.size} != decoded HEIC {decoded.size}")

    pre_detail = centered_fraction(pre, 0.50)
    decoded_detail = centered_fraction(decoded, 0.50)

    width, height = pre_detail.size
    margin, gap = 40, 40
    title_h, label_h = 105, 55

    sheet_w = margin * 2 + width * 2 + gap
    sheet_h = margin * 2 + title_h + label_h + height

    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = get_font(31)
    label_font = get_font(24)

    draw.text(
        (margin, margin),
        f"{metrics(result)} — center 50% at 100% native pixels; no scaling",
        fill="black",
        font=title_font,
    )

    left_x = margin
    right_x = margin + width + gap
    image_y = margin + title_h + label_h

    draw.text((left_x, margin + title_h), "Pre-HEIC JPEG", fill="black", font=label_font)
    draw.text((right_x, margin + title_h), "Decoded HEIC Q20", fill="black", font=label_font)

    sheet.paste(pre_detail, (left_x, image_y))
    sheet.paste(decoded_detail, (right_x, image_y))
    sheet.save(out, "PNG")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    source_path = run_dir / "01_synthetic_native_4608x2592.jpg"
    results_path = run_dir / "results.csv"
    out_dir = run_dir / "cut_sheets"

    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run folder not found: {run_dir}")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not results_path.is_file():
        raise FileNotFoundError(results_path)

    results = load_results(results_path)

    for profile in PROFILE_ORDER:
        if profile not in results:
            raise RuntimeError(f"Missing {profile} in results.csv")

    out_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(source_path) as image:
        source = image.convert("RGB")

    pre_images = {}
    decoded_images = {}

    with tempfile.TemporaryDirectory(prefix="reef_spatial_heic_") as temp_name:
        temp_dir = Path(temp_name)

        for profile in PROFILE_ORDER:
            jpg_path, heic_path = locate_profile_files(run_dir, profile)

            with Image.open(jpg_path) as image:
                pre = image.convert("RGB")

            expected = EXPECTED_SIZES[profile]
            if pre.size != expected:
                raise RuntimeError(
                    f"{profile}: expected {expected[0]}×{expected[1]}, "
                    f"got {pre.width}×{pre.height}"
                )

            decoded = decode_heic(heic_path, temp_dir)
            if decoded.size != pre.size:
                raise RuntimeError(
                    f"{profile}: decoded HEIC size {decoded.size} != JPEG size {pre.size}"
                )

            pre_images[profile] = pre
            decoded_images[profile] = decoded

    make_overlay(source, out_dir / "00_source_crop67_overlay.png")

    make_full_frame_sheet(
        pre_images,
        results,
        "Spatial sweep before HEIC — normalized to the same display size",
        out_dir / "01_pre_heic_full_frame_normalized.png",
    )

    make_full_frame_sheet(
        decoded_images,
        results,
        "Spatial sweep after HEIC Q20 — normalized to the same display size",
        out_dir / "02_decoded_heic_full_frame_normalized.png",
    )

    make_native_detail_sheet(
        pre_images,
        results,
        "Center 30% of the same scene — native pixels, no upscaling",
        out_dir / "03_pre_heic_center_detail_native_pixels.png",
    )

    make_native_detail_sheet(
        decoded_images,
        results,
        "Center 30% after HEIC Q20 — native pixels, no upscaling",
        out_dir / "04_decoded_heic_center_detail_native_pixels.png",
    )

    for profile in PROFILE_ORDER:
        make_pre_vs_heic_sheet(
            profile,
            pre_images[profile],
            decoded_images[profile],
            results[profile],
            out_dir / f"{profile}_pre_vs_heic_100pct.png",
        )

    print(f"PASS: cut sheets written to {out_dir}")
    for path in sorted(out_dir.glob("*.png")):
        print(path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
