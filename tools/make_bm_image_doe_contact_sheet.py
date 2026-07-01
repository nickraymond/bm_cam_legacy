#!/usr/bin/env python3
"""
Make local contact sheets for BM image quality DOE results.

Runs on Mac/desktop after scp'ing a DOE run folder from the Pi.
Requires:
  python3 -m pip install pillow pillow-heif

Examples:
  python3 tools/make_bm_image_doe_contact_sheet.py \
    --results-csv ~/Downloads/bmcam001_smoke/results.csv \
    --output ~/Downloads/bmcam001_smoke/contact_heic_decoded.jpg \
    --export-jpeg-roundtrip

The default sheet shows HEIC files decoded for visual comparison. With
--export-jpeg-roundtrip, it also writes JPEG versions of each HEIC and creates a
second contact sheet using the JPEG round-trip files.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import pillow_heif
    from PIL import Image, ImageDraw, ImageFont
except Exception as exc:
    raise SystemExit(
        "Missing dependency. Install with: python3 -m pip install pillow pillow-heif\n"
        f"Original error: {exc}"
    )

pillow_heif.register_heif_opener()


def load_rows(csv_path: Path) -> list[dict[str, Any]]:
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def resolve_path(raw_path: str, csv_dir: Path) -> Path:
    p = Path(raw_path)
    if p.exists():
        return p
    # When copied from Pi to Mac, absolute /home/pi/... paths will not exist.
    # Fall back to the local images folder using the filename.
    local = csv_dir / "images" / p.name
    if local.exists():
        return local
    local2 = csv_dir / p.name
    if local2.exists():
        return local2
    return p


def font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def format_kb(value: Any) -> str:
    try:
        return f"{float(value):.1f} KB"
    except Exception:
        return "—"


def draw_centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt, fill=(25, 40, 55)):
    x0, y0, x1, y1 = box
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((x0 + (x1 - x0 - tw) / 2, y0 + (y1 - y0 - th) / 2), text, font=fnt, fill=fill)


def load_tile_image(path: Path, tile_w: int, image_h: int) -> Image.Image:
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((tile_w, image_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (tile_w, image_h), (230, 236, 242))
        x = (tile_w - img.width) // 2
        y = (image_h - img.height) // 2
        canvas.paste(img, (x, y))
        return canvas


def make_sheet(
    rows: list[dict[str, Any]],
    *,
    csv_dir: Path,
    output: Path,
    image_field: str = "heic_path",
    title: str = "BM image DOE contact sheet",
    tile_w: int = 360,
    image_h: int = 270,
    label_h: int = 86,
):
    if not rows:
        raise ValueError("No rows found")

    # Sort rows by resolution then quality.
    rows = sorted(rows, key=lambda r: (r.get("hostname", ""), r.get("resolution_key", ""), int(r.get("quality") or 0)))
    qualities = sorted({int(r.get("quality") or 0) for r in rows})
    res_keys = []
    for r in rows:
        key = r.get("resolution_key") or "unknown"
        if key not in res_keys:
            res_keys.append(key)

    by_res_quality = {(r.get("resolution_key"), int(r.get("quality") or 0)): r for r in rows}

    margin = 24
    row_label_w = 120
    header_h = 90
    quality_header_h = 38
    tile_h = image_h + label_h
    cols = len(qualities)
    rows_n = len(res_keys)
    width = margin * 2 + row_label_w + cols * tile_w
    height = margin * 2 + header_h + quality_header_h + rows_n * tile_h

    sheet = Image.new("RGB", (width, height), (244, 247, 251))
    draw = ImageDraw.Draw(sheet)
    f_title = font(24, bold=True)
    f_head = font(16, bold=True)
    f_label = font(13, bold=False)
    f_small = font(12, bold=False)

    hostname = rows[0].get("hostname", "unknown")
    run_id = rows[0].get("run_id", "")
    draw.text((margin, margin), title, font=f_title, fill=(30, 50, 70))
    draw.text((margin, margin + 34), f"host={hostname}  run={run_id}", font=f_label, fill=(80, 100, 120))
    draw.text((margin, margin + 56), f"source={Path(image_field).name if image_field else 'heic'}", font=f_small, fill=(100, 115, 130))

    y0 = margin + header_h
    # Column quality headers
    for col, q in enumerate(qualities):
        x = margin + row_label_w + col * tile_w
        draw.rectangle((x, y0, x + tile_w - 4, y0 + quality_header_h - 4), fill=(255, 255, 255), outline=(205, 217, 228))
        draw_centered_text(draw, (x, y0, x + tile_w - 4, y0 + quality_header_h - 4), f"q{q:03d}", f_head)

    y = y0 + quality_header_h
    for res_key in res_keys:
        draw.rectangle((margin, y, margin + row_label_w - 8, y + tile_h - 8), fill=(255, 255, 255), outline=(205, 217, 228))
        draw_centered_text(draw, (margin, y, margin + row_label_w - 8, y + tile_h - 8), res_key, f_head)

        for col, q in enumerate(qualities):
            x = margin + row_label_w + col * tile_w
            r = by_res_quality.get((res_key, q))
            draw.rectangle((x, y, x + tile_w - 4, y + tile_h - 8), fill=(255, 255, 255), outline=(205, 217, 228))
            if not r:
                draw_centered_text(draw, (x, y, x + tile_w - 4, y + image_h), "missing", f_label, fill=(170, 80, 80))
                continue

            path = resolve_path(r.get(image_field) or r.get("heic_path") or "", csv_dir)
            if path.exists():
                try:
                    tile = load_tile_image(path, tile_w - 16, image_h - 12)
                    sheet.paste(tile, (x + 8, y + 6))
                except Exception as exc:
                    draw_centered_text(draw, (x, y, x + tile_w - 4, y + image_h), f"open failed\n{exc}", f_small, fill=(170, 80, 80))
            else:
                draw_centered_text(draw, (x, y, x + tile_w - 4, y + image_h), f"file not found\n{path.name}", f_small, fill=(170, 80, 80))

            label_y = y + image_h
            lines = [
                f"{r.get('width_px')}×{r.get('height_px')}  HEIC {format_kb(r.get('heic_size_kb'))}",
                f"est buffers: {r.get('estimated_bm_buffers', '—')}  b64 chars: {r.get('base64_chars', '—')}",
                f"source: {format_kb(r.get('source_size_kb'))}",
            ]
            if image_field == "jpeg_roundtrip_path":
                lines[0] = f"JPEG roundtrip {format_kb(r.get('jpeg_roundtrip_size_kb'))}"
                lines.append(f"from HEIC {format_kb(r.get('heic_size_kb'))}")
            for i, line in enumerate(lines):
                draw.text((x + 10, label_y + 8 + i * 18), line, font=f_small, fill=(45, 65, 85))

        y += tile_h

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=92)
    print(f"Wrote {output}")


def export_jpeg_roundtrip(rows: list[dict[str, Any]], csv_dir: Path, quality: int) -> list[dict[str, Any]]:
    out_dir = csv_dir / f"jpeg_roundtrip_q{quality:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for r in rows:
        rr = dict(r)
        heic_path = resolve_path(r.get("heic_path") or "", csv_dir)
        jpeg_name = Path(r.get("heic_filename") or heic_path.name).with_suffix(".jpg").name
        jpeg_path = out_dir / jpeg_name
        try:
            with Image.open(heic_path) as img:
                img.convert("RGB").save(jpeg_path, format="JPEG", quality=quality, optimize=True)
            rr["jpeg_roundtrip_path"] = str(jpeg_path)
            rr["jpeg_roundtrip_size_bytes"] = jpeg_path.stat().st_size
            rr["jpeg_roundtrip_size_kb"] = round(jpeg_path.stat().st_size / 1024, 3)
        except Exception as exc:
            rr["jpeg_roundtrip_path"] = ""
            rr["jpeg_roundtrip_error"] = str(exc)
        out_rows.append(rr)
    return out_rows


def write_augmented_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    fieldnames = []
    for r in rows:
        for k in r.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {path}")


def parse_args():
    p = argparse.ArgumentParser(description="Create contact sheets from BM image DOE results.csv")
    p.add_argument("--results-csv", required=True, help="Path to DOE results.csv copied from Pi")
    p.add_argument("--output", default=None, help="Output contact sheet JPEG path")
    p.add_argument("--tile-width", type=int, default=360)
    p.add_argument("--image-height", type=int, default=270)
    p.add_argument("--export-jpeg-roundtrip", action="store_true", help="Also export HEIC-decoded JPEGs and make a JPEG roundtrip contact sheet")
    p.add_argument("--jpeg-quality", type=int, default=95, help="JPEG quality for round-trip exports")
    return p.parse_args()


def main():
    args = parse_args()
    csv_path = Path(args.results_csv).expanduser().resolve()
    csv_dir = csv_path.parent
    rows = load_rows(csv_path)
    output = Path(args.output).expanduser().resolve() if args.output else csv_dir / "contact_heic_decoded.jpg"

    make_sheet(
        rows,
        csv_dir=csv_dir,
        output=output,
        image_field="heic_path",
        title="BM image DOE · HEIC decoded view",
        tile_w=args.tile_width,
        image_h=args.image_height,
    )

    if args.export_jpeg_roundtrip:
        round_rows = export_jpeg_roundtrip(rows, csv_dir, args.jpeg_quality)
        write_augmented_csv(round_rows, csv_dir / f"results_with_jpeg_roundtrip_q{args.jpeg_quality:03d}.csv")
        make_sheet(
            round_rows,
            csv_dir=csv_dir,
            output=csv_dir / f"contact_jpeg_roundtrip_q{args.jpeg_quality:03d}.jpg",
            image_field="jpeg_roundtrip_path",
            title=f"BM image DOE · HEIC decoded → JPEG q{args.jpeg_quality}",
            tile_w=args.tile_width,
            image_h=args.image_height,
        )


if __name__ == "__main__":
    main()
