#!/usr/bin/env python3
# filename: s28_b3a_effort_check.py
# description: Sprint28 B3a cjxl effort 4 vs 5 on one TG-7 frame at 1600x900: (a) EQUAL BYTES (each effort bisected to the same 0.97-fill target of 195 msgs) and (b) the PRODUCTION byte search per effort; SSIMULACRA2 / butteraugli vs the lossless reference, encode time.
"""
The B3a input is built with the PRODUCTION functions (rc_raw_jxl.coding_gains /
headroom_x10000 / rgb_codes / write_ppm) from the TG-7 frame mapped to IMX708 geometry by the
rig's tg7_budget.map_to_imx ("TG-7 resampled to IMX708 geometry, an approximation"). The decode
is the DESIGN_B3a.md §2 formula; the render and the reference are s28_density_sweep.py's TG-7
render (cv2 bilinear of the same mosaic, as-shot WB, Olympus CCM, exposure scaled so the
reference's G p99.5 = 0.9).
  (a) equal bytes: for each effort, the smallest d whose bytes <= target (bisection on d, 14
      steps); bytes are reported (they differ by < ~1 % at the end).
  (b) production: rc_raw_jxl.choose_rate (fill 0.97, <= 3 encodes, the VarDCT prior) with
      run_capped (the guard), per effort: d, bytes, msgs, attempts, encode seconds.
Times are single-thread wall seconds on THIS host (relative only; Pi times = bench B0).
Outputs: --out/<label>/effort.json. Example:
  PYTHONPATH=$RIG/src:$RIG <rig venv python> tools/s28_b3a_effort_check.py --rig $RIG \\
      --orf X.orf --label tg7_P9160564 --out runs/s28_b3a_effort_20261005
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP, CHUNK, FILL = 195, 384, 0.97


class _Budget:
    seconds_per_message = 1.3

    @staticmethod
    def remaining_s():
        return 100000.0

    @staticmethod
    def messages_fit(n):
        return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rig", required=True)
    ap.add_argument("--orf", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--efforts", default="4,5")
    args = ap.parse_args(argv)
    sys.path[:0] = [os.path.join(args.rig, "src"), args.rig, os.path.join(REPO, "BM_Devel_Pi")]
    import cv2
    import numpy as np
    from PIL import Image
    from compression_study.preview_loss import tg7_budget as TG
    from host_tools.tg7.orf_io import read_orf
    import rc_raw_jxl as X

    f = read_orf(args.orf)
    scene, _, _ = TG.map_to_imx(f, (1600, 900))
    mos, cfa, black, white = scene["mosaic"], scene["cfa"], scene["black"], scene["white"]
    g3 = np.asarray(scene["gains"], np.float64)
    ccm = np.asarray(scene["ccm"], np.float64)
    meta = {"ExposureTime": float(f.exposure_s) * 1e6, "AnalogueGain": float(f.iso) / 100,
            "ColourGains": [float(g3[0] / g3[1]), float(g3[2] / g3[1])],
            "ColourCorrectionMatrix": [float(v) for v in ccm.ravel()], "DigitalGain": 1.0}
    colour = X.colour_params(meta)
    g = X.coding_gains(colour)
    hx = X.headroom_x10000(mos, cfa, black, white, g)
    work = tempfile.mkdtemp(prefix="s28eff_")
    X.write_ppm(os.path.join(work, X.RGB_PPM), X.rgb_codes(mos, cfa, black, white, g, hx))
    target = int(FILL * 3 * ((CAP * CHUNK) // 4))

    code = {"BGGR": cv2.COLOR_BayerRG2RGB, "RGGB": cv2.COLOR_BayerBG2RGB,
            "GRBG": cv2.COLOR_BayerGB2RGB, "GBRG": cv2.COLOR_BayerGR2RGB}[cfa]
    norm = np.clip((mos.astype(np.float32) - black) / (white - black), 0, 1)
    ref_lin = cv2.cvtColor((norm * 65535 + 0.5).astype(np.uint16), code).astype(np.float64) / 65535
    expo = [None]

    def render(lin):
        rgb = np.clip(np.minimum(lin * g3, 1.0) @ ccm.T, 0, None)
        if expo[0] is None:
            expo[0] = 0.9 / max(float(np.percentile(rgb[..., 1], 99.5)), 1e-6)
        rgb = np.clip(rgb * expo[0], 0, 1)
        s = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
        return (np.clip(s, 0, 1) * 255 + 0.5).astype(np.uint8)
    ref = render(ref_lin)
    ref_p = os.path.join(work, "ref.png")
    Image.fromarray(ref).save(ref_p)

    def encode(d, e):
        dst = os.path.join(work, f"e{e}.jxl")
        t0 = time.monotonic()
        subprocess.run(X.cjxl_rgb_command(shutil.which("cjxl"), os.path.join(work, X.RGB_PPM),
                                          dst, d, e) + ["--quiet"], check=True,
                       capture_output=True)
        dt = time.monotonic() - t0
        with open(dst, "rb") as fh:
            return fh.read(), dt

    def score(payload):
        jxl, png = os.path.join(work, "s.jxl"), os.path.join(work, "s.png")
        with open(jxl, "wb") as fh:
            fh.write(payload)
        subprocess.run(["djxl", jxl, png, "--bits_per_sample=16", "--quiet"], check=True)
        dec = cv2.cvtColor(cv2.imread(png, cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB)
        codes = np.rint(dec.astype(np.float64) * 4095 / 65535)
        cand = os.path.join(work, "c.png")
        Image.fromarray(render(X.rgb_linear_from_codes(codes, g, hx))).save(cand)
        s2 = float(subprocess.run(["ssimulacra2", ref_p, cand], capture_output=True,
                                  text=True).stdout.split()[-1])
        ba = subprocess.run(["butteraugli_main", ref_p, cand], capture_output=True, text=True)
        return round(s2, 3), round(float(re.search(r"3-norm:\s*([0-9.]+)", ba.stdout).group(1)), 3)

    out = {"label": args.label, "orf": args.orf, "target_bytes": target, "headroom_x10000": hx,
           "note": "TG-7 resampled to IMX708 geometry, an approximation; times = this host"}
    for e in [int(x) for x in args.efforts.split(",")]:
        lo, hi = 0.1, 15.0
        best = None
        times = []
        for _ in range(14):
            mid = round((lo + hi) / 2, 4)
            b, dt = encode(mid, e)
            times.append(dt)
            if len(b) <= target:
                hi, best = mid, (mid, b)
            else:
                lo = mid
        d, b = best
        s2, b3 = score(b)
        rec = {"equal_bytes": {"distance": d, "bytes": len(b), "ssimulacra2": s2,
                               "butteraugli_3norm": b3,
                               "encode_s_median": round(sorted(times)[len(times) // 2], 3)}}
        cfg = dict(X.DEFAULT_CONFIG, format="nrjxl", layout="rgb", effort=e, encode_max_s=600,
                   target_fill=FILL, d_max=15.0)
        crop = {"cfa": cfa, "black": black, "white": white, "native_w": 4608, "native_h": 2592,
                "w": 1600, "h": 900, "headroom_x10000": hx}
        t0 = time.monotonic()
        r = X.choose_rate(crop, None, colour, cfg, crop_xywh=[1504, 846, 1600, 900],
                          budget=_Budget, message_cap=CAP, chunk_b64_chars=CHUNK, reserve_msgs=0,
                          fallback_msgs=CAP, work_dir=work, cjxl=shutil.which("cjxl"),
                          runner=X.run_capped, log=lambda *_: None)
        search_s = time.monotonic() - t0
        ps2, pb3 = score(X.unpack_container(r["blob"])[1][0])
        rec["production"] = {"distance": r["distance"], "bytes": len(r["blob"]),
                             "msgs": r["message_count"], "attempts": r["attempts"],
                             "search_s": round(search_s, 3), "ssimulacra2": ps2,
                             "butteraugli_3norm": pb3}
        out[f"e{e}"] = rec
        print(f"[EFF] {args.label} e{e}: equal-bytes d={d} {len(b)} B s2 {s2} b3 {b3} "
              f"{rec['equal_bytes']['encode_s_median']} s | production d={r['distance']} "
              f"{len(r['blob'])} B {r['message_count']} msgs att {r['attempts']} s2 {ps2} b3 {pb3} "
              f"{search_s:.2f} s", flush=True)
    os.makedirs(os.path.join(args.out, args.label), exist_ok=True)
    with open(os.path.join(args.out, args.label, "effort.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
