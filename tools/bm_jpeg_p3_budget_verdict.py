#!/usr/bin/env python3
"""
BM JPEG Budget Overlay + Verdict (Sprint 06 P3)

Post-analysis only — no encoding. Aggregates the P3 verdict sweep (per-source
subruns of bm_reference_card_jpeg_partial_sweep.py at the frozen geometry:
ROI 1600x900 native -> 1000x562 output, 1.6x density, 100% received) and
produces the P3 deliverables:

  1. combined_results_p3_verdict.csv — all subrun rows + `source` column +
     `duration_band_cap195` (re-banded with the field-tested hard cap).
  2. Duration-banded heatmaps (PIL; no matplotlib in the venv):
       - per mode: rows = source, cols = JPEG quality; cell = messages +
         minutes, band-colored; card cells carry PASS/WARN/FAIL.
       - fleet summary: rows = mode, cols = quality; cell = coral band
         counts + worst-case coral + card status (the spec's quality x mode
         heatmap, coral-anchored).
  3. Ranked recommendation table (CSV + verdict.md) — the JPEG (mode,
     quality) values to try on the Pi (P4). Optional --p2-csv merges the P2
     partial-transmission robustness columns into the ranking.
  4. run_manifest.json + verdict_log.txt.

Budget bands (messages, coral-anchored)
---------------------------------------
  ideal <= 75 · feasible <= 125 · gated <= 195 · over_cap > 195
  The spec's hard cap was ~180 (~15 min); Nick field-tested a 195-message
  transmission successfully (2026-07-22), so P3 bands use 195. The sweep
  CSVs' own `duration_band` column (cap 180) is preserved unchanged;
  this tool adds `duration_band_cap195` and ranks on it.

Ranking rule (approved D9, inspectable — no weighted score)
-----------------------------------------------------------
  Eligible: card sprint_status PASS at 100% received AND worst coral scene
  <= 195 messages. Eligible cells sort by:
    1. # coral scenes feasible-or-better (desc)
    2. # coral scenes within the hard cap (desc; tie-break so ineligible
       cells still order sensibly by budget)
    3. P2 partial robustness: lowest received-% keeping the 4-tag card PASS
       (asc; cells without P2 data sort last on this key)
    4. mean coral PSNR vs lossless source (desc)
    5. worst-coral message count (asc)

Example
-------
  .venv/bin/python3 tools/bm_jpeg_p3_budget_verdict.py \
      --parent ~/Downloads/bm_jpeg_partial_sweep/p3_verdict_<UTC> \
      --p2-csv ~/Downloads/bm_jpeg_partial_sweep/p2_partial_<UTC>/post_analysis/combined_results_p2_partial.csv

Assumptions / known limitations
-------------------------------
  - Metrics are read from the sweep CSVs, never recomputed.
  - PSNR/chroma are per-scene vs each scene's own lossless source; means
    across the fixed 8-coral set are comparable across (mode, quality)
    cells but are not an absolute fidelity scale.
  - P2 robustness covers only the qualities/modes P2 swept (q 9/13/15);
    other cells show '-' and sort last on that key alone.
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

# Budget bands in messages. Hard cap 195 = Nick's successful field test
# (supersedes the spec's ~180 estimate); ideal/feasible unchanged from spec.
BAND_IDEAL_MAX = 75
BAND_FEASIBLE_MAX = 125
BAND_GATED_MAX = 195

BAND_ORDER = ["ideal", "feasible", "gated", "over_cap"]
BAND_FILL = {
    "ideal": (198, 232, 206),
    "feasible": (255, 240, 178),
    "gated": (255, 208, 158),
    "over_cap": (246, 178, 178),
    "missing": (222, 226, 232),
}

# Display order: card first, then corals cheapest-role first.
SOURCE_ORDER = ["card", "coral_primary", "alt_01", "alt_02", "alt_03", "alt_04", "alt_05", "alt_06", "alt_07"]


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
        line = f"[p3-verdict] {msg}"
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


def band_cap195(message_count: int) -> str:
    if message_count <= BAND_IDEAL_MAX:
        return "ideal"
    if message_count <= BAND_FEASIBLE_MAX:
        return "feasible"
    if message_count <= BAND_GATED_MAX:
        return "gated"
    return "over_cap"


# -----------------------------------------------------------------------------
# Load + combine
# -----------------------------------------------------------------------------


def discover_sources(parent: Path) -> List[Tuple[str, Path]]:
    found = [(p.name, p) for p in sorted(parent.iterdir()) if p.is_dir() and (p / RESULTS_REL).is_file()]
    known = [s for s in SOURCE_ORDER if s in {n for n, _ in found}]
    extra = sorted(n for n, _ in found if n not in SOURCE_ORDER)
    order = known + extra
    by_name = dict(found)
    return [(n, by_name[n]) for n in order]


def load_rows(sources: List[Tuple[str, Path]]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for name, sub in sources:
        with (sub / RESULTS_REL).open("r", newline="", encoding="utf-8") as f:
            sub_rows = [r for r in csv.DictReader(f) if int(r["received_fraction_pct"]) == 100]
        for r in sub_rows:
            r["source"] = name
            r["duration_band_cap195"] = band_cap195(int(r["message_count"]))
        log(f"loaded {name}: {len(sub_rows)} rows (100% received)")
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
# P2 partial-robustness merge (optional)
# -----------------------------------------------------------------------------


def load_p2_robustness(p2_csv: Path) -> Dict[Tuple[str, int], Dict[str, object]]:
    """Per (mode, quality): lowest received-% keeping the 4-tag card PASS,
    and mean coral partial PSNR at 50% received (P2 worst-case corals)."""
    with p2_csv.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out: Dict[Tuple[str, int], Dict[str, object]] = {}
    keys = {(r["mode"], int(r["jpeg_quality"])) for r in rows}
    for mode, q in sorted(keys):
        cell = [r for r in rows if r["mode"] == mode and int(r["jpeg_quality"]) == q]
        card = sorted((r for r in cell if r["image_label"] == "card"),
                      key=lambda r: int(r["received_fraction_pct"]))
        # Smallest fraction f where PASS holds at f and every larger swept fraction.
        pass_from: Optional[int] = None
        for i, r in enumerate(card):
            if all(c.get("sprint_status") == "PASS" for c in card[i:]):
                pass_from = int(r["received_fraction_pct"])
                break
        psnr50 = [fnum(r, "ref_psnr_rgb") for r in cell
                  if r["image_label"] == "coral" and int(r["received_fraction_pct"]) == 50]
        psnr50 = [v for v in psnr50 if v is not None]
        out[(mode, q)] = {
            "p2_card_pass_from_recv_pct": pass_from,
            "p2_coral_psnr_at_50pct": round(sum(psnr50) / len(psnr50), 2) if psnr50 else None,
        }
    log(f"P2 robustness loaded for cells: {sorted(out)}")
    return out


# -----------------------------------------------------------------------------
# Heatmaps
# -----------------------------------------------------------------------------


def dark_for(fill: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return (35, 48, 62)


def make_source_quality_heatmap(
    rows: List[Dict[str, str]],
    mode: str,
    sources: Sequence[str],
    qualities: Sequence[int],
    out_path: Path,
    subtitle: str,
) -> None:
    f_title, f_sub, f_axis, f_cell, f_small = pil_font(28, True), pil_font(14), pil_font(16, True), pil_font(15, True), pil_font(12)
    left, top, right, bottom, legend_h = 150, 122, 30, 24, 60
    cell_w, cell_h = 118, 74
    w = left + len(qualities) * cell_w + right
    h = top + len(sources) * cell_h + bottom + legend_h
    sheet = Image.new("RGB", (w, h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    d.text((24, 20), f"Transmit budget heatmap: {mode} JPEG (100% received)", font=f_title, fill=(30, 45, 60))
    d.text((24, 58), subtitle, font=f_sub, fill=(80, 90, 100))
    d.text((left, top - 52), "JPEG quality", font=f_small, fill=(80, 90, 100))
    for j, q in enumerate(qualities):
        d.text((left + j * cell_w + cell_w / 2 - 14, top - 28), f"q{q}", font=f_axis, fill=(35, 50, 65))

    by_cell = {(r["source"], int(r["jpeg_quality"])): r for r in rows if r["mode"] == mode}
    for i, src in enumerate(sources):
        y = top + i * cell_h
        d.text((22, y + cell_h / 2 - 10), src, font=f_axis, fill=(35, 50, 65))
        for j, q in enumerate(qualities):
            x = left + j * cell_w
            r = by_cell.get((src, q))
            if r is None:
                d.rectangle((x, y, x + cell_w - 2, y + cell_h - 2), fill=BAND_FILL["missing"], outline=(255, 255, 255), width=2)
                d.text((x + 10, y + cell_h / 2 - 8), "missing", font=f_small, fill=(70, 80, 90))
                continue
            msgs = int(r["message_count"])
            band = r["duration_band_cap195"]
            fill = BAND_FILL[band]
            d.rectangle((x, y, x + cell_w - 2, y + cell_h - 2), fill=fill, outline=(255, 255, 255), width=2)
            lines = [f"{msgs} msg", f"{float(r['est_minutes']):.1f} min"]
            status = r.get("sprint_status") or ""
            if status:
                lines.append(status)
            for k, line in enumerate(lines):
                d.text((x + 10, y + 7 + k * 20), line, font=f_cell if k < 2 else f_small, fill=dark_for(fill))

    ly = top + len(sources) * cell_h + 26
    lx = 24
    d.text((lx, ly - 20), f"Bands (messages): ideal<={BAND_IDEAL_MAX}  feasible<={BAND_FEASIBLE_MAX}  gated<={BAND_GATED_MAX} (field-tested cap)  over_cap>{BAND_GATED_MAX}", font=f_small, fill=(80, 90, 100))
    for band in BAND_ORDER:
        d.rectangle((lx, ly, lx + 26, ly + 20), fill=BAND_FILL[band], outline=(120, 130, 140))
        d.text((lx + 32, ly + 2), band, font=f_small, fill=(35, 50, 65))
        lx += 150
    sheet.save(out_path, quality=95)
    log(f"heatmap: {out_path}")


def coral_band_counts(rows: List[Dict[str, str]], mode: str, q: int) -> Dict[str, int]:
    counts = {b: 0 for b in BAND_ORDER}
    for r in rows:
        if r["mode"] == mode and int(r["jpeg_quality"]) == q and r["source"] != "card":
            counts[r["duration_band_cap195"]] += 1
    return counts


def worst_coral(rows: List[Dict[str, str]], mode: str, q: int) -> Optional[Dict[str, str]]:
    corals = [r for r in rows if r["mode"] == mode and int(r["jpeg_quality"]) == q and r["source"] != "card"]
    return max(corals, key=lambda r: int(r["message_count"])) if corals else None


def make_fleet_summary_heatmap(
    rows: List[Dict[str, str]],
    modes: Sequence[str],
    qualities: Sequence[int],
    n_corals: int,
    out_path: Path,
    subtitle: str,
) -> None:
    """The spec's quality x mode heatmap, coral-anchored: cell color = band of
    the WORST coral scene; text = coral band counts + worst scene + card status."""
    f_title, f_sub, f_axis, f_cell, f_small = pil_font(28, True), pil_font(14), pil_font(16, True), pil_font(14, True), pil_font(12)
    left, top, right, bottom, legend_h = 150, 128, 30, 24, 60
    cell_w, cell_h = 172, 96
    w = left + len(qualities) * cell_w + right
    h = top + len(modes) * cell_h + bottom + legend_h
    sheet = Image.new("RGB", (w, h), (245, 247, 250))
    d = ImageDraw.Draw(sheet)
    d.text((24, 20), "Fleet budget summary: quality x mode (coral-anchored)", font=f_title, fill=(30, 45, 60))
    d.text((24, 58), subtitle, font=f_sub, fill=(80, 90, 100))
    d.text((24, 78), f"Cell color = worst coral scene's band ({n_corals} coral scenes); I/F/G/X = coral scenes per band", font=f_sub, fill=(80, 90, 100))
    for j, q in enumerate(qualities):
        d.text((left + j * cell_w + cell_w / 2 - 14, top - 26), f"q{q}", font=f_axis, fill=(35, 50, 65))

    card_by_q = {(r["mode"], int(r["jpeg_quality"])): r for r in rows if r["source"] == "card"}
    for i, mode in enumerate(modes):
        y = top + i * cell_h
        d.text((22, y + cell_h / 2 - 10), mode, font=f_axis, fill=(35, 50, 65))
        for j, q in enumerate(qualities):
            x = left + j * cell_w
            wc = worst_coral(rows, mode, q)
            if wc is None:
                d.rectangle((x, y, x + cell_w - 2, y + cell_h - 2), fill=BAND_FILL["missing"], outline=(255, 255, 255), width=2)
                continue
            counts = coral_band_counts(rows, mode, q)
            fill = BAND_FILL[wc["duration_band_cap195"]]
            d.rectangle((x, y, x + cell_w - 2, y + cell_h - 2), fill=fill, outline=(255, 255, 255), width=2)
            card = card_by_q.get((mode, q))
            card_txt = f"card {card.get('sprint_status')} {int(card['message_count'])}m" if card else "card missing"
            lines = [
                f"I{counts['ideal']} F{counts['feasible']} G{counts['gated']} X{counts['over_cap']}",
                f"worst {int(wc['message_count'])}m ({wc['source']})",
                card_txt,
            ]
            for k, line in enumerate(lines):
                d.text((x + 10, y + 8 + k * 22), line, font=f_cell if k == 0 else f_small, fill=dark_for(fill))

    ly = top + len(modes) * cell_h + 26
    lx = 24
    d.text((lx, ly - 20), f"Bands (messages): ideal<={BAND_IDEAL_MAX}  feasible<={BAND_FEASIBLE_MAX}  gated<={BAND_GATED_MAX} (field-tested cap)  over_cap>{BAND_GATED_MAX}", font=f_small, fill=(80, 90, 100))
    for band in BAND_ORDER:
        d.rectangle((lx, ly, lx + 26, ly + 20), fill=BAND_FILL[band], outline=(120, 130, 140))
        d.text((lx + 32, ly + 2), band, font=f_small, fill=(35, 50, 65))
        lx += 150
    sheet.save(out_path, quality=95)
    log(f"heatmap: {out_path}")


# -----------------------------------------------------------------------------
# Ranked recommendation
# -----------------------------------------------------------------------------


def build_ranking(
    rows: List[Dict[str, str]],
    modes: Sequence[str],
    qualities: Sequence[int],
    p2: Dict[Tuple[str, int], Dict[str, object]],
) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for mode in modes:
        for q in qualities:
            corals = [r for r in rows if r["mode"] == mode and int(r["jpeg_quality"]) == q and r["source"] != "card"]
            card = next((r for r in rows if r["mode"] == mode and int(r["jpeg_quality"]) == q and r["source"] == "card"), None)
            if not corals or card is None:
                continue
            counts = coral_band_counts(rows, mode, q)
            wc = worst_coral(rows, mode, q)
            psnrs = [fnum(r, "ref_psnr_rgb") for r in corals]
            psnrs = [v for v in psnrs if v is not None]
            chromas = [fnum(r, "ff_chroma_sat") for r in corals]
            chromas = [v for v in chromas if v is not None]
            rob = p2.get((mode, q), {})
            eligible = (card.get("sprint_status") == "PASS") and int(wc["message_count"]) <= BAND_GATED_MAX
            out.append({
                "mode": mode,
                "jpeg_quality": q,
                "eligible": eligible,
                "card_status": card.get("sprint_status", ""),
                "card_min_tag_px": card.get("tag_side_px_min", ""),
                "card_msgs": int(card["message_count"]),
                "corals_ideal": counts["ideal"],
                "corals_feasible_or_better": counts["ideal"] + counts["feasible"],
                "corals_within_cap": counts["ideal"] + counts["feasible"] + counts["gated"],
                "corals_over_cap": counts["over_cap"],
                "worst_coral": wc["source"],
                "worst_coral_msgs": int(wc["message_count"]),
                "worst_coral_minutes": round(int(wc["message_count"]) * 5 / 60.0, 1),
                "mean_coral_psnr": round(sum(psnrs) / len(psnrs), 2) if psnrs else "",
                "mean_coral_chroma_sat": round(sum(chromas) / len(chromas), 2) if chromas else "",
                "p2_card_pass_from_recv_pct": rob.get("p2_card_pass_from_recv_pct"),
                "p2_coral_psnr_at_50pct": rob.get("p2_coral_psnr_at_50pct"),
            })

    def sort_key(c: Dict[str, object]):
        pass_from = c["p2_card_pass_from_recv_pct"]
        return (
            0 if c["eligible"] else 1,
            -int(c["corals_feasible_or_better"]),
            -int(c["corals_within_cap"]),
            int(pass_from) if pass_from is not None else 999,  # no P2 data sorts last on this key
            -(c["mean_coral_psnr"] if c["mean_coral_psnr"] != "" else -999.0),
            int(c["worst_coral_msgs"]),
        )

    out.sort(key=sort_key)
    for i, c in enumerate(out, 1):
        c["rank"] = i
    return out


def write_verdict_md(ranking: List[Dict[str, object]], out_path: Path, run_tag: str, n_corals: int, p2_note: str) -> None:
    hdr = ["rank", "mode", "q", "eligible", "card@100%", "corals I/F+/cap (of {n})", "worst coral",
           "mean PSNR", "P2: card PASS from", "P2: coral PSNR@50%"]
    lines = [
        f"# Sprint06 P3 verdict — ranked JPEG (mode, quality) for Pi validation",
        "",
        f"Run: `{run_tag}` · geometry frozen: ROI 1600x900 native -> 1000x562 (1.6x density) · 100% received",
        f"Bands (messages): ideal<={BAND_IDEAL_MAX} · feasible<={BAND_FEASIBLE_MAX} · gated<={BAND_GATED_MAX} "
        f"(hard cap 195 = field-tested, supersedes spec ~180) · {n_corals} coral scenes, coral-anchored.",
        "",
        "Eligibility: card 4-tag PASS at 100% AND worst coral <= 195 msgs. Rank keys, in order: "
        "coral scenes feasible-or-better (desc) -> scenes within cap (desc) -> P2 card-PASS-from received-% (asc) "
        "-> mean coral PSNR (desc) -> worst-coral msgs (asc). " + p2_note,
        "",
        "| " + " | ".join(h.replace("{n}", str(n_corals)) for h in hdr) + " |",
        "|" + "---|" * len(hdr),
    ]
    for c in ranking:
        pf = c["p2_card_pass_from_recv_pct"]
        p50 = c["p2_coral_psnr_at_50pct"]
        lines.append("| " + " | ".join(str(x) for x in [
            c["rank"], c["mode"], c["jpeg_quality"], "yes" if c["eligible"] else "no",
            f"{c['card_status']} ({c['card_msgs']}m)",
            f"{c['corals_ideal']}/{c['corals_feasible_or_better']}/{c['corals_within_cap']}",
            f"{c['worst_coral']} {c['worst_coral_msgs']}m ({c['worst_coral_minutes']} min)",
            c["mean_coral_psnr"],
            f"{pf}%" if pf is not None else "-",
            p50 if p50 is not None else "-",
        ]) + " |")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"verdict markdown: {out_path}")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Sprint06 P3: budget overlay heatmaps + ranked (mode, quality) verdict.")
    ap.add_argument("--parent", type=Path, required=True,
                    help="Parent folder holding per-source P3 sweep subruns (card/, coral_primary/, alt_01/, ...)")
    ap.add_argument("--p2-csv", type=Path, default=None,
                    help="Optional P2 combined_results_p2_partial.csv to merge partial-robustness columns")
    ap.add_argument("--output", type=Path, default=None,
                    help="Output folder. Default: <parent>/verdict")
    args = ap.parse_args()

    parent = args.parent.expanduser().resolve()
    if not parent.is_dir():
        raise SystemExit(f"--parent not a directory: {parent}")
    out_dir = (args.output or parent / "verdict").expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log.attach(out_dir / "verdict_log.txt")
    log(f"parent={parent}")
    log(f"output={out_dir}")

    sources = discover_sources(parent)
    if not sources:
        raise SystemExit(f"No subruns with {RESULTS_REL} found under {parent}")
    log(f"sources: {[n for n, _ in sources]}")
    rows = load_rows(sources)
    write_combined_csv(rows, out_dir / "combined_results_p3_verdict.csv")

    modes = sorted({r["mode"] for r in rows})
    qualities = sorted({int(r["jpeg_quality"]) for r in rows})
    source_names = [n for n, _ in sources]
    n_corals = len([s for s in source_names if s != "card"])
    log(f"modes={modes} qualities={qualities} corals={n_corals}")

    p2: Dict[Tuple[str, int], Dict[str, object]] = {}
    p2_note = "No --p2-csv given: P2 robustness columns empty."
    if args.p2_csv is not None:
        p2_csv = args.p2_csv.expanduser().resolve()
        if not p2_csv.is_file():
            raise SystemExit(f"--p2-csv not found: {p2_csv}")
        p2 = load_p2_robustness(p2_csv)
        p2_note = f"P2 robustness from {p2_csv.name} (card + worst-case corals, tail-loss model)."

    heat_dir = out_dir / "heatmaps"
    heat_dir.mkdir(exist_ok=True)
    subtitle = f"run={parent.name}  ROI 1600x900 native -> 1000x562 (1.6x)  |  msgs=ceil(base64/300), 5 s/msg"
    for mode in modes:
        make_source_quality_heatmap(rows, mode, source_names, qualities,
                                    heat_dir / f"heatmap_source_x_quality_{mode}.png", subtitle)
    make_fleet_summary_heatmap(rows, modes, qualities, n_corals,
                               heat_dir / "heatmap_fleet_summary_quality_x_mode.png", subtitle)

    ranking = build_ranking(rows, modes, qualities, p2)
    rank_fields = ["rank", "mode", "jpeg_quality", "eligible", "card_status", "card_min_tag_px", "card_msgs",
                   "corals_ideal", "corals_feasible_or_better", "corals_within_cap", "corals_over_cap",
                   "worst_coral", "worst_coral_msgs", "worst_coral_minutes",
                   "mean_coral_psnr", "mean_coral_chroma_sat",
                   "p2_card_pass_from_recv_pct", "p2_coral_psnr_at_50pct"]
    with (out_dir / "recommendation_ranked.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rank_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(ranking)
    log(f"ranked CSV: {out_dir / 'recommendation_ranked.csv'} ({len(ranking)} cells)")
    write_verdict_md(ranking, out_dir / "verdict.md", parent.name, n_corals, p2_note)

    manifest = {
        "tool": "bm_jpeg_p3_budget_verdict.py",
        "sprint": "Sprint06 P3 budget overlay + verdict",
        "created_utc": utc_stamp(),
        "platform": platform.platform(),
        "python": sys.version,
        "parent": str(parent),
        "p2_csv": str(args.p2_csv) if args.p2_csv else None,
        "sources": source_names,
        "modes": modes,
        "qualities": qualities,
        "bands_messages": {
            "ideal_max": BAND_IDEAL_MAX,
            "feasible_max": BAND_FEASIBLE_MAX,
            "gated_max_hard_cap": BAND_GATED_MAX,
            "note": "hard cap 195 field-tested by Nick (2026-07-22); supersedes spec ~180. Sweep CSVs' duration_band column (cap 180) preserved; this tool adds duration_band_cap195.",
        },
        "ranking_rule": "eligible = card PASS @100% AND worst coral <= 195 msgs; sort: corals feasible-or-better desc, corals within cap desc, P2 card-PASS-from-% asc (missing last), mean coral PSNR desc, worst-coral msgs asc",
        "row_count": len(rows),
        "outputs": {
            "combined_csv": str(out_dir / "combined_results_p3_verdict.csv"),
            "heatmaps": str(heat_dir),
            "recommendation_ranked_csv": str(out_dir / "recommendation_ranked.csv"),
            "verdict_md": str(out_dir / "verdict.md"),
        },
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    log("complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
