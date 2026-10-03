#!/usr/bin/env python3
# filename: s28_calibrate_distances.py
# description: Sprint28 S0 — distance -> bytes curves for the nrjxl encoder on the study DNGs; picks the default still.raw.distances rungs.
"""
S0 desk calibration (SPEC r4 §3.5, §8 S0): run the PRODUCTION encoder path
(BM_Devel_Pi/rc_raw_jxl.py: DNG crop reader, sqrt LUT, cjxl -m 1 -e 5 --num_threads=0,
NR container v1) over a grid of distances on the study's IMX708 DNGs, for each raw
crop preset, and pick the default distance rungs.

Inputs:  --data  rig data/s4_20260930 (the study's sha256-checked copy; read-only)
         the DNG + rpicam metadata JSON pairs `<ill>_imx708/stop_<s>_r<r>.{dng,json}`
Outputs: --out run folder: run_manifest.json, curves.csv (one row per frame x crop x
         distance), rungs.json (the picked rungs + the rule), calibration.log
Rule (written before looking at the numbers): for each byte target, the rung is the
         MEDIAN over frames of the smallest grid distance whose blob is <= the target
         (linear interpolation between grid points, rounded UP to 0.05 so the median
         frame lands at or under the target). Targets at 384 b64 chars / chunk (the
         deployed units, SPEC §0.2):
           56_100 B = 195 msgs (still.message_cap default)        rung 1
           50_000 B = 174 msgs (heal-wake limit, SPEC §3.8)       rung 2
           43_000 B = 150 msgs (2400x1350 heal wake, SPEC §5.2)   rung 3
           34_000 B = 118 msgs (complex scenes: ~0.6 x rung 1)     rung 4
Assumptions: the study frames are an indoor card scene in air. Real reef frames are
         likely more complex (more bytes at the same distance), so rung 4 is wide on
         purpose; a scene that does not fit rung 4 sends pjpg with rfb=fit (R4/O1 count).
         Bytes depend on the libjxl version (Mac v0.11.1 here; the unit's is recorded
         by R0.4).
Example:
  .venv-dev/bin/python tools/s28_calibrate_distances.py \\
      --data ~/Documents/GitHub/nereus-camera-test-rig/data/s4_20260930 \\
      --out runs/s28_s0_calibration_20261002
Known limitations: Mac timings are NOT Pi timings (R0.3 measures those).
"""

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))

import rc_raw_jxl as R  # noqa: E402

# SPEC r4 §3.3 presets (native px). 1600x900 is the default; the others are opt-in.
PRESETS = {
    "1600x900": [1504, 846, 1600, 900],
    "2000x1124": [1304, 734, 2000, 1124],
    "2400x1350": [1104, 620, 2400, 1350],
}
TARGETS = [("rung1_cap195", 56_100), ("rung2_heal174", 50_000), ("rung3_150", 43_000),
           ("rung4_complex", 34_000)]
