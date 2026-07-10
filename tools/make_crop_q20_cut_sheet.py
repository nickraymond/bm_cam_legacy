#!/usr/bin/env python3
"""
make_crop_q20_cut_sheet.py

Create high-quality review sheets for a bmcam reef crop/Q20 sweep.

Expected run structure:
    <run_dir>/
      01_synthetic_native_4608x2592.jpg
      results.csv
      crop_75/
        02_crop_75_2030x1142.jpg
        03_crop_75_q20.heic
      crop_67/
      crop_58/
      crop_50/

Outputs:
    <run_dir>/cut_sheets/
      00_source_crop_overlay.png
      01_pre_heic_crop_overview.png
      02_decoded_heic_crop_overview.png
      crop_75_pre_vs_heic_100pct.png
      crop_67_pre_vs_heic_100pct.png
      crop_58_pre_vs_heic_100pct.png
      crop_50_pre_vs_heic_100pct.png

The overview sheets use the same display dimensions for every profile.
The per-profile pre-vs-HEIC sheets preserve the full 2030x1142 image pixels
with no rescaling.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Tuple

from PIL import Image, ImageDraw, ImageFont


PROFILE_ORDER = ("crop_75", "crop_67", "crop_58", "crop_50")
PROFILE_COLORS = {
    "crop_75": (220, 20, 60),
    "crop_67": (255, 140, 0),
    "crop_58": (30, 144, 255),
    "crop_50": (34, 139, 34),
}


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Helvetica.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def read_results(path: Path) -> Dict[str, dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows found in {path}")
    return {row["profile"]: row for row in rows}


def decode_heic(path: Path, temp_dir: Path) -> Image.Image:
    """Decode HEIC using pillow-heif when available, otherwise macOS sips."""
    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
        with Image.open(path) as image:
            return image.convert("RGB")
    except Exception:
        pass

    sips = shutil.which("sips")
    if not sips:
        raise RuntimeError(
            "Unable to decode HEIC. Install pillow-heif with:\n"
            "  python3 -m pip install pillow-heif\n"
            "or run this tool on macOS where `sips` is available."
        )

    destination = temp_dir / f"{path.stem}_decoded.png"
    result = subprocess.run(
        [sips, "-s", "format", "png", str(path), "--out", str(destination)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(
            f"macOS sips failed to decode {path}:\n{result.stdout}"
        )

    with Image.open(destination) as image:
        return image.convert("RGB")


def fit_image(image: Image.Image, target_width: int, target_height: int) -> Image.Image:
    preview = image.copy()
    preview.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
    return preview


def format_metrics(row: dict) -> str:
    heic_bytes = int(row["heic_bytes"])
    messages = int(row["data_messages"])
    minutes = float(row["pace_minutes"])
    crop_w = int(row["crop_w"])
    crop_h = int(row["crop_h"])
    return (
        f"{row['profile']}  ROI {crop_w}×{crop_h}  "
        f"HEIC {heic_bytes / 1024:.1f} KiB  "
        f"{messages} buffers  {minutes:.2f} min"
    )


def make_overlay(
    source: Image.Image,
    results: Dict[str, dict],
    output_path: Path,
) -> None:
    image = source.convert("RGB").copy()
    draw = ImageDraw.Draw(image)
    font = load_font(44)

    for profile in PROFILE_ORDER:
        row = results[profile]
        x = int(row["crop_x"])
        y = int(row["crop_y"])
        w = int(row["crop_w"])
        h = int(row["crop_h"])
        color = PROFILE_COLORS[profile]
        line_width = 12

        draw.rectangle(
            (x, y, x + w - 1, y + h - 1),
            outline=color,
            width=line_width,
        )
        label_y = y + 16
        label_x = x + 20
        label = f"{profile}: {w}×{h}"
        bbox = draw.textbbox((label_x, label_y), label, font=font)
        draw.rectangle(
            (bbox[0] - 10, bbox[1] - 6, bbox[2] + 10, bbox[3] + 6),
            fill=(255, 255, 255),
        )
        draw.text((label_x, label_y), label, fill=color, font=font)

    image.save(output_path, format="PNG")


def make_overview(
    images: Dict[str, Image.Image],
    results: Dict[str, dict],
    title: str,
    output_path: Path,
) -> None:
    panel_width = 1120
    panel_height = 630
    margin = 40
    gap = 30
    title_height = 90
    label_height = 90

    sheet_width = margin * 2 + panel_width * 2 + gap
    sheet_height = (
        margin * 2
        + title_height
        + (panel_height + label_height) * 2
        + gap
    )

    sheet = Image.new("RGB", (sheet_width, sheet_height), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = load_font(34)
    label_font = load_font(24)

    draw.text((margin, margin), title, fill="black", font=title_font)

    for index, profile in enumerate(PROFILE_ORDER):
        row_index = index // 2
        col_index = index % 2
        x = margin + col_index * (panel_width + gap)
        y = margin + title_height + row_index * (panel_height + label_height + gap)

        image = images[profile]
        if image.size != (2030, 1142):
            raise RuntimeError(
                f"{profile} expected 2030x1142, got {image.size[0]}x{image.size[1]}"
            )

        preview = fit_image(image, panel_width, panel_height)
        image_x = x + (panel_width - preview.width) // 2
        image_y = y + label_height

        draw.text((x, y), format_metrics(results[profile]), fill="black", font=label_font)
        sheet.paste(preview, (image_x, image_y))
        draw.rectangle(
            (
                image_x,
                image_y,
                image_x + preview.width - 1,
                image_y + preview.height - 1,
            ),
            outline="black",
            width=2,
        )

    sheet.save(output_path, format="PNG")


def make_full_size_pair(
    profile: str,
    pre_heic: Image.Image,
    decoded_heic: Image.Image,
    result: dict,
    output_path: Path,
) -> None:
    if pre_heic.size != decoded_heic.size:
        raise RuntimeError(
            f"{profile}: pre-HEIC size {pre_heic.size} differs from "
            f"decoded HEIC size {decoded_heic.size}"
        )

    width, height = pre_heic.size
    margin = 40
    gap = 40
    title_height = 100
    label_height = 55

    sheet_width = margin * 2 + width * 2 + gap
    sheet_height = margin * 2 + title_height + label_height + height

    sheet = Image.new("RGB", (sheet_width, sheet_height), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = load_font(34)
    label_font = load_font(26)

    title = (
        f"{format_metrics(result)} — full-resolution 100% comparison; "
        "no image rescaling"
    )
    draw.text((margin, margin), title, fill="black", font=title_font)

    left_x = margin
    right_x = margin + width + gap
    image_y = margin + title_height + label_height

    draw.text((left_x, margin + title_height), "Pre-HEIC JPEG", fill="black", font=label_font)
    draw.text((right_x, margin + title_height), "Decoded HEIC Q20", fill="black", font=label_font)

    sheet.paste(pre_heic.convert("RGB"), (left_x, image_y))
    sheet.paste(decoded_heic.convert("RGB"), (right_x, image_y))

    sheet.save(output_path, format="PNG")


def locate_profile_files(run_dir: Path, profile: str) -> Tuple[Path, Path]:
    profile_dir = run_dir / profile
    if not profile_dir.is_dir():
        raise FileNotFoundError(f"Missing profile folder: {profile_dir}")

    jpeg_candidates = sorted(profile_dir.glob("02_*.jpg"))
    heic_candidates = sorted(profile_dir.glob("03_*.heic"))

    if len(jpeg_candidates) != 1:
        raise RuntimeError(
            f"Expected one pre-HEIC JPEG in {profile_dir}, found {len(jpeg_candidates)}"
        )
    if len(heic_candidates) != 1:
        raise RuntimeError(
            f"Expected one HEIC in {profile_dir}, found {len(heic_candidates)}"
        )

    return jpeg_candidates[0], heic_candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    source_path = run_dir / "01_synthetic_native_4608x2592.jpg"
    results_path = run_dir / "results.csv"
    output_dir = run_dir / "cut_sheets"

    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not results_path.is_file():
        raise FileNotFoundError(results_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    results = read_results(results_path)

    missing_profiles = [p for p in PROFILE_ORDER if p not in results]
    if missing_profiles:
        raise RuntimeError(f"Missing profiles in results.csv: {missing_profiles}")

    with Image.open(source_path) as image:
        source = image.convert("RGB")

    pre_heic_images: Dict[str, Image.Image] = {}
    decoded_images: Dict[str, Image.Image] = {}

    with tempfile.TemporaryDirectory(prefix="reef_heic_decode_") as temp_name:
        temp_dir = Path(temp_name)

        for profile in PROFILE_ORDER:
            jpeg_path, heic_path = locate_profile_files(run_dir, profile)

            with Image.open(jpeg_path) as image:
                pre_heic_images[profile] = image.convert("RGB")

            decoded_images[profile] = decode_heic(heic_path, temp_dir)

    make_overlay(
        source,
        results,
        output_dir / "00_source_crop_overlay.png",
    )
    make_overview(
        pre_heic_images,
        results,
        "Pre-HEIC crop/downsample outputs — identical 2030×1142 display geometry",
        output_dir / "01_pre_heic_crop_overview.png",
    )
    make_overview(
        decoded_images,
        results,
        "Decoded HEIC Q20 outputs — identical 2030×1142 display geometry",
        output_dir / "02_decoded_heic_crop_overview.png",
    )

    for profile in PROFILE_ORDER:
        make_full_size_pair(
            profile,
            pre_heic_images[profile],
            decoded_images[profile],
            results[profile],
            output_dir / f"{profile}_pre_vs_heic_100pct.png",
        )

    print(f"PASS: cut sheets written to {output_dir}")
    for path in sorted(output_dir.glob("*.png")):
        print(path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
