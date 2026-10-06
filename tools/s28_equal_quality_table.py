#!/usr/bin/env python3
# filename: s28_equal_quality_table.py
# description: Sprint28: collect the s28_density_sweep.py --equal-quality runs (B3a at 1600 bisected to the JPEG's SSIMULACRA2) into equal_quality_<tag>.csv / .md with per-band and all-frame P50 / P90.
"""
Inputs:  --runs (tg7_<stem>/equal_quality.json), --select (tg7_selection.json), --tag.
Outputs: equal_quality_<tag>.csv, equal_quality_<tag>.md; prints the P50 / P90 line.
P90 of bytes / msgs / ratio = the 90th percentile (the frame that needs MORE bytes): 90 % of
frames reach the JPEG's SSIMULACRA2 at or below it.
Example: python3 tools/s28_equal_quality_table.py --runs runs/s28_density_sweep_20261005 \\
           --select runs/s28_density_sweep_20261005/tg7_selection.json --tag tg7
"""

import argparse
import csv
import json
import os
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
    ap.add_argument("--tag", default="tg7")
    args = ap.parse_args(argv)
    with open(args.select) as fh:
        sel = {f"tg7_{r['stem']}": r for r in json.load(fh)}
    rows = []
    for lab in sorted(sel, key=lambda l: float(sel[l]["water_depth_m"])):
        path = os.path.join(args.runs, lab, "equal_quality.json")
        if not os.path.isfile(path):
            print(f"[EQ] missing {path}", file=sys.stderr)
            continue
        with open(path) as fh:
            e = json.load(fh)
        j = e["jpeg"]
        rows.append({"scene": lab, "band_m": sel[lab]["band_m"], "depth_m": sel[lab]["water_depth_m"],
                     "jpeg_q": j["quality"], "jpeg_bytes": j["bytes"], "jpeg_msgs": j["msgs"],
                     "jpeg_ssimulacra2": j["ssimulacra2"], "b3a_distance": e.get("distance"),
                     "b3a_bytes": e.get("bytes"), "b3a_msgs": e.get("msgs"),
                     "b3a_ssimulacra2": e.get("ssimulacra2"),
                     "b3a_butteraugli_3norm": e.get("butteraugli_3norm"),
                     "jpeg_butteraugli_3norm": j["butteraugli_3norm"],
                     "bytes_ratio_vs_jpeg": e.get("bytes_ratio_vs_jpeg"), "note": e.get("note", "")})
    with open(os.path.join(args.runs, f"equal_quality_{args.tag}.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    ok = [r for r in rows if r["b3a_bytes"]]

    def line(sub, name):
        return (f"| {name} | {len(sub)} | {pct([r['b3a_bytes'] for r in sub], 50) / 1000:.1f} / "
                f"{pct([r['b3a_bytes'] for r in sub], 90) / 1000:.1f} | "
                f"{pct([r['b3a_msgs'] for r in sub], 50):.0f} / {pct([r['b3a_msgs'] for r in sub], 90):.0f} | "
                f"{pct([r['bytes_ratio_vs_jpeg'] for r in sub], 50):.2f} / "
                f"{pct([r['bytes_ratio_vs_jpeg'] for r in sub], 90):.2f} | "
                f"{pct([r['jpeg_msgs'] for r in sub], 50):.0f} | "
                f"{pct([r['b3a_distance'] for r in sub], 50):.2f} |")
    md = [f"# Sprint28 equal quality ({args.tag}): B3a at 1600x900 native vs today's JPEG", "",
          "Per frame, the largest JPEG XL distance whose B3a decode (linear RGB, WB as a coding "
          "transform, cjxl VarDCT e5) still scores SSIMULACRA2 >= today's JPEG of the same ROI "
          "(1000x562 on the ladder, <= 195 msgs, upsampled), both vs the lossless neutral render. "
          "Bytes include a nominal 60 B container header. TG-7 resampled to IMX708 geometry, an "
          "approximation. P90 = the 90th percentile (the frames that need more).", "",
          "| set | frames | KB P50 / P90 | msgs P50 / P90 | bytes vs JPEG P50 / P90 | JPEG msgs P50 | "
          "d P50 |", "|---|---|---|---|---|---|---|", line(ok, "all frames")]
    for band in sorted({r["band_m"] for r in ok}, key=lambda b: float(b.split("-")[0])):
        md.append(line([r for r in ok if r["band_m"] == band], f"{band} m"))
    md += ["", "| scene | depth m | JPEG q / KB / msgs / s2 | B3a d / KB / msgs / s2 | ratio |",
           "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['scene']} | {r['depth_m']} | q{r['jpeg_q']} / {r['jpeg_bytes'] / 1000:.1f} / "
                  f"{r['jpeg_msgs']} / {r['jpeg_ssimulacra2']:.1f} | "
                  + (f"{r['b3a_distance']:.2f} / {r['b3a_bytes'] / 1000:.1f} / {r['b3a_msgs']} / "
                     f"{r['b3a_ssimulacra2']:.1f} | {r['bytes_ratio_vs_jpeg']:.2f} |" if r["b3a_bytes"]
                     else f"{r['note']} | |"))
    with open(os.path.join(args.runs, f"equal_quality_{args.tag}.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print(md[6])
    return 0


if __name__ == "__main__":
    sys.exit(main())
