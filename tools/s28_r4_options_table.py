#!/usr/bin/env python3
# filename: s28_r4_options_table.py
# description: Sprint28 R4 options — collect every s28_r4_options.py run (scene x demosaic) into one table: per message budget, how many scenes have nrjxl >= today's JPEG.
"""
Inputs:  --runs runs/s28_r4_options_20261005 (each subfolder = one s28_r4_options.py run with
         scores.csv + summary.json).
Outputs: options_table.csv (scene, demosaic, today's JPEG q/bytes/msgs/scores, per budget the
         nrjxl d / SSIMULACRA2 / butteraugli / PASS), options_table.md (the same, readable),
         printed summary: smallest passing budget per scene and the count per budget.
Example: .venv-dev/bin/python tools/s28_r4_options_table.py --runs runs/s28_r4_options_20261005
"""

import argparse
import csv
import json
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", required=True)
    args = ap.parse_args(argv)
    runs = sorted(d for d in os.listdir(args.runs)
                  if os.path.isfile(os.path.join(args.runs, d, "scores.csv"))
                  and (d.endswith("_bilinear") or d.endswith("_ea")))
    table, budgets = [], set()
    for d in runs:
        with open(os.path.join(args.runs, d, "scores.csv")) as fh:
            rows = list(csv.DictReader(fh))
        today = next(r for r in rows if r["option"].startswith("today"))
        scene, dm = d.rsplit("_", 1)
        rec = {"scene": scene, "demosaic": dm, "jpeg_q": today["quality"],
               "jpeg_bytes": today["bytes"], "jpeg_msgs": today["msgs"],
               "jpeg_ssimulacra2": today["ssimulacra2"], "jpeg_b3": today["butteraugli_3norm"]}
        for r in rows:
            if r["option"].startswith("(a)") and r.get("bytes"):
                b = int(r["budget_msgs"])
                budgets.add(b)
                rec[f"{b}_d"] = r["distance"]
                rec[f"{b}_s2"] = r["ssimulacra2"]
                rec[f"{b}_b3"] = r["butteraugli_3norm"]
                rec[f"{b}_pass"] = r["pass_vs_jpeg"]
        passing = [b for b in sorted(budgets) if rec.get(f"{b}_pass") == "True"]
        rec["smallest_pass_msgs"] = passing[0] if passing else ">max"
        table.append(rec)
    budgets = sorted(budgets)
    keys = list(table[0].keys()) if table else []
    for t in table:
        for k in t:
            if k not in keys:
                keys.append(k)
    out_csv = os.path.join(args.runs, "options_table.csv")
    with open(out_csv, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        wr.writerows(table)
    lines = ["| scene | demosaic | today's JPEG (q, msgs, SSIMULACRA2 / butteraugli) | "
             + " | ".join(f"nrjxl @{b}" for b in budgets) + " | smallest PASS |",
             "|---|---|---|" + "---|" * len(budgets) + "---|"]
    for t in table:
        cells = []
        for b in budgets:
            if f"{b}_s2" not in t:
                cells.append("n/a")
                continue
            mark = "PASS" if t[f"{b}_pass"] == "True" else "FAIL"
            cells.append(f"d {float(t[f'{b}_d']):.2f}: {float(t[f'{b}_s2']):.1f} / "
                         f"{float(t[f'{b}_b3']):.2f} {mark}")
        lines.append(f"| {t['scene']} | {t['demosaic']} | q{t['jpeg_q']}, {t['jpeg_msgs']}, "
                     f"{float(t['jpeg_ssimulacra2']):.1f} / {float(t['jpeg_b3']):.2f} | "
                     + " | ".join(cells) + f" | {t['smallest_pass_msgs']} |")
    for dm in ("bilinear", "ea"):
        sub = [t for t in table if t["demosaic"] == dm]
        counts = [sum(1 for t in sub if t.get(f"{b}_pass") == "True") for b in budgets]
        lines.append(f"| **{dm}: scenes passing** | | | "
                     + " | ".join(f"{c}/{len(sub)}" for c in counts) + " | |")
    md = "\n".join(lines) + "\n"
    with open(os.path.join(args.runs, "options_table.md"), "w") as fh:
        fh.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
