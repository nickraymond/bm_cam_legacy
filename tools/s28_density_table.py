#!/usr/bin/env python3
# filename: s28_density_table.py
# description: Sprint28 density sweep: collect s28_density_sweep.py runs into the options table (width x scene per row), per depth band, P50/P90 over frames, and the slider manifest.json.
"""
Inputs:  --runs <folder of s28_density_sweep.py runs> (each <label>/summary.json),
         --select <json list of {stem, band_m, water_depth_m, shutter, iso, category, ...}>
         (TG-7 frames; labels are tg7_<stem>), --prefix (which labels: tg7_ or study_),
         --slider <labels whose slider assets are listed>, --tag (output file suffix).
Outputs (in --runs): options_table_<tag>.csv / .md (scene x row x width: d, bytes, msgs,
         SSIMULACRA2, butteraugli 3-norm, delta vs JPEG, PASS / SIG), the per-band and all-frame
         P50 / P90 of the deltas, the largest width that is SIG at P50 / P90 per row, and
         manifest_<tag>.json (slider files + scores, every image a 1600x900 display render).
Example: python3 tools/s28_density_table.py --runs runs/s28_density_sweep_20261005 \\
           --prefix study_ --tag study
Bars: PASS = SSIMULACRA2 >= JPEG and butteraugli 3-norm <= JPEG; SIG = SSIMULACRA2 >= JPEG + 5
      and butteraugli 3-norm <= 0.9 x JPEG (Nick, 2026-10-05). P50/P90 are over frames of the
      per-frame delta (s2 - JPEG s2) and ratio (b3 / JPEG b3); "SIG at P90" uses the 10th
      percentile of the delta and the 90th of the ratio (90 % of frames at least that good).
"""

import argparse
import csv
import json
import os
import statistics
import sys

SIG_S2, SIG_B3 = 5.0, 0.9


