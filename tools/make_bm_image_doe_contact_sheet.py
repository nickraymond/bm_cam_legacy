#!/usr/bin/env python3
"""
Make local contact sheets for BM image quality DOE results.

Runs on Mac/desktop after scp'ing a DOE run folder from the Pi.
Requires:
  python3 -m pip install pillow pillow-heif

Examples:
  python3 tools/make_bm_image_doe_contact_sheet.py \
    --results-csv ~/Downloads/bmcam001_smoke/results.csv \
    --export-jpeg-roundtrip \
    --make-source-quality-matrix

Outputs by default:
  contact_heic_decoded.jpg
  contact_jpeg_roundtrip_q095.jpg                 if --export-jpeg-roundtrip
  contact_source_vs_quality_matrix_q010_q075.jpg  if --make-source-quality-matrix

The source-vs-quality matrix is the quick 2x2 view for comparing:
  rows: source JPEG vs source PNG
  cols: HEIC quality 10 vs 75
"""

from __future__ import annotations

import argparse
import csv
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
    p = Path(raw_path or "")
    if p.exists():
        return p
    # When copied from Pi to Mac, absolute /home/pi/... paths will not exist.
    # Fall back to the local images folder using the filename.
    if p.name:
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


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def draw_centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt, fill=(25, 40, 55)):
    x0, y0, x1, y1 = box
    lines = str(text).splitlines() or [""]
    line_heights = []
    line_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=fnt)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    total_h = sum(line_heights) + max(0, len(lines) - 1) * 4
    y = y0 + (y1 - y0 - total_h) / 2
    for line, tw, th in zip(lines, line_widths, line_heights):
        draw.text((x0 + (x1 - x0 - tw) / 2, y), line, font=fnt, fill=fill)
        y += th + 4


