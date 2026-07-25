#!/usr/bin/env python3
"""
BM Sprint07 P4 — truncated-progressive render check against the REAL backend code

Purpose
-------
The deployment premise: when a transmission is tail-cut (backend bug register
B6), a progressive JPEG must still yield a usable partial preview. The Nereus
backend already has the machinery — on partial ingest it calls
convert_partial_image_bytes_to_jpeg() (LOAD_TRUNCATED_IMAGES decode, reject
< 5% recovered rows, re-encode JPEG q85, upload as *.partial.jpg display
derivative; see nereus-vision-dev/backend/app/services/image_derivatives.py,
wired at poll_once_ingest.py:879). Partials with a derivative get
render_state="renderable" in the gallery; without one they fall back to a
placeholder tile.

This tool proves that path with REAL Pi-encoded files: it loads the backend's
image_derivatives module (by file path, read-only — the backend repo is not
modified) and feeds it tail-cut prefixes of the Sprint07 P1 grid JPEGs at
several received fractions, in both modes. Truncation uses the Sprint06 chunk
model: N = ceil(base64_len/300) messages, keep the FIRST floor(N*f) messages,
1 message = 225 raw bytes.

Outputs (timestamped, self-contained)
-------------------------------------
  <out>/results/p4_render_check.csv   ok/error, recovered fraction, sizes
  <out>/derivatives/*.jpg             what the gallery would actually serve
  <out>/cut_sheets/p4_render_evidence_*.jpg  grid: cell x fraction, PASS/FAIL
  <out>/run_manifest.json, p4_log.txt

Example
-------
  ~/Documents/GitHub/nereus-vision-dev/backend/.venv/bin/python \
      tools/bm_p4_partial_render_check.py \
      --pi-run ~/Downloads/bm_jpeg_partial_sweep/p1_grid_20260724T165653Z \
      --backend ~/Documents/GitHub/nereus-vision-dev/backend

Assumptions / known limitations
-------------------------------
  - Function-level validation of the ingest derivative path; it does not run
    the FastAPI app, Postgres, or R2. End-to-end staging ingest is a separate
    (optional) exercise.
  - Run with the backend's .venv python so PIL/pillow_heif match the deployed
    decode environment.
"""
from __future__ import annotations

import argparse
import base64
import csv
import importlib.util
import json
import math
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFont

CHUNK_B64_CHARS = 300
RAW_BYTES_PER_MSG = CHUNK_B64_CHARS * 3 // 4  # 225

DEFAULT_CELLS = [  # (source, mode, quality) — shortlist + baseline contrast
    ("card", "progressive", 13),
    ("card", "baseline", 13),
    ("coral_primary", "progressive", 13),
    ("alt_07", "progressive", 13),
    ("alt_07", "progressive", 9),
    ("alt_07", "progressive", 15),
    ("alt_07", "baseline", 13),
]
DEFAULT_FRACTIONS = [10, 25, 50, 75, 90, 100]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


class RunLog:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.path = None

    def attach(self, path: Path) -> None:
        self.path = path
        if self.lines:
            path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"[p4-render] {msg}"
        print(line, flush=True)
        self.lines.append(line)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


log = RunLog()