GRID = [round(1.5 + 0.25 * i, 2) for i in range(31)]          # 1.5 .. 9.0
FRAMES = [(ill, stop, 0) for ill in ("cool", "warm") for stop in (-1, 0, 1)]


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _distance_for(curve, target):
    """Smallest distance with bytes <= target, interpolated between grid points."""
    prev = None
    for d, n in curve:
        if n <= target:
            if prev is None:
                return d
            d0, n0 = prev
            return d0 + (d - d0) * (n0 - target) / float(n0 - n)
        prev = (d, n)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--effort", type=int, default=5)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    cjxl = shutil.which("cjxl")
    if not cjxl:
        raise SystemExit("cjxl not on PATH")
    ver = subprocess.run([cjxl, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    log = open(os.path.join(args.out, "calibration.log"), "w")

    def say(msg):
        print(msg, flush=True)
        log.write(msg + "\n")

    say(f"[S0] host={platform.node()} cjxl={ver} effort={args.effort} data={args.data}")
    inputs, rows = {}, []
    for ill, stop, rep in FRAMES:
        stem = os.path.join(args.data, f"{ill}_imx708", f"stop_{stop:+d}_r{rep}")
        dng, meta_path = stem + ".dng", stem + ".json"
        inputs[os.path.relpath(dng, args.data)] = _sha(dng)
        with open(meta_path) as fh:
            meta = json.load(fh)
        colour = R.colour_params(meta)
        for preset, crop_xywh in PRESETS.items():
            crop = R.read_dng_crop(dng, crop_xywh)
            codes = R.code_planes(crop)
            with tempfile.TemporaryDirectory(prefix="s28cal_") as work:
                for d in GRID:
                    payloads, runs = R.encode_rung(codes, d, args.effort, work, cjxl=cjxl,
                                                   runner=R.run_capped, timeout_s=600)
                    params = R.build_params(crop_xywh=crop_xywh,
                                            native_wh=(crop["native_w"], crop["native_h"]),
                                            crc=0, colour=colour, distance=d,
                                            effort=args.effort)
                    blob, _crc = R.seal_container(w=crop_xywh[2], h=crop_xywh[3],
                                                  cfa=crop["cfa"], black=crop["black"],
                                                  white=crop["white"], params=params,
                                                  payloads=payloads)
                    rows.append({"frame": f"{ill}_s{stop:+d}_r{rep}", "preset": preset,
                                 "distance": d, "bytes": len(blob),
                                 "msgs_384": R.message_count(len(blob), 384),
                                 "msgs_300": R.message_count(len(blob), 300),
                                 "bpp": round(len(blob) * 8 / (crop_xywh[2] * crop_xywh[3]), 4),
                                 "mac_cjxl_s": round(sum(r["seconds"] for r in runs), 3),
                                 "mac_peak_rss_kb": max(r["peak_rss_kb"] for r in runs)})
            say(f"[S0] {ill} stop {stop:+d} {preset}: d=1.5 -> {rows[-len(GRID)]['bytes']} B, "
                f"d=9.0 -> {rows[-1]['bytes']} B")
    with open(os.path.join(args.out, "curves.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)

    picked = {}
    for preset in PRESETS:
        per = {}
        for name, target in TARGETS:
            ds = []
            for ill, stop, rep in FRAMES:
                frame = f"{ill}_s{stop:+d}_r{rep}"
                curve = [(r["distance"], r["bytes"]) for r in rows
                         if r["frame"] == frame and r["preset"] == preset]
                ds.append(_distance_for(curve, target))
            ok = sorted(d for d in ds if d is not None)
            med = ok[len(ok) // 2] if ok else None
            per[name] = {"target_bytes": target, "per_frame": [None if d is None else round(d, 3)
                                                                for d in ds],
                         "median": None if med is None else round(med, 3),
                         "rung": None if med is None else math.ceil(med * 20 - 1e-9) / 20.0}
        picked[preset] = per
        say(f"[S0] {preset} rungs: " + ", ".join(f"{n}={v['rung']}" for n, v in per.items()))
    with open(os.path.join(args.out, "rungs.json"), "w") as fh:
        json.dump({"rule": "median over frames of the interpolated distance at each target, "
                           "rounded up to 0.05", "targets": TARGETS, "grid": GRID,
                   "frames": [f"{i}_s{s:+d}_r{r}" for i, s, r in FRAMES], "picked": picked},
                  fh, indent=1)
    manifest = {"run": os.path.basename(os.path.abspath(args.out)), "sprint": "Sprint28 S0",
                "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "host": platform.node(), "cjxl": ver, "effort": args.effort,
                "command": " ".join([os.path.relpath(sys.argv[0], REPO)] + (argv or sys.argv[1:])),
                "encoder": "BM_Devel_Pi/rc_raw_jxl.py", "inputs_sha256": inputs,
                "outputs": ["curves.csv", "rungs.json", "calibration.log"]}
    with open(os.path.join(args.out, "run_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    say(f"[S0] wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