def pct(vals, p):
    v = sorted(vals)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 3)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--select")
    ap.add_argument("--slider", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--plot-row", default="", help="draw curve_<tag>.png for this row")
    ap.add_argument("--slider-rows", default="reference,jpeg,bayer,rgbwa",
                    help="which rows' slider files the manifest lists")
    args = ap.parse_args(argv)
    sel = {}
    if args.select:
        with open(args.select) as fh:
            sel = {f"tg7_{r['stem']}": r for r in json.load(fh)}
    runs = sorted(d for d in os.listdir(args.runs) if d.startswith(args.prefix)
                  and os.path.isfile(os.path.join(args.runs, d, "summary.json")))
    if not runs:
        print(f"[TAB] no runs with prefix {args.prefix!r} in {args.runs}", file=sys.stderr)
        return 1
    table, per = [], {}
    order = (lambda d: (float(sel[d]["water_depth_m"]), d)) if sel else (lambda d: d)
    for lab in sorted(runs, key=order):
        with open(os.path.join(args.runs, lab, "summary.json")) as fh:
            s = json.load(fh)
        j = s["jpeg"]
        info = sel.get(lab, {})
        for r in s["rows"]:
            if r["row"] == "jpeg":
                continue
            rec = {"scene": lab, "band_m": info.get("band_m", ""),
                   "depth_m": info.get("water_depth_m", ""), "shutter": info.get("shutter", ""),
                   "iso": info.get("iso", ""), "category": info.get("category", ""),
                   "row": r["row"], "width": r["width"], "height": r["height"],
                   "distance": r["distance"], "bytes": r["bytes"], "msgs": r["msgs"],
                   "ssimulacra2": r["ssimulacra2"], "butteraugli_3norm": r["butteraugli_3norm"],
                   "jpeg_q": j["quality"], "jpeg_bytes": j["bytes"], "jpeg_msgs": j["msgs"],
                   "jpeg_ssimulacra2": j["ssimulacra2"], "jpeg_butteraugli_3norm": j["butteraugli_3norm"],
                   "d_s2": r["d_s2"], "b3_ratio": r["b3_ratio"], "pass": r["pass"], "sig": r["sig"],
                   "prod_distance": r.get("prod_distance", "")}
            table.append(rec)
            per.setdefault((r["row"], r["width"]), []).append(rec)
    keys = list(table[0])
    with open(os.path.join(args.runs, f"options_table_{args.tag}.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        wr.writerows(table)

    rows = sorted({r for r, _ in per}, key=lambda r: ("bayer", "rgb", "rgbv", "rgbw", "rgbwi", "rgbwa").index(r)
                  if r in ("bayer", "rgb", "rgbv", "rgbw", "rgbwi", "rgbwa") else 9)
    widths = sorted({w for _, w in per}, reverse=True)
    label = {"bayer": "A Bayer-plane resample (production 4-plane cjxl -m 1)",
             "rgb": "B linear RGB, cjxl modular -m 1", "rgbv": "B' linear RGB, cjxl VarDCT -m 0",
             "rgbw": "B\" WB applied before coding, CLIPPED at 1, cjxl VarDCT -m 0",
             "rgbwi": "B3i WB as a coding transform / max gain, undone at decode (no clip)",
             "rgbwa": "B3a WB as a coding transform / frame WB peak, undone at decode (no clip; "
                      "CANDIDATE)"}
    md = [f"# Sprint28 density sweep: {args.tag}", "",
          "ROI [1504, 846, 1600, 900] (native IMX708 px) and the 195-msg cap fixed; nrjxl byte "
          "target = production's 0.97 fill (54475 B). Every image UPSAMPLED (lanczos) to "
          "1600x900 for scoring. Reference = the lossless neutral render at native 1600x900. "
          f"PASS = s2 >= JPEG and b3 <= JPEG; SIG = s2 >= JPEG+{SIG_S2:g} and b3 <= "
          f"{SIG_B3:g} x JPEG. {args.note}", ""]

    def summary_block(recs_by, title):
        out = [f"## {title}", "",
               "| row | width | d P50 | s2 - JPEG P50 / P10 | b3 / JPEG P50 / P90 | PASS | SIG | "
               "SIG at P50 | SIG at P90 |", "|---|---|---|---|---|---|---|---|---|"]
        best = {}
        for row in rows:
            for w in widths:
                recs = recs_by.get((row, w), [])
                if not recs:
                    continue
                ds = [r["d_s2"] for r in recs]
                br = [r["b3_ratio"] for r in recs]
                dd = [float(r["distance"]) for r in recs]
                s50 = pct(ds, 50) >= SIG_S2 and pct(br, 50) <= SIG_B3
                s90 = pct(ds, 10) >= SIG_S2 and pct(br, 90) <= SIG_B3
                out.append(f"| {row} | {w} | {statistics.median(dd):.2f} | {pct(ds, 50):+.1f} / "
                           f"{pct(ds, 10):+.1f} | {pct(br, 50):.2f} / {pct(br, 90):.2f} | "
                           f"{sum(r['pass'] for r in recs)}/{len(recs)} | "
                           f"{sum(r['sig'] for r in recs)}/{len(recs)} | {'yes' if s50 else 'no'} "
                           f"| {'yes' if s90 else 'no'} |")
                if s50:
                    best.setdefault(row, {}).setdefault("p50", w)
                if s90:
                    best.setdefault(row, {}).setdefault("p90", w)
        out.append("")
        for row in rows:
            b = best.get(row, {})
            out.append(f"- **{label.get(row, row)}**: largest width SIG at P50 = "
                       f"{b.get('p50', 'none')}, at P90 = {b.get('p90', 'none')}")
        out.append("")
        return out, best

    allmd, best_all = summary_block(per, f"All frames ({len(runs)}): P50 / P90 over frames")
    md += allmd
    bands = sorted({r["band_m"] for r in table if r["band_m"]},
                   key=lambda b: float(b.split("-")[0]))
    for band in bands:
        sub = {}
        for r in table:
            if r["band_m"] == band:
                sub.setdefault((r["row"], r["width"]), []).append(r)
        md += summary_block(sub, f"Depth band {band} m")[0]

    md += ["## Per frame", "",
           "| scene | depth m | shutter | JPEG q / msgs / s2 / b3 | row | " +
           " | ".join(str(w) for w in widths) + " |",
           "|---|---|---|---|---|" + "---|" * len(widths)]
    for lab in sorted(runs, key=order):
        for row in rows:
            recs = {r["width"]: r for r in table if r["scene"] == lab and r["row"] == row}
            if not recs:
                continue
            j = next(iter(recs.values()))
            cells = []
            for w in widths:
                r = recs.get(w)
                cells.append("" if r is None else
                             f"d {float(r['distance']):.2f}: {r['ssimulacra2']:.1f} / "
                             f"{r['butteraugli_3norm']:.2f} "
                             f"{'SIG' if r['sig'] else 'PASS' if r['pass'] else 'FAIL'}")
            md.append(f"| {lab} | {j['depth_m']} | {j['shutter']} | q{j['jpeg_q']} / "
                      f"{j['jpeg_msgs']} / {j['jpeg_ssimulacra2']:.1f} / "
                      f"{j['jpeg_butteraugli_3norm']:.2f} | {row} | " + " | ".join(cells) + " |")
    with open(os.path.join(args.runs, f"options_table_{args.tag}.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")

    slider = []
    for lab in [x for x in args.slider.split(",") if x]:
        with open(os.path.join(args.runs, lab, "summary.json")) as fh:
            s = json.load(fh)
        slider.append({"scene": lab, **({k: sel[lab].get(k) for k in
                                          ("stem", "band_m", "water_depth_m", "shutter", "iso",
                                           "category")} if lab in sel else {}),
                       "source": s.get("source"),
                       "files": [x for x in s["slider"]
                                 if x.get("row") in args.slider_rows.split(",")]})
    manifest = {"what": f"Sprint28 density sweep ({args.tag})",
                "display": "every file is a 1600x900 display render, JPEG q92; non-native "
                           "images were UPSAMPLED (lanczos) to 1600x900; scores vs the lossless "
                           "neutral render at native 1600x900",
                "bars": {"pass": "s2 >= JPEG and b3 <= JPEG",
                         "sig": f"s2 >= JPEG+{SIG_S2:g} and b3 <= {SIG_B3:g} x JPEG"},
                "frames": [{"scene": lab, **sel.get(lab, {})} for lab in sorted(runs, key=order)],
                "largest_sig_width": best_all, "slider": slider, "note": args.note}
    with open(os.path.join(args.runs, f"manifest_{args.tag}.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    if args.plot_row:
        plot_curve(args, table, per, widths, bands)
    print("\n".join(allmd))
    return 0


def plot_curve(args, table, per, widths, bands):
    """The headline: margin over today's JPEG vs output width at the 195 cap, per depth band
    (P50 of frames) and all frames (P50 and the P90-safe line), one panel per metric."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    row = args.plot_row
    ws = sorted(w for (r, w) in per if r == row)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
    for band in bands:
        y1, y2 = [], []
        for w in ws:
            recs = [r for r in per[(row, w)] if r["band_m"] == band]
            y1.append(pct([r["d_s2"] for r in recs], 50))
            y2.append(pct([r["b3_ratio"] for r in recs], 50))
        a1.plot(ws, y1, marker="o", lw=1, label=f"{band} m P50")
        a2.plot(ws, y2, marker="o", lw=1, label=f"{band} m P50")
    for p, style, lab in ((50, "-", "all frames P50"), (10, "--", "all frames P90 (10th pct)")):
        a1.plot(ws, [pct([r["d_s2"] for r in per[(row, w)]], p) for w in ws], "k" + style, lw=2.5,
                label=lab)
    for p, style, lab in ((50, "-", "all frames P50"), (90, "--", "all frames P90")):
        a2.plot(ws, [pct([r["b3_ratio"] for r in per[(row, w)]], p) for w in ws], "k" + style,
                lw=2.5, label=lab)
    bayer = per.get(("bayer", 1600))
    if bayer and row != "bayer":
        a1.plot([1600], [pct([r["d_s2"] for r in bayer], 50)], "rs", ms=9,
                label="Bayer 1600 (RAW anchor) P50")
        a2.plot([1600], [pct([r["b3_ratio"] for r in bayer], 50)], "rs", ms=9,
                label="Bayer 1600 (RAW anchor) P50")
    a1.axhline(SIG_S2, color="g", ls=":", label=f"SIG bar +{SIG_S2:g}")
    a1.axhline(0, color="grey", lw=0.8)
    a2.axhline(SIG_B3, color="g", ls=":", label=f"SIG bar x{SIG_B3:g}")
    a2.axhline(1, color="grey", lw=0.8)
    a1.set(xlabel="nrjxl output width of the 1600x900 ROI (px)",
           ylabel="SSIMULACRA2 - today's JPEG (higher = better)")
    a2.set(xlabel="nrjxl output width of the 1600x900 ROI (px)",
           ylabel="butteraugli 3-norm / today's JPEG (lower = better)")
    for a in (a1, a2):
        a.set_xticks(ws)
        a.grid(alpha=0.3)
        a.legend(fontsize=7)
    fig.suptitle(f"Sprint28 density sweep ({args.tag}): nrjxl {row} vs today's pjpg (1000x562), "
                 f"both at <= 195 msgs; all images UPSAMPLED to 1600x900 for scoring. "
                 f"{args.note}"[:200], fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(args.runs, f"curve_{args.tag}.png"), dpi=110)


if __name__ == "__main__":
    sys.exit(main())
