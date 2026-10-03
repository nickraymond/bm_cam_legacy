#!/usr/bin/env python3
# filename: s28_mac_e2e_check.py
# description: Sprint28 S1 gate — the production nrjxl encoder on the study's field crops, decoded by the rig's study decoder and scored with the study's colour metric, vs the study's D2 row.
"""
SPEC r4 §7.1 / §8 S1 gate: "Mac end-to-end: the production module on the study DNGs ->
blob -> rig decoder -> stress dE within +-0.02 of the study's D2 row at the same bytes."

For each study field frame set (IMX708, cool / warm, stop -1, air / uw):
  1. rebuild the study's own field context (rig compression_study.run_study: the card-
     centred 1600x900 crop, the noise model, the uw thinning with the study's seeds);
  2. code the crop's planes with the PRODUCTION path (BM_Devel_Pi/rc_raw_jxl.py: sqrt
     LUT, cjxl -m 1 -e 5 --num_threads=0, NR container v1 crc-v1b) at the distance whose
     blob is closest to the study row's byte count (bisection);
  3. decode the blob with the RIG's decoder (compression_study.methods.raw_planes.decode)
     and score it with the rig's metrics.evaluate (stress dE00 mean, block dE00 median);
  4. compare with the study's `D2/modular` row (results/results.csv, effort 7 on the Mac).

Run it with the rig's venv and the rig's origin/main sources (read-only copies):
  RIG=<dir with compression_study/ and src/ from rig origin/main 372d6f2>
  PYTHONPATH=$RIG/src:$RIG ~/Documents/GitHub/nereus-camera-test-rig/.venv/bin/python \\
      tools/s28_mac_e2e_check.py --rig $RIG \\
      --data ~/Documents/GitHub/nereus-camera-test-rig/data/s4_20260930 \\
      --out runs/s28_s1_mac_e2e_20261002
Outputs: e2e.csv (one row per frame set), run_manifest.json, e2e.log.
Exit 1 when any |dE(production) - dE(study)| > 0.02.
Known limitations: the study row is effort 7; production is effort 5 (the Pi setting).
The REPORT says e5 and e7 give the same quality at 1600x900; this run measures it.
"""

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOL = 0.02


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rig", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    sys.path[:0] = [os.path.join(args.rig, "src"), args.rig, os.path.join(REPO, "BM_Devel_Pi")]
    from pathlib import Path

    import numpy as np
    from compression_study import metrics, noise, rois, sim
    from compression_study import run_study as rs
    from compression_study.methods import raw_planes as rp

    import rc_raw_jxl as X

    os.makedirs(args.out, exist_ok=True)
    log = open(os.path.join(args.out, "e2e.log"), "w")

    def say(msg):
        print(msg, flush=True)
        log.write(msg + "\n")

    cjxl = shutil.which("cjxl")
    ver = subprocess.run([cjxl, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    study = {}
    with open(os.path.join(args.rig, "compression_study", "results", "results.csv")) as fh:
        for row in csv.DictReader(fh):
            if row["variant"] == "D2/modular" and row["fsid"].endswith("_field"):
                study[row["fsid"]] = row
    data = Path(args.data)
    roi_all = rois.load(Path(args.rig) / "compression_study" / "config" / "card_rois.yaml")
    meta = {"ExposureTime": 1, "AnalogueGain": 1.0, "ColourGains": [1.0, 1.0],
            "ColourCorrectionMatrix": [1, 0, 0, 0, 1, 0, 0, 0, 1]}     # params only, unscored
    rows, worst = [], 0.0
    for ill in ("cool", "warm"):
        roi = roi_all[f"imx708_{ill}"]
        air = [rs.load(data, "imx708", ill, -1, i) for i in range(3)]
        _air_ctx, air_noise = rs.context_for(air, roi, 2.0)
        for cond in ("air", "uw"):
            fsid = f"imx708_{ill}_s-1_{cond}_field"
            ref = study[fsid]
            reps = air if cond == "air" else [
                sim.thin(r, air_noise, seed=1000 + 100 * (-1 + 2) + i, dead_zone=0.0)
                for i, r in enumerate(air)]
            # the study's own field crop (run_study.field_rows)
            cx = np.mean([np.mean(p["quad"], axis=0) for p in roi["patches"].values()], axis=0) * 2
            w, h = rs.FIELD_CROP
            x0 = int(np.clip(cx[0] - w / 2, 0, reps[0].shape[1] - w)) // 2 * 2
            y0 = int(np.clip(cx[1] - h / 2, 0, reps[0].shape[0] - h)) // 2 * 2
            crop = [replace(r, mosaic=np.ascontiguousarray(r.mosaic[y0:y0 + h, x0:x0 + w]))
                    for r in reps]
            croi = rs.shift_rois(roi, x0 // 2, y0 // 2)
            ctx = metrics.Context(crop[0], crop, croi, noise.estimate(crop, croi, 2.0),
                                  rs.grey_wb(crop[0], croi))
            c0 = {"mosaic": crop[0].mosaic, "cfa": crop[0].cfa, "black": crop[0].black,
                  "white": crop[0].white, "native_w": 4608, "native_h": 2592}
            codes = X.code_planes(c0)
            colour = X.colour_params(meta)
            target = int(ref["bytes"])
            cache = {}

            def blob_at(d):
                if d not in cache:
                    with tempfile.TemporaryDirectory(prefix="s28e2e_") as work:
                        payloads, _ = X.encode_rung(codes, d, 5, work, cjxl=cjxl,
                                                    runner=X.run_capped, timeout_s=600)
                    params = X.build_params(crop_xywh=[x0, y0, w, h], native_wh=(4608, 2592),
                                            crc=0, colour=colour, distance=d, effort=5)
                    cache[d] = X.seal_container(w=w, h=h, cfa=c0["cfa"], black=c0["black"],
                                                white=c0["white"], params=params,
                                                payloads=payloads)[0]
                return cache[d]
            lo, hi = 0.5, 12.0
            for _ in range(14):                      # bytes fall as d rises
                mid = round((lo + hi) / 2, 4)
                if len(blob_at(mid)) > target:
                    lo = mid
                else:
                    hi = mid
            d = min((lo, hi), key=lambda v: abs(len(blob_at(v)) - target))
            blob = blob_at(d)
            rec = rp.decode(blob)
            m = metrics.evaluate(ctx, rec)
            diff = float(m["stress_de_mean"]) - float(ref["stress_de_mean"])
            worst = max(worst, abs(diff))
            row = {"fsid": fsid, "crop_xywh": f"{x0},{y0},{w},{h}", "study_bytes": target,
                   "prod_bytes": len(blob), "prod_distance_e5": d,
                   "study_knob_e7": ref["knob"], "study_stress_de": round(float(ref["stress_de_mean"]), 4),
                   "prod_stress_de": round(float(m["stress_de_mean"]), 4),
                   "diff_stress_de": round(diff, 4),
                   "study_block_de_med": round(float(ref["block_de_med"]), 4),
                   "prod_block_de_med": round(float(m["block_de_med"]), 4),
                   "verdict": "PASS" if abs(diff) <= TOL else "FAIL"}
            rows.append(row)
            say(f"[E2E] {fsid}: {len(blob)} B (study {target}) d={d} stress dE "
                f"{row['prod_stress_de']} vs study {row['study_stress_de']} "
                f"(diff {row['diff_stress_de']:+}) {row['verdict']}")
    with open(os.path.join(args.out, "e2e.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    verdict = "PASS" if worst <= TOL else "FAIL"
    with open(os.path.join(args.out, "run_manifest.json"), "w") as fh:
        json.dump({"run": os.path.basename(os.path.abspath(args.out)), "sprint": "Sprint28 S1",
                   "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "host": platform.node(), "cjxl": ver, "tolerance_stress_de": TOL,
                   "worst_abs_diff": round(worst, 4), "verdict": verdict,
                   "rig": "compression_study + src from rig origin/main (read-only copies)",
                   "outputs": ["e2e.csv", "e2e.log"]}, fh, indent=1)
    say(f"[E2E] {verdict}: worst |diff| {worst:.4f} (tolerance {TOL})")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