def load_tile_image(path: Path, tile_w: int, image_h: int, *, no_upscale: bool = True) -> Image.Image:
    with Image.open(path) as img:
        img = img.convert("RGB")
        if no_upscale:
            scale = min(tile_w / img.width, image_h / img.height, 1.0)
            new_w = max(1, int(img.width * scale))
            new_h = max(1, int(img.height * scale))
            if (new_w, new_h) != img.size:
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            img.thumbnail((tile_w, image_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (tile_w, image_h), (230, 236, 242))
        x = (tile_w - img.width) // 2
        y = (image_h - img.height) // 2
        canvas.paste(img, (x, y))
        return canvas


def row_label(row: dict[str, Any]) -> str:
    res = row.get("resolution_key") or "unknown"
    src = row.get("source_mode") or "source"
    return f"{res}\nsrc-{src}"


def sorted_resolution_keys(rows: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for r in rows:
        key = r.get("resolution_key") or "unknown"
        if key not in keys:
            keys.append(key)
    return keys


def sorted_source_modes(rows: list[dict[str, Any]]) -> list[str]:
    preferred = ["jpeg", "png"]
    present = {r.get("source_mode") or "unknown" for r in rows}
    out = [m for m in preferred if m in present]
    out.extend(sorted(present - set(out)))
    return out


def make_sheet(
    rows: list[dict[str, Any]],
    *,
    csv_dir: Path,
    output: Path,
    image_field: str = "heic_path",
    title: str = "BM image DOE contact sheet",
    tile_w: int = 360,
    image_h: int = 270,
    label_h: int = 100,
):
    if not rows:
        raise ValueError("No rows found")

    rows = sorted(
        rows,
        key=lambda r: (
            r.get("hostname", ""),
            r.get("resolution_key", ""),
            r.get("source_mode", ""),
            to_int(r.get("quality")),
        ),
    )
    qualities = sorted({to_int(r.get("quality")) for r in rows})

    row_keys: list[tuple[str, str]] = []
    for r in rows:
        key = (r.get("resolution_key") or "unknown", r.get("source_mode") or "source")
        if key not in row_keys:
            row_keys.append(key)

    by_key = {
        (r.get("resolution_key") or "unknown", r.get("source_mode") or "source", to_int(r.get("quality"))): r
        for r in rows
    }

    margin = 24
    row_label_w = 160
    header_h = 92
    quality_header_h = 38
    tile_h = image_h + label_h
    cols = len(qualities)
    rows_n = len(row_keys)
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
    draw.text((margin, margin + 56), f"image_field={image_field}", font=f_small, fill=(100, 115, 130))

    y0 = margin + header_h
    for col, q in enumerate(qualities):
        x = margin + row_label_w + col * tile_w
        draw.rectangle((x, y0, x + tile_w - 4, y0 + quality_header_h - 4), fill=(255, 255, 255), outline=(205, 217, 228))
        draw_centered_text(draw, (x, y0, x + tile_w - 4, y0 + quality_header_h - 4), f"HEIC q{q:03d}", f_head)

    y = y0 + quality_header_h
    for res_key, source_mode in row_keys:
        draw.rectangle((margin, y, margin + row_label_w - 8, y + tile_h - 8), fill=(255, 255, 255), outline=(205, 217, 228))
        draw_centered_text(draw, (margin, y, margin + row_label_w - 8, y + tile_h - 8), f"{res_key}\nsrc-{source_mode}", f_head)

        for col, q in enumerate(qualities):
            x = margin + row_label_w + col * tile_w
            r = by_key.get((res_key, source_mode, q))
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
            if image_field == "jpeg_roundtrip_path":
                lines = [
                    f"{r.get('width_px')}×{r.get('height_px')}  JPEG {format_kb(r.get('jpeg_roundtrip_size_kb'))}",
                    f"from HEIC {format_kb(r.get('heic_size_kb'))}  buffers: {r.get('estimated_bm_buffers', '—')}",
                    f"source {r.get('source_mode')}: {format_kb(r.get('source_size_kb'))}",
                ]
            else:
                lines = [
                    f"{r.get('width_px')}×{r.get('height_px')}  HEIC {format_kb(r.get('heic_size_kb'))}",
                    f"est buffers: {r.get('estimated_bm_buffers', '—')}  b64 chars: {r.get('base64_chars', '—')}",
                    f"source {r.get('source_mode')}: {format_kb(r.get('source_size_kb'))}",
                ]
            for i, line in enumerate(lines):
                draw.text((x + 10, label_y + 8 + i * 18), line, font=f_small, fill=(45, 65, 85))

        y += tile_h

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=92)
    print(f"Wrote {output}")


def make_source_quality_matrix(
    rows: list[dict[str, Any]],
    *,
    csv_dir: Path,
    output: Path,
    matrix_qualities: list[int],
    tile_w: int = 560,
    image_h: int = 360,
    label_h: int = 92,
):
    """Create a compact source-mode vs HEIC-quality matrix.

    For each resolution, rows are source modes and columns are selected quality values.
    With default inputs this is a 2x2 block: JPEG vs PNG, q10 vs q75.
    """
    if not rows:
        raise ValueError("No rows found")

    qualities = [to_int(q) for q in matrix_qualities]
    res_keys = sorted_resolution_keys(rows)
    source_modes = sorted_source_modes(rows)
    by_key = {
        (r.get("resolution_key") or "unknown", r.get("source_mode") or "source", to_int(r.get("quality"))): r
        for r in rows
    }

    margin = 24
    row_label_w = 150
    header_h = 96
    block_gap = 34
    resolution_label_h = 34
    quality_header_h = 38
    tile_h = image_h + label_h
    block_h = resolution_label_h + quality_header_h + len(source_modes) * tile_h
    width = margin * 2 + row_label_w + len(qualities) * tile_w
    height = margin * 2 + header_h + len(res_keys) * block_h + max(0, len(res_keys) - 1) * block_gap

    sheet = Image.new("RGB", (width, height), (244, 247, 251))
    draw = ImageDraw.Draw(sheet)
    f_title = font(24, bold=True)
    f_head = font(16, bold=True)
    f_label = font(13, bold=False)
    f_small = font(12, bold=False)

    hostname = rows[0].get("hostname", "unknown")
    run_id = rows[0].get("run_id", "")
    draw.text((margin, margin), "BM image DOE · source mode vs HEIC quality", font=f_title, fill=(30, 50, 70))
    draw.text((margin, margin + 34), f"host={hostname}  run={run_id}", font=f_label, fill=(80, 100, 120))
    draw.text((margin, margin + 56), "Rows compare starting source. Columns compare HEIC quality.", font=f_small, fill=(100, 115, 130))

    y = margin + header_h
    for res_key in res_keys:
        draw.rectangle((margin, y, width - margin, y + resolution_label_h - 6), fill=(226, 235, 244), outline=(205, 217, 228))
        draw.text((margin + 10, y + 6), f"Resolution: {res_key}", font=f_head, fill=(30, 50, 70))
        y += resolution_label_h

        for col, q in enumerate(qualities):
            x = margin + row_label_w + col * tile_w
            draw.rectangle((x, y, x + tile_w - 4, y + quality_header_h - 4), fill=(255, 255, 255), outline=(205, 217, 228))
            draw_centered_text(draw, (x, y, x + tile_w - 4, y + quality_header_h - 4), f"HEIC q{q:03d}", f_head)
        y += quality_header_h

        for source_mode in source_modes:
            draw.rectangle((margin, y, margin + row_label_w - 8, y + tile_h - 8), fill=(255, 255, 255), outline=(205, 217, 228))
            draw_centered_text(draw, (margin, y, margin + row_label_w - 8, y + tile_h - 8), f"src-{source_mode}", f_head)

            for col, q in enumerate(qualities):
                x = margin + row_label_w + col * tile_w
                r = by_key.get((res_key, source_mode, q))
                draw.rectangle((x, y, x + tile_w - 4, y + tile_h - 8), fill=(255, 255, 255), outline=(205, 217, 228))
                if not r:
                    draw_centered_text(draw, (x, y, x + tile_w - 4, y + image_h), "missing", f_label, fill=(170, 80, 80))
                    continue

                path = resolve_path(r.get("heic_path") or "", csv_dir)
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
                    f"source {source_mode}: {format_kb(r.get('source_size_kb'))}",
                ]
                for i, line in enumerate(lines):
                    draw.text((x + 10, label_y + 8 + i * 18), line, font=f_small, fill=(45, 65, 85))

            y += tile_h

        y += block_gap

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
        stem = Path(r.get("heic_filename") or heic_path.name).with_suffix("").name
        jpeg_name = f"{stem}_jpeg-roundtrip-q{quality:03d}.jpg"
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
    fieldnames: list[str] = []
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
    p.add_argument("--tile-width", type=int, default=420)
    p.add_argument("--image-height", type=int, default=300)
    p.add_argument("--export-jpeg-roundtrip", action="store_true", help="Also export HEIC-decoded JPEGs and make a JPEG roundtrip contact sheet")
    p.add_argument("--jpeg-quality", type=int, default=95, help="JPEG quality for round-trip exports")
    p.add_argument("--make-source-quality-matrix", action="store_true", help="Create source-mode vs selected HEIC quality matrix contact sheet")
    p.add_argument("--matrix-qualities", nargs="+", type=int, default=[10, 75], help="HEIC qualities for source-vs-quality matrix")
    p.add_argument("--matrix-tile-width", type=int, default=720, help="Tile width for source-vs-quality matrix")
    p.add_argument("--matrix-image-height", type=int, default=460, help="Image height for source-vs-quality matrix")
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

    round_rows: list[dict[str, Any]] | None = None
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

    if args.make_source_quality_matrix:
        q_name = "_".join(f"q{int(q):03d}" for q in args.matrix_qualities)
        make_source_quality_matrix(
            rows,
            csv_dir=csv_dir,
            output=csv_dir / f"contact_source_vs_quality_matrix_{q_name}.jpg",
            matrix_qualities=args.matrix_qualities,
            tile_w=args.matrix_tile_width,
            image_h=args.matrix_image_height,
        )


if __name__ == "__main__":
    main()
