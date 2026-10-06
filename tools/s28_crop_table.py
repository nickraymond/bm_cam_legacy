#!/usr/bin/env python3
# filename: s28_crop_table.py
# description: Sprint28 crop-size sweep: collect s28_crop_sweep.py runs into the table (crop x row: central vs today's JPEG of that region, whole frame vs today's JPEG of the same FOV), per depth band, the largest crop >= JPEG at P90, and the slider manifest.
"""
Inputs:  --runs (each tg7_<stem>/summary.json), --select (tg7_selection.json), --cost
         (crop_cost.json from tools/s28_crop_cost.py, optional), --slider (labels).
Outputs: crop_table.md / crop_table.csv, manifest_crop.json (same-FOV slider files).
Bars (central 1600x900 of each crop vs the same region of the crop's lossless render):
  PASS = SSIMULACRA2 >= JPEG's AND butteraugli 3-norm <= JPEG's (JPEG = today's pjpg of that
  region, 1000x562, upsampled); SIG = +5 / x0.9. "at P90" = the 10th percentile of the s2
  delta >= 0 (resp. +5) AND the 90th percentile of the b3 ratio <= 1 (resp. 0.9).
Example: python3 tools/s28_crop_table.py --runs runs/s28_crop_sweep_20261005 \\
           --select runs/s28_crop_sweep_20261005/tg7_selection.json
"""

import argparse
import csv
import json
import os
import statistics
import sys


