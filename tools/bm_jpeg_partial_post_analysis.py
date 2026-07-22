#!/usr/bin/env python3
"""
BM JPEG Partial-Transmission Post-Analysis (Sprint 06 P2)

Aggregates the per-source subruns of bm_reference_card_jpeg_partial_sweep.py
(one subrun per source because card/coral use different ROI centers) and
builds the P2 deliverables the sweep itself does not produce:

  1. combined_results_p2_partial.csv — all subrun rows + a `source` column.
  2. Baseline-vs-progressive comparison sheets, one per (source, quality):
     rows = received fraction, columns = baseline | progressive decoded
     frames. Tiles are display-normalized (NOT 1:1 pixels).
  3. Metric-vs-received-% curve panels, one per source (PIL-drawn; the venv
     has no matplotlib, matching bm_heic_sweep_post_analysis.py):
       card   : tag_count and tag_side_px_min vs received %
       coral  : ref_psnr_rgb and ff_laplacian_var vs received %
     One series per (mode, quality): baseline = blues, progressive =
     oranges, darker = higher quality. Cells with no scoreable metric
     (rejected/failed decode) are drawn as an X at y=panel floor.
  4. post_manifest.json + post_analysis_log.txt.

Inputs
------
A parent folder whose immediate subfolders are sweep run folders, each
containing results/results_jpeg_partial_sweep.csv and decoded/<label>/*.png.
Subfolder name = source label (e.g. card, alt_03, alt_07).

Example (Sprint06 P2)
---------------------
  .venv/bin/python3 tools/bm_jpeg_partial_post_analysis.py \
      --parent ~/Downloads/bm_jpeg_partial_sweep/p2_partial_<UTC>

Assumptions / known limitations
-------------------------------
  - Metrics are read from the sweep CSVs, never recomputed; a blank metric
    means the sweep rejected that decode (see recovered_status).
  - Full-frame PSNR on a baseline partial includes libjpeg's gray fill —
    that is the intended "delivered image" score.
  - Sheets/curves assume every subrun swept the same modes/qualities/
    fractions grid; missing cells are labeled, not errors.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

RESULTS_REL = Path("results") / "results_jpeg_partial_sweep.csv"

# Series palette: mode family x quality shade (darker = higher quality).
BASELINE_SHADES = [(120, 175, 230), (60, 120, 200), (20, 70, 155)]
PROGRESSIVE_SHADES = [(250, 180, 110), (235, 130, 60), (195, 80, 20)]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class RunLog:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.path: Optional[Path] = None

    def attach(self, path: Path) -> None:
        self.path = path
        if self.lines:
            path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"[p2-post] {msg}"
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


def fnum(row: Dict[str, str], key: str) -> Optional[float]:
    try:
        v = row.get(key, "")
        return float(v) if v not in ("", None) else None
    except (TypeError, ValueError):
        return None


# -----------------------------------------------------------------------------
# Load + combine
# -----------------------------------------------------------------------------


def discover_sources(parent: Path) -> List[Tuple[str, Path]]:
    found = []
    for sub in sorted(p for p in parent.iterdir() if p.is_dir()):
        if (sub / RESULTS_REL).is_file():
            found.append((sub.name, sub))
    return found


def load_rows(parent: Path, sources: List[Tuple[str, Path]]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for name, sub in sources:
        with (sub / RESULTS_REL).open("r", newline="", encoding="utf-8") as f:
            sub_rows = list(csv.DictReader(f))
        for r in sub_rows:
            r["source"] = name
        log(f"loaded {name}: {len(sub_rows)} rows")
        rows.extend(sub_rows)
    return rows


def write_combined_csv(rows: List[Dict[str, str]], out_path: Path) -> None:
    fields = ["source"]
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    log(f"combined CSV: {out_path} ({len(rows)} rows)")


# -----------------------------------------------------------------------------
# Baseline-vs-progressive comparison sheets
# -----------------------------------------------------------------------------


def status_fill(status: str) -> Tuple[int, int, int]:
    return {
        "PASS": (220, 245, 226),
        "WARN": (255, 244, 210),
        "FAIL": (255, 224, 224),
        "REJECTED_LOW_RECOVERY": (255, 224, 224),
        "DECODE_FAIL": (240, 220, 240),
    }.get(status, (235, 238, 242))


def row_status(r: Dict[str, str]) -> str:
    return r.get("sprint_status") or r.get("recovered_status") or ""


def make_mode_compare_sheet(
    rows: List[Dict[str, str]],
    source: str,
    quality: int,
    fractions: Sequence[int],
    out_path: Path,
    subtitle: str,
) -> None:
    """Rows = received fraction, columns = baseline | progressive."""
    f_title, f_sub, f_head, f_small = pil_font(26, True), pil_font(14), pil_font(15, True), pil_font(12)
    tile_img = (420, 236)
    label_h = 118
    margin, gap = 24, 14
    header_h = 84 + 26
    modes = ["baseline", "progressive"]
    cell: Dict[Tuple[str, int], Dict[str, str]] = {
        (r["mode"], int(r["received_fraction_pct"])): r
        for r in rows
        if int(r["jpeg_quality"]) == quality
    }
    row_h = tile_img[1] + label_h
    sheet_w = margin * 2 + 88 + len(modes) * (tile_img[0] + gap)
    sheet_h = margin * 2 + header_h + len(fractions) * (row_h + gap)
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    d.text((margin, margin), f"Baseline vs progressive under tail-loss: {source} q{quality}", font=f_title, fill=(30, 50, 70))
    d.text((margin, margin + 34), subtitle + "  |  tiles display-normalized (not 1:1)", font=f_sub, fill=(90, 100, 110))
    for mi, mode in enumerate(modes):
        x = margin + 88 + mi * (tile_img[0] + gap)
        d.text((x, margin + 66), mode.upper(), font=f_head, fill=(60, 75, 95))

    for fi, frac in enumerate(fractions):
        y = margin + header_h + fi * (row_h + gap)
        d.text((margin, y + tile_img[1] // 2 - 10), f"{frac}%\nrecv", font=f_head, fill=(60, 75, 95))
        for mi, mode in enumerate(modes):
            x = margin + 88 + mi * (tile_img[0] + gap)
            r = cell.get((mode, frac))
            if r is None:
                d.text((x + 10, y + 20), "cell not swept", font=f_small, fill=(120, 40, 40))
                continue
            status = row_status(r)
            d.rectangle((x - 4, y - 4, x + tile_img[0] + 4, y + row_h - 6), fill=status_fill(status), outline=(200, 208, 218))
            img_path = Path(r.get("decoded_path") or "")
            if img_path.is_file():
                with Image.open(img_path) as im:
                    im = im.convert("RGB")
                    im.thumbnail(tile_img, Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", tile_img, (230, 235, 240))
                    canvas.paste(im, ((tile_img[0] - im.width) // 2, (tile_img[1] - im.height) // 2))
                    sheet.paste(canvas, (x, y))
            else:
                d.text((x + 10, y + 20), "no decoded frame", font=f_small, fill=(120, 40, 40))
            tags = r.get("tag_count", "")
            tag_txt = f"tags={tags} min_tag_px={r.get('tag_side_px_min', '')}" if tags not in ("", None) else "no tags on this image"
            lines = [
                f"{status}  kept {r.get('messages_kept')}/{r.get('messages_total')} msgs  recovered~{r.get('recovered_fraction_est')}",
                f"full file: {r.get('jpeg_kb')} KB  b64={r.get('base64_len')}  msgs={r.get('message_count')} ({r.get('est_minutes')} min, {r.get('duration_band')})",
                f"PSNR={r.get('ref_psnr_rgb')}  ff_sharp={r.get('ff_laplacian_var')}",
                tag_txt,
            ]
            for j, line in enumerate(lines):
                d.text((x + 4, y + tile_img[1] + 8 + j * 21), line, font=f_small, fill=(35, 50, 65))
    sheet.save(out_path, quality=94)
    log(f"mode-compare sheet: {out_path}")


# -----------------------------------------------------------------------------
# Metric-vs-received-% curves (pure PIL; no matplotlib in the venv)
# -----------------------------------------------------------------------------


def nice_ceiling(v: float) -> float:
    if v <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def draw_marker(d: ImageDraw.ImageDraw, x: float, y: float, mode: str, color) -> None:
    r = 5
    if mode == "baseline":
        d.ellipse((x - r, y - r, x + r, y + r), fill=color, outline=(255, 255, 255))
    else:
        d.rectangle((x - r, y - r, x + r, y + r), fill=color, outline=(255, 255, 255))


def draw_panel(
    d: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    title: str,
    fractions: Sequence[int],
    series: List[Tuple[str, int, Tuple[int, int, int], List[Optional[float]]]],
    y_max_hint: Optional[float] = None,
) -> None:
    """One axes panel. series = [(mode, quality, color, values-per-fraction)]."""
    x0, y0, x1, y1 = box
    f_axis, f_head = pil_font(12), pil_font(15, True)
    d.text((x0, y0 - 24), title, font=f_head, fill=(30, 50, 70))
    vals = [v for _, _, _, ys in series for v in ys if v is not None]
    y_max = y_max_hint if y_max_hint is not None else nice_ceiling(max(vals) * 1.05 if vals else 1.0)

    def px(frac: float) -> float:
        return x0 + (frac - fractions[0]) / (fractions[-1] - fractions[0]) * (x1 - x0)

    def py(v: float) -> float:
        return y1 - (min(v, y_max) / y_max) * (y1 - y0)

    d.rectangle(box, outline=(160, 170, 182))
    for gy in range(1, 5):
        yy = y0 + gy * (y1 - y0) / 5
        d.line((x0, yy, x1, yy), fill=(226, 230, 236))
        d.text((x0 - 8, yy - 6), f"{y_max * (1 - gy / 5):g}", font=f_axis, fill=(110, 118, 128), anchor="ra")
    d.text((x0 - 8, y0 - 6), f"{y_max:g}", font=f_axis, fill=(110, 118, 128), anchor="ra")
    d.text((x0 - 8, y1 - 6), "0", font=f_axis, fill=(110, 118, 128), anchor="ra")
    for frac in fractions:
        d.line((px(frac), y1, px(frac), y1 + 5), fill=(160, 170, 182))
        d.text((px(frac), y1 + 8), f"{frac}%", font=f_axis, fill=(110, 118, 128), anchor="ma")

    for mode, quality, color, ys in series:
        pts = [(px(f), py(v)) for f, v in zip(fractions, ys) if v is not None]
        if len(pts) >= 2:
            d.line(pts, fill=color, width=3)
        for f, v in zip(fractions, ys):
            if v is None:
                # unscored cell (rejected/failed decode): X on the floor
                xx, yy = px(f), y1 - 7
                d.line((xx - 5, yy - 5, xx + 5, yy + 5), fill=color, width=2)
                d.line((xx - 5, yy + 5, xx + 5, yy - 5), fill=color, width=2)
            else:
                draw_marker(d, px(f), py(v), mode, color)


def make_curve_sheet(
    rows: List[Dict[str, str]],
    source: str,
    has_tags: bool,
    modes: Sequence[str],
    qualities: Sequence[int],
    fractions: Sequence[int],
    out_path: Path,
    subtitle: str,
) -> None:
    panel_specs = (
        [("AprilTag count vs received %", "tag_count", 4.0),
         ("Min tag side (px) vs received %", "tag_side_px_min", None)]
        if has_tags
        else [("PSNR vs lossless source (dB) vs received %", "ref_psnr_rgb", None),
              ("Full-frame sharpness (laplacian var) vs received %", "ff_laplacian_var", None)]
    )
    W, panel_h, margin, legend_h = 980, 300, 70, 64
    header_h = 92
    H = header_h + len(panel_specs) * (panel_h + 58) + legend_h
    sheet = Image.new("RGB", (W, H), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    f_title, f_sub, f_leg = pil_font(26, True), pil_font(14), pil_font(13)
    d.text((margin, 20), f"Partial-transmission curves: {source}", font=f_title, fill=(30, 50, 70))
    d.text((margin, 54), subtitle, font=f_sub, fill=(90, 100, 110))

    cell: Dict[Tuple[str, int, int], Dict[str, str]] = {
        (r["mode"], int(r["jpeg_quality"]), int(r["received_fraction_pct"])): r for r in rows
    }
    for pi, (title, key, y_hint) in enumerate(panel_specs):
        y0 = header_h + pi * (panel_h + 58) + 24
        box = (margin, y0, W - 40, y0 + panel_h)
        series = []
        for mode, shades in (("baseline", BASELINE_SHADES), ("progressive", PROGRESSIVE_SHADES)):
            if mode not in modes:
                continue
            for qi, q in enumerate(qualities):
                ys = [fnum(cell[(mode, q, f)], key) if (mode, q, f) in cell else None for f in fractions]
                series.append((mode, q, shades[min(qi, len(shades) - 1)], ys))
        draw_panel(d, box, title, fractions, series, y_max_hint=y_hint)

    ly = H - legend_h + 6
    lx = margin
    for mode, shades in (("baseline", BASELINE_SHADES), ("progressive", PROGRESSIVE_SHADES)):
        if mode not in modes:
            continue
        for qi, q in enumerate(qualities):
            color = shades[min(qi, len(shades) - 1)]
            draw_marker(d, lx + 6, ly + 8, mode, color)
            d.text((lx + 18, ly), f"{mode} q{q}", font=f_leg, fill=(35, 50, 65))
            lx += 150
    d.text((margin, ly + 24), "X on panel floor = cell not scoreable (rejected/failed decode)", font=f_leg, fill=(110, 118, 128))
    sheet.save(out_path, quality=94)
    log(f"curve sheet: {out_path}")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Sprint06 P2 post-analysis: combine subruns, mode-compare sheets, metric-vs-% curves.")
    ap.add_argument("--parent", type=Path, required=True,
                    help="Parent folder holding per-source sweep subruns (card/, alt_03/, ...)")
    ap.add_argument("--output", type=Path, default=None,
                    help="Output folder. Default: <parent>/post_analysis")
    args = ap.parse_args()

    parent = args.parent.expanduser().resolve()
    if not parent.is_dir():
        raise SystemExit(f"--parent not a directory: {parent}")
    out_dir = (args.output or parent / "post_analysis").expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log.attach(out_dir / "post_analysis_log.txt")
    log(f"parent={parent}")
    log(f"output={out_dir}")

    sources = discover_sources(parent)
    if not sources:
        raise SystemExit(f"No subruns with {RESULTS_REL} found under {parent}")
    log(f"sources: {[n for n, _ in sources]}")
    rows = load_rows(parent, sources)
    write_combined_csv(rows, out_dir / "combined_results_p2_partial.csv")

    sheets_dir = out_dir / "cut_sheets_mode_compare"
    curves_dir = out_dir / "curves"
    sheets_dir.mkdir(exist_ok=True)
    curves_dir.mkdir(exist_ok=True)

    made_sheets, made_curves = 0, 0
    for name, sub in sources:
        src_rows = [r for r in rows if r["source"] == name]
        modes = sorted({r["mode"] for r in src_rows})
        qualities = sorted({int(r["jpeg_quality"]) for r in src_rows})
        fractions = sorted({int(r["received_fraction_pct"]) for r in src_rows})
        has_tags = any((r.get("tag_count") or "") not in ("", None) for r in src_rows)
        # Geometry for labels, from the subrun manifest (fail soft: label unknown).
        geom = ""
        man = sub / "run_manifest.json"
        if man.is_file():
            try:
                coords = json.loads(man.read_text(encoding="utf-8")).get("coordinate_systems", {})
                geom = f"{coords.get('native', '')} -> {coords.get('output', '')}"
            except Exception:
                geom = "(manifest unreadable)"
        subtitle = f"run={parent.name}/{name}  {geom}"
        if len(fractions) < 2:
            log(f"WARNING {name}: only fractions {fractions} — skipping sheets/curves")
            continue
        for q in qualities:
            make_mode_compare_sheet(src_rows, name, q, fractions, sheets_dir / f"{name}_q{q:02d}_baseline_vs_progressive.jpg", subtitle)
            made_sheets += 1
        make_curve_sheet(src_rows, name, has_tags, modes, qualities, fractions, curves_dir / f"{name}_curves.jpg", subtitle)
        made_curves += 1

    manifest = {
        "tool": "bm_jpeg_partial_post_analysis.py",
        "sprint": "Sprint06 P2 partial-transmission post-analysis",
        "created_utc": utc_stamp(),
        "platform": platform.platform(),
        "python": sys.version,
        "parent": str(parent),
        "sources": [n for n, _ in sources],
        "row_count": len(rows),
        "outputs": {
            "combined_csv": str(out_dir / "combined_results_p2_partial.csv"),
            "mode_compare_sheets": made_sheets,
            "curve_sheets": made_curves,
        },
    }
    (out_dir / "post_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    log(f"complete: {made_sheets} mode-compare sheets, {made_curves} curve sheets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
