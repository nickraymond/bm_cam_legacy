#!/usr/bin/env python3
# filename: s28_crop_cost.py
# description: Sprint28 crop sweep feasibility: measure cjxl VarDCT (RGB route) and the production 4-plane modular encode on the Mac per crop size (time, peak RSS), and scale to a labelled Pi Zero 2 W ESTIMATE.
"""
Measured here (Mac, single thread, --num_threads=0): wall time and peak RSS of ONE cjxl run per
crop size, for (a) the RGB route: one 3-channel 16-bit PPM, -m 0 -e 5 at a d that lands near the
195-msg target, and (b) the production 4-plane modular encode (-m 1 -e 5) at d 15 (the largest
d). Peak RSS from `/usr/bin/time -l` (macOS: bytes).
ESTIMATED for the Pi Zero 2 W (labelled ESTIMATE in every output):
  time  = Mac time x PI_TIME_RATIO. PI_TIME_RATIO = 11.9 = the production 1600x900 4-plane rung
          on the Pi (~6.4 s, Sprint28 study / S0 calibration) / the same encode here (0.54 s).
  RSS   = Mac peak RSS (cjxl's working set is mostly its own image buffers, similar on ARM64).
  prep  = the numpy side of the RGB route on the unit: demosaic + WB + LUT of a w x h crop,
          float32 RGB (12 B/px) + uint16 codes (6 B/px) + the uint16 mosaic (2 B/px).
  The guard: the unit's encoder runs under `ulimit -v 256000` (250 MB virtual, rc_raw_jxl
  GUARD_SH): a peak above ~200 MB RSS is flagged as "kills under today's guard".
Inputs: --ppm-from (a TG-7 ORF via the rig mapping) and --rig. Outputs: crop_cost.csv/.json.
Example: PYTHONPATH=$RIG/src:$RIG <rig venv python> tools/s28_crop_cost.py --rig $RIG \\
           --orf X.orf --out runs/s28_crop_sweep_20261005
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROPS = [(1600, 900), (1920, 1080), (2304, 1296), (2880, 1620), (3200, 1800), (4608, 2592)]
PI_TIME_RATIO = 11.9
GUARD_RSS_MB = 200


def timed(cmd):
    t0 = time.time()
    r = subprocess.run(["/usr/bin/time", "-l"] + cmd, capture_output=True, text=True)
    wall = time.time() - t0
    m = re.search(r"(\d+)\s+maximum resident set size", r.stderr)
    return wall, (int(m.group(1)) / 1e6 if m else None), r.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rig", required=True)
    ap.add_argument("--orf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rgb-d", type=float, default=None,
                    help="VarDCT distance (default: per crop, from a quick fit)")
    args = ap.parse_args(argv)
    sys.path[:0] = [os.path.join(args.rig, "src"), args.rig, os.path.join(REPO, "BM_Devel_Pi")]
    import numpy as np
    from compression_study.preview_loss import tg7_budget as TG
    from host_tools.tg7.orf_io import read_orf
    import rc_raw_jxl as X

    f = read_orf(args.orf)
    tmp = tempfile.mkdtemp(prefix="s28cc_")
    rows = []
    for rw, rh in CROPS:
        scene, _, _ = TG.map_to_imx(f, (rw, rh))
        mos = scene["mosaic"]
        # RGB route input: a cheap stand-in demosaic (2x2 box) is enough for timing / memory
        m = mos.astype(np.float32)
        rgb = np.stack([np.repeat(np.repeat(m[0::2, 1::2], 2, 0), 2, 1),
                        np.repeat(np.repeat(m[0::2, 0::2], 2, 0), 2, 1),
                        np.repeat(np.repeat(m[1::2, 0::2], 2, 0), 2, 1)], -1)[:rh, :rw]
        lut = X.sqrt_lut(scene["black"], scene["white"])
        codes = lut[np.clip(rgb, 0, scene["white"]).astype(np.int32)]
        ppm = os.path.join(tmp, "x.ppm")
        with open(ppm, "wb") as fh:
            fh.write(f"P6\n{rw} {rh}\n4095\n".encode())
            fh.write(np.ascontiguousarray(codes, dtype=">u2").tobytes())
        d = args.rgb_d or {1600: 1.6, 1920: 2.6, 2304: 4.0, 2880: 8.7, 3200: 11.0,
                           4608: 25.0}[rw]
        wall_v, rss_v, rc_v = timed(["cjxl", ppm, os.path.join(tmp, "x.jxl"), "-m", "0", "-e",
                                     "5", "-d", str(d), "--num_threads=0", "--quiet"])
        pgm = os.path.join(tmp, "p.pgm")
        X.write_pgm(pgm, X.code_planes({"mosaic": mos, "cfa": scene["cfa"],
                                        "black": scene["black"], "white": scene["white"]})["G1"])
        wall_m, rss_m, rc_m = timed(["cjxl", pgm, os.path.join(tmp, "p.jxl"), "-m", "1", "-e",
                                     "5", "-d", "15", "--num_threads=0", "--quiet"])
        prep_mb = rw * rh * (12 + 6 + 2) / 1e6
        r = {"crop": f"{rw}x{rh}", "megapixels": round(rw * rh / 1e6, 2),
             "mac_vardct_rgb_s": round(wall_v, 2), "mac_vardct_rgb_rss_mb": round(rss_v, 1),
             "vardct_d": d, "mac_modular_1plane_s": round(wall_m, 2),
             "mac_modular_1plane_rss_mb": round(rss_m, 1),
             "EST_pi_vardct_s": round(wall_v * PI_TIME_RATIO, 1),
             "EST_pi_modular_4plane_s": round(4 * wall_m * PI_TIME_RATIO, 1),
             "EST_pi_prep_numpy_mb": round(prep_mb, 1),
             "EST_pi_peak_mb": round(max(rss_v, prep_mb), 1)}
        r["EST_kills_under_250MB_guard"] = rss_v > GUARD_RSS_MB
        r["EST_fits_512MB_unit"] = (rss_v + prep_mb) < 300
        rows.append(r)
        print(f"[COST] {r}", flush=True)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "crop_cost.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    with open(os.path.join(args.out, "crop_cost.json"), "w") as fh:
        json.dump({"note": "mac_* MEASURED on the Mac (single thread); EST_* are ESTIMATES for "
                           f"the Pi Zero 2 W (time x {PI_TIME_RATIO}; RSS as on the Mac; numpy "
                           "prep 20 B/px); NOT measured on a unit", "rows": rows}, fh, indent=1)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
