#!/usr/bin/env python3
"""
BM Mac-side analysis of a Pi encode run (Sprint 07 P1)

Purpose
-------
The Sprint07 division of labor: the Pi alters images (crop -> downsample ->
JPEG encode, via tools/bm_pi_jpeg_encode.py), the Mac does ALL image-quality
analysis. This tool ingests a pulled-back Pi run (parent folder with one
subrun per source) and scores every Pi-encoded JPEG with the UNCHANGED
Sprint06 analysis code:

  - full-frame metrics + PSNR vs the (pixel-verified) lossless working
    source, imported directly from bm_reference_card_jpeg_partial_sweep.py
  - AprilTag detection via bm_reference_card_quality_v2.py (card only),
    through the sweep tool's own run_quality_analyzer/sprint_status
  - quality-ladder cut sheets via the sweep tool's make_tile_sheet

Output is one analysis subrun per source in the sweep tool's on-disk format
(results/results_jpeg_partial_sweep.csv, decoded/, cut_sheets/), so
tools/bm_jpeg_p3_budget_verdict.py runs on it UNCHANGED to produce the
Pi-native heatmaps + ranked recommendation table. The Pi timing columns
(encode_wall_s_mean/best, peak_rss_kb_after) are carried through as extra
CSV columns.

Optionally (--mac-ref), every Pi JPEG is sha256-compared against the same
cell of a Mac reference run (e.g. p3_verdict_20260722T055437Z) to extend the
P0 byte-parity check across the whole grid -> parity_grid.csv.

Inputs
------
  --pi-run   pulled-back Pi parent (subdirs per source, each with
             results/encode_results.csv, jpeg/<label>/*.jpg, source_<W>/*.png)
  --mac-ref  optional Mac sweep parent for the sha256 parity grid
  --output   analysis parent. Default: ~/Downloads/bm_jpeg_partial_sweep/p1_pi_analysis_<UTC>

Example
-------
  .venv/bin/python3 tools/bm_mac_analyze_pi_run.py \
      --pi-run ~/Downloads/bm_jpeg_partial_sweep/p1_grid_<UTC> \
      --mac-ref ~/Downloads/bm_jpeg_partial_sweep/p3_verdict_20260722T055437Z

Assumptions / known limitations
-------------------------------
  - 100% received only (fractions belong to the Mac DOE; P1 re-validates
    full-file quality/budget on Pi bytes). decode_partial is still used at
    fraction 100 so the decode path matches the Sweep exactly.
  - PSNR reference is the Pi subrun's own source PNG (P0 proved it is
    pixel-identical to the Mac working source).
  - All sources in the Pi parent share one geometry per subrun; the output
    size is read from each subrun's run_manifest.json.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import shutil
import sys
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm_reference_card_jpeg_partial_sweep as sweep  # unchanged Sprint06 analysis code

PI_RESULTS_REL = Path("results") / "encode_results.csv"
OUT_RESULTS_REL = Path("results") / "results_jpeg_partial_sweep.csv"
PI_TIMING_FIELDS = ["encode_wall_s_mean", "encode_wall_s_best", "timing_repeats", "peak_rss_kb_after"]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_pi_manifest(subrun: Path) -> Dict[str, object]:
    p = subrun / "run_manifest.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def subrun_output_size(subrun: Path, manifest: Dict[str, object]) -> tuple:
    """Output size from the Pi manifest ('1000x562 lanczos-downsampled px')."""
    txt = str(manifest.get("coordinate_systems", {}).get("output", ""))
    dims = txt.split(" ")[0] if txt else ""
    if "x" in dims:
        w, h = dims.split("x")
        return (int(w), int(h))
    pngs = list((subrun.glob("source_*/*.png")))
    if pngs:
        from PIL import Image
        with Image.open(pngs[0]) as im:
            return im.size
    raise SystemExit(f"cannot determine output size for {subrun}")


def analyze_subrun(source_name: str, pi_sub: Path, out_sub: Path, args) -> List[Dict[str, object]]:
    manifest = load_pi_manifest(pi_sub)
    out_size = subrun_output_size(pi_sub, manifest)
    sweep.OUTPUT_SIZE = out_size  # decode size check + cut-sheet labels see the Pi geometry

    with (pi_sub / PI_RESULTS_REL).open("r", newline="", encoding="utf-8") as f:
        pi_rows = list(csv.DictReader(f))
    if not pi_rows:
        raise SystemExit(f"{source_name}: empty {PI_RESULTS_REL}")
    label = pi_rows[0]["image_label"]

    src_candidates = sorted(pi_sub.glob(f"source_*/{label}_source_*.png"))
    if not src_candidates:
        raise SystemExit(f"{source_name}: no source PNG under {pi_sub}")
    source_png = src_candidates[0]

    sweep.ensure_dir(out_sub)
    rows: List[Dict[str, object]] = []
    for pr in pi_rows:
        jpeg_path = pi_sub / "jpeg" / label / Path(pr["jpeg_path"]).name
        if not jpeg_path.is_file():
            raise SystemExit(f"{source_name}: missing pulled-back JPEG {jpeg_path}")
        enc = sweep.EncodeInfo(
            image_label=label,
            mode=pr["mode"],
            jpeg_quality=int(pr["jpeg_quality"]),
            jpeg_path=str(jpeg_path),
            jpeg_bytes=int(pr["jpeg_bytes"]),
            jpeg_kb=float(pr["jpeg_kb"]),
            base64_len=int(pr["base64_len"]),
            message_count=int(pr["message_count"]),
            est_minutes=float(pr["est_minutes"]),
            duration_band=pr["duration_band"],
        )
        part = sweep.decode_partial(enc, 100, out_sub, args.min_recovered_fraction)
        row: Dict[str, object] = {**asdict(enc), **asdict(part)}
        if part.decode_ok and part.recovered_status == "OK":
            row.update(sweep.full_frame_metrics(Path(part.decoded_path), source_png))
        for k in PI_TIMING_FIELDS:
            row[f"pi_{k}"] = pr.get(k, "")
        row["pi_jpeg_sha256"] = pr.get("jpeg_sha256", "")
        rows.append(row)

    if label in sweep.IMAGES_WITH_TAGS:
        scored = [r for r in rows if r.get("recovered_status") == "OK"]
        if scored:
            sweep.log(f"running AprilTag analyzer on {len(scored)} decoded frames for {source_name}")
            analyzer_rows = sweep.run_quality_analyzer(
                args.quality_script, out_sub / "decoded" / label, out_sub / "quality" / label,
                args.corner_map, args.scales, source_png,
            )
            for r in rows:
                ar = analyzer_rows.get(Path(str(r.get("decoded_path") or "x")).stem)
                if ar is None:
                    continue
                for k in sweep.ANALYZER_CARRY_FIELDS:
                    r[k] = ar.get(k, "")
                status, reason = sweep.sprint_status(ar)
                r["sprint_status"], r["status_reason"] = status, reason
        for r in rows:
            if "sprint_status" not in r:
                r["sprint_status"] = "FAIL"
                r["status_reason"] = f"no scoreable decode ({r.get('recovered_status')})"
    else:
        for r in rows:
            r["sprint_status"] = ""
            r["status_reason"] = "no tags on this image; scored on sharpness/contrast/PSNR"

    sweep.write_csv(out_sub / OUT_RESULTS_REL, rows, sweep.RESULT_FIELDS)
    shutil.copy2(pi_sub / "run_manifest.json", out_sub / "pi_run_manifest.json")

    for mode in sorted({r["mode"] for r in rows}):
        ladder = sorted([r for r in rows if r["mode"] == mode], key=lambda r: int(r["jpeg_quality"]))
        if ladder:
            sweep.make_tile_sheet(
                ladder,
                out_sub / "cut_sheets" / f"{source_name}_{mode}_quality_ladder.jpg",
                f"Pi-encoded JPEG quality ladder: {source_name} {mode} (100% received)",
                f"pi_run={pi_sub.name}  encoded on {load_pi_manifest(pi_sub).get('hardware', {}).get('board_model', 'Pi')}"
                f"  -> {out_size[0]}x{out_size[1]}",
            )
    return rows


def parity_grid(pi_parent: Path, mac_ref: Path, sources: List[str], out_csv: Path) -> Dict[str, int]:
    """sha256 every Pi JPEG vs the same cell in the Mac reference run."""
    mac_rows: Dict[tuple, Dict[str, str]] = {}
    combined = next(mac_ref.glob("verdict/combined_results_*.csv"), None)
    if combined is None:
        raise SystemExit(f"no verdict/combined_results_*.csv under {mac_ref}")
    for r in csv.DictReader(combined.open()):
        if r.get("received_fraction_pct") == "100":
            mac_rows[(r["source"], r["mode"], r["jpeg_quality"])] = r

    out_rows, identical, differing, missing = [], 0, 0, 0
    for source in sources:
        pi_csv = pi_parent / source / PI_RESULTS_REL
        for pr in csv.DictReader(pi_csv.open()):
            key = (source, pr["mode"], pr["jpeg_quality"])
            mr = mac_rows.get(key)
            if mr is None:
                missing += 1
                out_rows.append({"source": source, "mode": pr["mode"], "q": pr["jpeg_quality"],
                                 "pi_bytes": pr["jpeg_bytes"], "mac_bytes": "", "delta_pct": "",
                                 "byte_identical": "NO_MAC_CELL"})
                continue
            mac_jpg = Path(mr["jpeg_path"])
            mac_sha = hashlib.sha256(mac_jpg.read_bytes()).hexdigest() if mac_jpg.is_file() else ""
            same = bool(mac_sha) and mac_sha == pr.get("jpeg_sha256")
            identical += int(same)
            differing += int(not same)
            delta = 100.0 * (int(pr["jpeg_bytes"]) - int(mr["jpeg_bytes"])) / int(mr["jpeg_bytes"])
            out_rows.append({"source": source, "mode": pr["mode"], "q": pr["jpeg_quality"],
                             "pi_bytes": pr["jpeg_bytes"], "mac_bytes": mr["jpeg_bytes"],
                             "delta_pct": round(delta, 4), "byte_identical": same})
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)
    return {"identical": identical, "differing": differing, "no_mac_cell": missing, "total": len(out_rows)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Sprint07 Mac-side analysis of a pulled-back Pi encode run.")
    ap.add_argument("--pi-run", type=Path, required=True, help="Pulled-back Pi parent folder (subdir per source)")
    ap.add_argument("--mac-ref", type=Path, default=None, help="Mac sweep parent for the sha256 parity grid (optional)")
    ap.add_argument("--output", type=Path, default=None,
                    help="Analysis parent. Default: ~/Downloads/bm_jpeg_partial_sweep/p1_pi_analysis_<UTC>")
    ap.add_argument("--min-recovered-fraction", type=float, default=0.10)
    ap.add_argument("--quality-script", type=Path, default=Path(__file__).resolve().parent / "bm_reference_card_quality_v2.py")
    ap.add_argument("--corner-map", default=sweep.DEFAULT_CORNER_MAP)
    ap.add_argument("--scales", nargs="+", type=float, default=[1, 2, 3])
    args = ap.parse_args()

    pi_parent = args.pi_run.expanduser().resolve()
    sources = [p.name for p in sorted(pi_parent.iterdir()) if p.is_dir() and (p / PI_RESULTS_REL).is_file()]
    if not sources:
        raise SystemExit(f"no Pi subruns (with {PI_RESULTS_REL}) under {pi_parent}")

    run_tag = f"p1_pi_analysis_{utc_stamp()}"
    out_dir = (args.output or (Path.home() / "Downloads" / "bm_jpeg_partial_sweep" / run_tag)).expanduser().resolve()
    sweep.ensure_dir(out_dir)
    sweep.log.attach(out_dir / "analysis_log.txt")
    sweep.log(f"run_tag={run_tag}")
    sweep.log(f"pi_run={pi_parent}")
    sweep.log(f"sources={sources}")

    manifest: Dict[str, object] = {
        "tool": "bm_mac_analyze_pi_run.py",
        "sprint": "Sprint07 P1 (Mac-side analysis of Pi-encoded JPEGs)",
        "run_tag": run_tag,
        "created_utc": utc_stamp(),
        "platform": platform.platform(),
        "python": sys.version,
        "pi_run": str(pi_parent),
        "mac_ref": str(args.mac_ref) if args.mac_ref else "",
        "sources": sources,
        "analysis_code": "imported unchanged from bm_reference_card_jpeg_partial_sweep.py"
                         " (full_frame_metrics, decode_partial, run_quality_analyzer, sprint_status)",
        "errors": [],
    }
    try:
        for source in sources:
            analyze_subrun(source, pi_parent / source, out_dir / source, args)
        if args.mac_ref:
            summary = parity_grid(pi_parent, args.mac_ref.expanduser().resolve(), sources, out_dir / "parity_grid.csv")
            manifest["parity_grid"] = summary
            sweep.log(f"parity grid vs {args.mac_ref.name}: {summary}")
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        sweep.log("complete")
        sweep.log(f"next: .venv/bin/python3 tools/bm_jpeg_p3_budget_verdict.py --parent {out_dir}")
        return 0
    except Exception as exc:
        manifest["errors"].append({"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        sweep.log(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
