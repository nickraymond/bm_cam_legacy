#!/usr/bin/env python3
"""
prepare_reference_images.py

Prepare scientist-supplied reference photographs as synthetic IMX708-native inputs.

Workflow:
1. Load image and apply EXIF orientation.
2. Center-crop to the target aspect ratio.
3. Resize to the target sensor dimensions.
4. Save:
   - original_normalized.jpg
   - source_16x9.jpg
   - synthetic_native_4608x2592.jpg
   - preparation_manifest.json
   - comparison_sheet.jpg

Examples
--------
Single image:
    python3 tools/prepare_reference_images.py \
      --input reference_images/P7071008.JPG \
      --output-root reference_images/prepared

Batch folder:
    python3 tools/prepare_reference_images.py \
      --input-dir reference_images \
      --output-root reference_images/prepared

Dependencies
------------
    python3 -m pip install Pillow
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def centered_crop_box(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> tuple[int, int, int, int]:
    """Return a centered crop box matching the target aspect ratio."""
    source_ratio = source_width / source_height
    target_ratio = target_width / target_height

    if abs(source_ratio - target_ratio) < 1e-12:
        return (0, 0, source_width, source_height)

    if source_ratio > target_ratio:
        crop_height = source_height
        crop_width = round(crop_height * target_ratio)
        left = (source_width - crop_width) // 2
        top = 0
    else:
        crop_width = source_width
        crop_height = round(crop_width / target_ratio)
        left = 0
        top = (source_height - crop_height) // 2

    return (left, top, left + crop_width, top + crop_height)


def load_rgb_with_orientation(path: Path) -> tuple[Image.Image, dict]:
    """Load image, apply EXIF orientation, and convert to RGB."""
    with Image.open(path) as raw:
        original_size = raw.size
        original_mode = raw.mode
        exif = raw.getexif()
        exif_orientation = exif.get(274)
        oriented = ImageOps.exif_transpose(raw)
        oriented_size = oriented.size
        rgb = oriented.convert("RGB")

    metadata = {
        "original_size": list(original_size),
        "original_mode": original_mode,
        "exif_orientation": exif_orientation,
        "oriented_size": list(oriented_size),
    }
    return rgb, metadata


def fit_preview(image: Image.Image, max_size: tuple[int, int]) -> Image.Image:
    preview = image.copy()
    preview.thumbnail(max_size, Image.Resampling.LANCZOS)
    return preview


def draw_crop_overlay(
    image: Image.Image,
    crop_box: tuple[int, int, int, int],
    line_width: int = 10,
) -> Image.Image:
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    draw.rectangle(crop_box, outline="red", width=line_width)
    return overlay


def make_comparison_sheet(
    original: Image.Image,
    crop_overlay: Image.Image,
    cropped: Image.Image,
    synthetic: Image.Image,
    destination: Path,
    source_name: str,
) -> None:
    """Create a visual review sheet. This sheet is for framing inspection."""
    panel_max = (1000, 700)
    panels = [
        ("Original, EXIF-corrected", fit_preview(original, panel_max)),
        ("Original with 16:9 crop box", fit_preview(crop_overlay, panel_max)),
        ("16:9 crop", fit_preview(cropped, panel_max)),
        ("Synthetic native 4608×2592", fit_preview(synthetic, panel_max)),
    ]

    label_height = 54
    margin = 30
    gap = 30

    panel_width = max(img.width for _, img in panels)
    panel_height = max(img.height for _, img in panels)

    sheet_width = margin * 2 + panel_width * 2 + gap
    sheet_height = margin * 2 + 70 + (panel_height + label_height) * 2 + gap

    sheet = Image.new("RGB", (sheet_width, sheet_height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    draw.text((margin, margin), f"Reference image preparation: {source_name}", fill="black", font=font)

    for index, (label, panel) in enumerate(panels):
        row = index // 2
        col = index % 2
        x = margin + col * (panel_width + gap)
        y = margin + 70 + row * (panel_height + label_height + gap)

        draw.text((x, y), label, fill="black", font=font)

        image_x = x + (panel_width - panel.width) // 2
        image_y = y + label_height
        sheet.paste(panel, (image_x, image_y))
        draw.rectangle(
            (image_x, image_y, image_x + panel.width - 1, image_y + panel.height - 1),
            outline="black",
            width=1,
        )

    sheet.save(destination, format="JPEG", quality=95, subsampling=0)


def process_image(
    source_path: Path,
    output_root: Path,
    target_width: int,
    target_height: int,
    jpeg_quality: int,
    overwrite: bool,
) -> Path:
    run_dir = output_root / source_path.stem

    if run_dir.exists() and not overwrite:
        raise FileExistsError(
            f"Output folder already exists: {run_dir}\n"
            "Use --overwrite to replace generated files."
        )

    run_dir.mkdir(parents=True, exist_ok=True)

    original, source_metadata = load_rgb_with_orientation(source_path)
    crop_box = centered_crop_box(
        original.width,
        original.height,
        target_width,
        target_height,
    )

    cropped = original.crop(crop_box)
    synthetic = cropped.resize(
        (target_width, target_height),
        Image.Resampling.LANCZOS,
    )
    crop_overlay = draw_crop_overlay(original, crop_box)

    original_output = run_dir / "original_normalized.jpg"
    cropped_output = run_dir / "source_16x9.jpg"
    synthetic_output = run_dir / f"synthetic_native_{target_width}x{target_height}.jpg"
    comparison_output = run_dir / "comparison_sheet.jpg"
    manifest_output = run_dir / "preparation_manifest.json"

    original.save(
        original_output,
        format="JPEG",
        quality=jpeg_quality,
        subsampling=0,
        optimize=True,
    )
    cropped.save(
        cropped_output,
        format="JPEG",
        quality=jpeg_quality,
        subsampling=0,
        optimize=True,
    )
    synthetic.save(
        synthetic_output,
        format="JPEG",
        quality=jpeg_quality,
        subsampling=0,
        optimize=True,
    )

    make_comparison_sheet(
        original=original,
        crop_overlay=crop_overlay,
        cropped=cropped,
        synthetic=synthetic,
        destination=comparison_output,
        source_name=source_path.name,
    )

    manifest = {
        "tool": "prepare_reference_images.py",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(source_path.resolve()),
            "filename": source_path.name,
            "bytes": source_path.stat().st_size,
            "sha256": sha256_file(source_path),
            **source_metadata,
        },
        "transformation": {
            "crop_mode": "center",
            "target_aspect_ratio": f"{target_width}:{target_height}",
            "crop_box_left_top_right_bottom": list(crop_box),
            "cropped_size": [cropped.width, cropped.height],
            "resize_method": "Pillow Image.Resampling.LANCZOS",
            "target_size": [target_width, target_height],
            "jpeg_quality": jpeg_quality,
            "jpeg_subsampling": 0,
        },
        "outputs": {
            "original_normalized": {
                "path": original_output.name,
                "size": list(original.size),
                "bytes": original_output.stat().st_size,
            },
            "source_16x9": {
                "path": cropped_output.name,
                "size": list(cropped.size),
                "bytes": cropped_output.stat().st_size,
            },
            "synthetic_native": {
                "path": synthetic_output.name,
                "size": list(synthetic.size),
                "bytes": synthetic_output.stat().st_size,
            },
            "comparison_sheet": {
                "path": comparison_output.name,
                "bytes": comparison_output.stat().st_size,
            },
        },
    }

    manifest_output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Final validation
    with Image.open(synthetic_output) as check:
        if check.size != (target_width, target_height):
            raise RuntimeError(
                f"Validation failed: expected {(target_width, target_height)}, "
                f"got {check.size}"
            )

    return run_dir


def find_images(input_dir: Path, output_root: Path) -> Iterable[Path]:
    output_root_resolved = output_root.resolve()
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            path.resolve().relative_to(output_root_resolved)
            continue
        except ValueError:
            pass
        yield path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Center-crop reference photographs to the target sensor aspect ratio "
            "and resize them into synthetic native camera inputs."
        )
    )

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--input",
        type=Path,
        help="Single source image.",
    )
    source_group.add_argument(
        "--input-dir",
        type=Path,
        help="Process all supported images recursively in this folder.",
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("reference_images/prepared"),
        help="Root output folder. Default: reference_images/prepared",
    )
    parser.add_argument(
        "--target-width",
        type=int,
        default=4608,
        help="Synthetic sensor width. Default: 4608",
    )
    parser.add_argument(
        "--target-height",
        type=int,
        default=2592,
        help="Synthetic sensor height. Default: 2592",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=95,
        help="JPEG quality for prepared artifacts. Default: 95",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing generated files.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.target_width <= 0 or args.target_height <= 0:
        print("ERROR: target dimensions must be positive.", file=sys.stderr)
        return 2

    if not 1 <= args.jpeg_quality <= 100:
        print("ERROR: --jpeg-quality must be between 1 and 100.", file=sys.stderr)
        return 2

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if args.input:
        sources = [args.input.resolve()]
    else:
        input_dir = args.input_dir.resolve()
        if not input_dir.is_dir():
            print(f"ERROR: input directory not found: {input_dir}", file=sys.stderr)
            return 2
        sources = list(find_images(input_dir, output_root))

    if not sources:
        print("ERROR: no supported images found.", file=sys.stderr)
        return 2

    failures = 0

    for source in sources:
        print(f"\n[PREP] Source: {source}")
        if not source.is_file():
            print(f"[FAIL] File not found: {source}", file=sys.stderr)
            failures += 1
            continue

        try:
            run_dir = process_image(
                source_path=source,
                output_root=output_root,
                target_width=args.target_width,
                target_height=args.target_height,
                jpeg_quality=args.jpeg_quality,
                overwrite=args.overwrite,
            )
            print(f"[PASS] Output: {run_dir}")
            print(
                f"[PASS] Synthetic native: "
                f"{run_dir / f'synthetic_native_{args.target_width}x{args.target_height}.jpg'}"
            )
        except Exception as exc:
            print(f"[FAIL] {source.name}: {exc}", file=sys.stderr)
            failures += 1

    if failures:
        print(f"\nCompleted with {failures} failure(s).", file=sys.stderr)
        return 1

    print(f"\nCompleted successfully: {len(sources)} image(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
