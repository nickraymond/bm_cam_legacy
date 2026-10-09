#!/usr/bin/env python3
# filename: replay_p.py
# description: replay the PRODUCTION rc_raw_jxl.choose_rate (tools/s28_b3a_search_calib.simulate_one) on the 31 measured B3a bytes(d) curves with every curve scaled by the cjxl -p overhead (x1.016 median, x1.027 max, desk study 2026-10-09), with and without scaling the prior curve the same way.
# Inputs: runs/s28_b3a_search_20261006/curves/*.json. Output: stdout (replay.txt). Run from the repo root.
# Limits: a uniform overhead per frame is an assumption (the study measured 0.5-2.7 %).
import glob, json, sys, os, collections, statistics
sys.path.insert(0, "tools"); sys.path.insert(0, "BM_Devel_Pi")
import s28_b3a_search_calib as C, rc_raw_jxl as X
curves = [json.load(open(p)) for p in sorted(glob.glob("runs/s28_b3a_search_20261006/curves/*.json"))]
base_curve = X.PRIOR_RGB_CURVE
for over in (1.0, 1.016, 1.027):
    for prior_scale in (1.0, over):
        X.PRIOR_RGB_CURVE = tuple((d, b * prior_scale) for d, b in base_curve)
        rows = []
        for c in curves:
            c2 = dict(c, e5=[[p[0], p[1] * over, *p[2:]] for p in c["e5"]])
            rows.append(C.simulate_one(c2, encode_max_s=45))
        att = collections.Counter(r["attempts"] for r in rows)
        fills = [r["fill"] for r in rows if r["fill"]]
        print(f"overhead x{over} prior x{prior_scale}: attempts {dict(sorted(att.items()))} "
              f"fill min {min(fills)} P50 {statistics.median(fills)} max {max(fills)} rfb {[r['rfb'] for r in rows if r['rfb']]}")
X.PRIOR_RGB_CURVE = base_curve