def load_backend_module(backend_dir: Path):
    """Load the backend's image_derivatives module by file path (read-only)."""
    mod_path = backend_dir / "app" / "services" / "image_derivatives.py"
    if not mod_path.is_file():
        raise SystemExit(f"backend module not found: {mod_path}")
    spec = importlib.util.spec_from_file_location("backend_image_derivatives", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolves cls.__module__ via sys.modules
    spec.loader.exec_module(mod)
    return mod, mod_path


def truncate_tail(raw: bytes, fraction_pct: int) -> tuple:
    """Sprint06 chunk model tail cut: keep the FIRST floor(N*f) of N messages."""
    total_msgs = math.ceil(len(base64.b64encode(raw)) / CHUNK_B64_CHARS)
    kept = total_msgs if fraction_pct >= 100 else max(1, math.floor(total_msgs * fraction_pct / 100.0))
    return raw[: min(len(raw), kept * RAW_BYTES_PER_MSG)], kept, total_msgs


def pil_font(size: int, bold: bool = False):
    for c in [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def make_evidence_sheet(rows: List[Dict[str, object]], cells: List[tuple],
                        fractions: List[int], out_path: Path, title: str) -> None:
    """Grid cut sheet: one row per cell, one column per received fraction.
    Tiles are display-normalized thumbnails of the backend-produced derivative
    (NOT 1:1); red tiles are cells the backend rejected (placeholder path)."""
    ensure_dir(out_path.parent)
    f_title, f_small, f_tag = pil_font(24, True), pil_font(12), pil_font(13, True)
    tile = (300, 169)
    label_h, row_label_w, margin = 58, 210, 20
    by_key = {(r["source"], r["mode"], int(r["jpeg_quality"]), int(r["received_fraction_pct"])): r for r in rows}
    W = margin * 2 + row_label_w + len(fractions) * (tile[0] + 10)
    H = margin * 2 + 70 + len(cells) * (tile[1] + label_h + 10)
    sheet = Image.new("RGB", (W, H), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    d.text((margin, margin), title, font=f_title, fill=(30, 50, 70))
    d.text((margin, margin + 30),
           "tiles = backend display derivatives (display-normalized, not 1:1); red = backend rejected (placeholder tile in gallery)",
           font=f_small, fill=(90, 100, 110))
    for j, f in enumerate(fractions):
        x = margin + row_label_w + j * (tile[0] + 10)
        d.text((x, margin + 52), f"{f}% received", font=f_tag, fill=(60, 75, 95))
    for i, (src, mode, q) in enumerate(cells):
        y = margin + 70 + i * (tile[1] + label_h + 10)
        d.text((margin, y + tile[1] // 2 - 8), f"{src}\n{mode} q{q}", font=f_tag, fill=(35, 50, 65))
        for j, f in enumerate(fractions):
            x = margin + row_label_w + j * (tile[0] + 10)
            r = by_key.get((src, mode, q, f))
            if r is None:
                continue
            ok = bool(r["derivative_ok"])
            d.rectangle((x - 2, y - 2, x + tile[0] + 2, y + tile[1] + label_h),
                        fill=(220, 245, 226) if ok else (255, 214, 214), outline=(195, 205, 215))
            dp = Path(str(r["derivative_path"]))
            if ok and dp.is_file():
                with Image.open(dp) as im:
                    im = im.convert("RGB")
                    im.thumbnail(tile, Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", tile, (230, 235, 240))
                    canvas.paste(im, ((tile[0] - im.width) // 2, (tile[1] - im.height) // 2))
                    sheet.paste(canvas, (x, y))
            else:
                d.text((x + 12, y + tile[1] // 2 - 14), "REJECTED →\nplaceholder", font=f_tag, fill=(150, 30, 30))
            lines = [
                f"kept {r['messages_kept']}/{r['messages_total']} msgs ({r['truncated_bytes']} B)",
                (f"recovered ~{float(r['recovered_fraction']):.0%}  deriv {int(r['derivative_bytes'])//1024} KB"
                 if ok else f"backend: {str(r['error'])[:44]}"),
            ]
            for k, line in enumerate(lines):
                d.text((x + 2, y + tile[1] + 6 + k * 17), line, font=f_small, fill=(35, 50, 65))
    sheet.save(out_path, quality=92)
    log(f"cut sheet: {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sprint07 P4: run the backend partial-derivative code on tail-cut Pi JPEGs.")
    ap.add_argument("--pi-run", type=Path, required=True, help="P1 Pi grid parent (per-source subruns with jpeg/<label>/)")
    ap.add_argument("--backend", type=Path, required=True, help="nereus-vision-dev/backend repo dir (read-only)")
    ap.add_argument("--fractions", nargs="+", type=int, default=DEFAULT_FRACTIONS)
    ap.add_argument("--output", type=Path, default=None,
                    help="Run folder. Default: ~/Downloads/bm_jpeg_partial_sweep/p4_render_<UTC>")
    args = ap.parse_args()

    backend_mod, backend_mod_path = load_backend_module(args.backend.expanduser().resolve())
    pi_parent = args.pi_run.expanduser().resolve()
    run_tag = f"p4_render_{utc_stamp()}"
    out_dir = (args.output or (Path.home() / "Downloads" / "bm_jpeg_partial_sweep" / run_tag)).expanduser().resolve()
    ensure_dir(out_dir)
    log.attach(out_dir / "p4_log.txt")
    log(f"run_tag={run_tag}")
    log(f"pi_run={pi_parent}")
    log(f"backend module={backend_mod_path} (MIN_RECOVERED_FRACTION={backend_mod.MIN_RECOVERED_FRACTION})")

    rows: List[Dict[str, object]] = []
    for src, mode, q in DEFAULT_CELLS:
        label = "card" if src == "card" else "coral"
        jpeg = pi_parent / src / "jpeg" / label / f"{label}_{mode}_q{q:02d}.jpg"
        if not jpeg.is_file():
            raise SystemExit(f"missing Pi JPEG: {jpeg}")
        raw = jpeg.read_bytes()
        for frac in sorted(set(args.fractions)):
            truncated, kept, total = truncate_tail(raw, frac)
            stem = f"{src}_{mode}_q{q:02d}_f{frac:03d}"
            deriv_path = ensure_dir(out_dir / "derivatives") / f"{stem}.partial.jpg"
            ok, err, deriv_bytes, recovered = False, "", 0, 0.0
            try:
                deriv = backend_mod.convert_partial_image_bytes_to_jpeg(truncated)
                deriv_path.write_bytes(deriv)
                deriv_bytes = len(deriv)
                with Image.open(deriv_path) as im:
                    recovered = backend_mod.recovered_row_fraction(im)
                ok = True
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
            rows.append({
                "source": src, "mode": mode, "jpeg_quality": q,
                "received_fraction_pct": frac,
                "messages_kept": kept, "messages_total": total,
                "truncated_bytes": len(truncated), "full_bytes": len(raw),
                "derivative_ok": ok, "derivative_bytes": deriv_bytes,
                "recovered_fraction": round(recovered, 4),
                "error": err,
                "derivative_path": str(deriv_path) if ok else "",
                "pi_jpeg_path": str(jpeg),
            })
            log(f"{stem}: kept {kept}/{total} -> {'OK deriv ' + str(deriv_bytes) + 'B recovered ' + format(recovered, '.0%') if ok else 'REJECTED ' + err}")

    csv_path = ensure_dir(out_dir / "results") / "p4_render_check.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    log(f"results CSV: {csv_path} ({len(rows)} rows)")

    prog_cells = [c for c in DEFAULT_CELLS if c[1] == "progressive"]
    base_cells = [c for c in DEFAULT_CELLS if c[1] == "baseline"]
    make_evidence_sheet(rows, prog_cells, sorted(set(args.fractions)),
                        out_dir / "cut_sheets" / f"{run_tag}_progressive.jpg",
                        "P4 render evidence — PROGRESSIVE tail-cut through the real backend derivative code")
    make_evidence_sheet(rows, base_cells, sorted(set(args.fractions)),
                        out_dir / "cut_sheets" / f"{run_tag}_baseline_contrast.jpg",
                        "P4 render evidence — BASELINE contrast (top-N% scanlines expected)")

    manifest = {
        "tool": "bm_p4_partial_render_check.py",
        "sprint": "Sprint07 P4 truncated-progressive render check",
        "run_tag": run_tag, "created_utc": utc_stamp(),
        "platform": platform.platform(), "python": sys.version,
        "backend_module": str(backend_mod_path),
        "backend_min_recovered_fraction": backend_mod.MIN_RECOVERED_FRACTION,
        "pi_run": str(pi_parent),
        "chunk_model": {"b64_chars_per_msg": CHUNK_B64_CHARS, "raw_bytes_per_msg": RAW_BYTES_PER_MSG,
                        "truncation": "tail loss: keep first floor(N*f) of N messages"},
        "cells": DEFAULT_CELLS, "fractions": sorted(set(args.fractions)),
        "summary": {
            "progressive_ok": sum(1 for r in rows if r["mode"] == "progressive" and r["derivative_ok"]),
            "progressive_total": sum(1 for r in rows if r["mode"] == "progressive"),
            "baseline_ok": sum(1 for r in rows if r["mode"] == "baseline" and r["derivative_ok"]),
            "baseline_total": sum(1 for r in rows if r["mode"] == "baseline"),
        },
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    log(f"summary: {manifest['summary']}")
    log("complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