def pct(vals, p):
    v = sorted(vals)
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 3)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", required=True)
    ap.add_argument("--select", required=True)
    ap.add_argument("--cost")
    ap.add_argument("--slider", default="")
    args = ap.parse_args(argv)
    with open(args.select) as fh:
        sel = {f"tg7_{r['stem']}": r for r in json.load(fh)}
    labs = sorted((d for d in os.listdir(args.runs)
                   if os.path.isfile(os.path.join(args.runs, d, "summary.json"))),
                  key=lambda d: float(sel[d]["water_depth_m"]))
    table = []
    for lab in labs:
        with open(os.path.join(args.runs, lab, "summary.json")) as fh:
            s = json.load(fh)
        jp = {r["crop"]: r for r in s["rows"] if r["row"] == "jpeg_central"}
        for r in s["rows"]:
            if r["row"] == "jpeg_central":
                continue
            j = jp[r["crop"]]
            table.append({"scene": lab, "band_m": sel[lab]["band_m"],
                          "depth_m": sel[lab]["water_depth_m"], "crop": r["crop"], "row": r["row"],
                          "fits": r["fits"], "distance": r["distance"], "bytes": r["bytes"],
                          "msgs": r["msgs"], "ssimulacra2": r["ssimulacra2"],
                          "butteraugli_3norm": r["butteraugli_3norm"],
                          "jpeg_q": j["quality"], "jpeg_ssimulacra2": j["ssimulacra2"],
                          "jpeg_butteraugli_3norm": j["butteraugli_3norm"], "d_s2": r["d_s2"],
                          "b3_ratio": r["b3_ratio"], "pass": r["pass"], "sig": r["sig"],
                          "whole_ssimulacra2": r["whole_ssimulacra2"],
                          "whole_jpeg_sameFOV_ssimulacra2": r["whole_jpeg_sameFOV_ssimulacra2"]})
    with open(os.path.join(args.runs, "crop_table.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(table[0]))
        wr.writeheader()
        wr.writerows(table)
    cost = {}
    if args.cost and os.path.isfile(args.cost):
        with open(args.cost) as fh:
            cost = {r["crop"]: r for r in json.load(fh)["rows"]}
    crops = sorted({r["crop"] for r in table}, key=lambda c: int(c.split("x")[0]))
    rows = [r for r in ("rgbwa", "rgbw", "bayer") if any(t["row"] == r for t in table)]
    md = ["# Sprint28 crop-size sweep (TG-7, 195 msgs, native density)", "",
          "Centred 16:9 crops at NATIVE IMX708 density; TG-7 resampled to IMX708 geometry, an "
          "approximation. Byte target 54475 B (0.97 fill of 195 msgs). CENTRAL = the central "
          "1600x900 of the decoded crop vs the same region of the crop's lossless render, against "
          "today's JPEG of that region (1000x562, upsampled). WHOLE = the whole decoded crop vs its "
          "lossless render (SSIMULACRA2), next to today's JPEG of the SAME FOV (the crop at 1000 px "
          "wide, upsampled). rgbwa = linear RGB, WB as a coding transform (no clip), cjxl VarDCT; "
          "bayer = the production 4-plane encoder (d <= 15).", ""]

    def block(sub, title):
        out = [f"## {title}", "",
               "| row | crop | fits | d P50 | KB P50 | central s2-JPEG P50 / P10 | b3/JPEG P50 / P90 "
               "| PASS | SIG | >=JPEG at P90 | SIG at P90 | whole s2 P50 (same-FOV JPEG P50) |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        best = {}
        for row in rows:
            for c in crops:
                rs = [t for t in sub if t["row"] == row and t["crop"] == c]
                if not rs:
                    continue
                ds, br = [t["d_s2"] for t in rs], [t["b3_ratio"] for t in rs]
                fits = sum(t["fits"] for t in rs)
                p90 = fits == len(rs) and pct(ds, 10) >= 0 and pct(br, 90) <= 1
                s90 = fits == len(rs) and pct(ds, 10) >= 5 and pct(br, 90) <= 0.9
                if p90:
                    best.setdefault(row, {})["pass"] = (c, statistics.median(
                        [float(t["distance"]) for t in rs]), statistics.median([t["bytes"] for t in rs]))
                if s90:
                    best.setdefault(row, {})["sig"] = c
                out.append(f"| {row} | {c} | {fits}/{len(rs)} | "
                           f"{statistics.median([float(t['distance']) for t in rs]):.2f} | "
                           f"{statistics.median([t['bytes'] for t in rs]) / 1000:.1f} | "
                           f"{pct(ds, 50):+.1f} / {pct(ds, 10):+.1f} | {pct(br, 50):.2f} / "
                           f"{pct(br, 90):.2f} | {sum(t['pass'] for t in rs)}/{len(rs)} | "
                           f"{sum(t['sig'] for t in rs)}/{len(rs)} | {'yes' if p90 else 'no'} | "
                           f"{'yes' if s90 else 'no'} | "
                           f"{pct([t['whole_ssimulacra2'] for t in rs], 50):.1f} "
                           f"({pct([t['whole_jpeg_sameFOV_ssimulacra2'] for t in rs], 50):.1f}) |")
        out.append("")
        for row in rows:
            b = best.get(row, {})
            p = b.get("pass")
            out.append(f"- **{row}**: largest crop >= JPEG at P90 = "
                       + (f"{p[0]} (d P50 {p[1]:.2f}, {p[2] / 1000:.1f} KB)" if p else "none")
                       + f"; largest SIG at P90 = {b.get('sig', 'none')}")
        out.append("")
        return out, best

    allb, best_all = block(table, f"All frames ({len(labs)})")
    md += allb
    for band in sorted({t["band_m"] for t in table}, key=lambda b: float(b.split("-")[0])):
        md += block([t for t in table if t["band_m"] == band], f"Depth band {band} m")[0]
    if cost:
        md += ["## Pi Zero 2 W feasibility per crop (RGB route)", "",
               "mac_* MEASURED on the Mac, single thread; EST_* are ESTIMATES (time x 11.9 = the "
               "measured Pi/Mac ratio of the production 4-plane rung; RSS as on the Mac; numpy prep "
               "20 B/px). The unit's encoder guard is ulimit -v 250 MB.", "",
               "| crop | MP | Mac VarDCT s / RSS MB | EST Pi VarDCT s (one encode) | EST Pi peak MB | "
               "kills under the 250 MB guard? | fits a 512 MB unit? |", "|---|---|---|---|---|---|---|"]
        for c in crops:
            r = cost.get(c)
            if r:
                md.append(f"| {c} | {r['megapixels']} | {r['mac_vardct_rgb_s']} / "
                          f"{r['mac_vardct_rgb_rss_mb']} | {r['EST_pi_vardct_s']} | "
                          f"{r['EST_pi_peak_mb']} | {'YES' if r['EST_kills_under_250MB_guard'] else 'no'} "
                          f"| {'yes' if r['EST_fits_512MB_unit'] else 'NO'} |")
        md.append("")
    with open(os.path.join(args.runs, "crop_table.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    slider = []
    for lab in [x for x in args.slider.split(",") if x]:
        with open(os.path.join(args.runs, lab, "summary.json")) as fh:
            s = json.load(fh)
        slider.append({"scene": lab, **sel[lab], "files": s["slider"]})
    with open(os.path.join(args.runs, "manifest_crop.json"), "w") as fh:
        json.dump({"what": "Sprint28 crop-size sweep: same-FOV slider (today's JPEG of the crop "
                           "vs nrjxl rgbwa of the crop at native density); files are JPEG q92 "
                           "display renders at the crop's native size",
                   "largest": best_all, "frames": [sel[l] for l in labs], "slider": slider}, fh,
                  indent=1, default=str)
    print("\n".join(allb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
