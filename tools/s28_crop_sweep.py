#!/usr/bin/env python3
# filename: s28_crop_sweep.py
# description: Sprint28 crop-size sweep (Nick, via the EM, 2026-10-05): how LARGE a crop at NATIVE density can the RGB JPEG XL route carry inside 195 msgs before its central 1600x900 drops below today's JPEG of that region?
"""
Crops, all centred 16:9 at NATIVE IMX708 density: 1600x900 (anchor), 1920x1080, 2304x1296,
2880x1620, 3200x1800, 4608x2592 (full). Source: TG-7 RAWs mapped by the rig's
tg7_budget.map_to_imx (crop = the IMX708 crop's FOV fraction of the TG-7 width, each CFA plane
lanczos-resampled to IMX708 px, 10-bit; "TG-7 resampled to IMX708 geometry, an
approximation"). One exposure scale per frame (from the central 1600x900 reference, as in
s28_density_sweep.py), so every crop renders alike.

Rows: the candidate RGB route (--rgb-row rgbw = WB applied before coding, clipped at 1; or
rgbwa = WB as a coding transform, scaled by the frame's WB peak, undone at decode), cjxl
VarDCT -m 0 -e 5; and the production 4-plane Bayer (cjxl -m 1 -e 5, d <= 15) for reference.
Byte target = production's 0.97 fill of 195 msgs (54475 B) for every crop.

Scores (SSIMULACRA2; butteraugli 3-norm too for the 1600x900 regions):
  central: the central 1600x900 of the decoded crop vs the same region of the crop's own
           lossless neutral render; against today's JPEG OF THAT REGION (1000x562 ladder,
           upsampled). PASS >= JPEG; SIG = +5 / x0.9 (Nick's bar).
  whole:   the whole decoded crop vs its lossless render (SSIMULACRA2 only), next to today's
           JPEG of the SAME FOV (the whole crop -> 1000 px wide on the ladder, upsampled back):
           is the outer area usable too.
Outputs (--out/<label>/): scores.csv, summary.json, sameFOV slider files (--slider): the
crop's decode and the same-FOV JPEG at the crop size, JPEG q92.
Example:
  PYTHONPATH=$RIG/src:$RIG ~/Documents/GitHub/nereus-camera-test-rig/.venv/bin/python \\
      tools/s28_crop_sweep.py --rig $RIG --orf X.orf --label tg7_P9160493 --out runs/s28_crop_sweep_20261005
Known limitations: TG-7 approximation (its own noise, its own optics); the 4608x2592 TG-7
crop is the whole TG-7 width upsampled x1.148 (no real extra detail beyond the TG-7's);
Pi feasibility is NOT measured here (tools/s28_crop_cost.py measures cjxl RSS / time on the
Mac; the Pi numbers are ESTIMATES).
"""

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROPS = [(1600, 900), (1920, 1080), (2304, 1296), (2880, 1620), (3200, 1800), (4608, 2592)]
CAP, CHUNK, FILL = 195, 384, 0.97
LADDER = [90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 13, 11, 9]
SIG_S2, SIG_B3 = 5.0, 0.9


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rig", required=True)
    ap.add_argument("--orf", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rgb-row", default="rgbw", choices=("rgbw", "rgbwa"))
    ap.add_argument("--crops", default=",".join(f"{w}x{h}" for w, h in CROPS))
    ap.add_argument("--no-bayer", action="store_true")
    ap.add_argument("--slider", action="store_true", help="write the same-FOV slider files")
    args = ap.parse_args(argv)
    sys.path[:0] = [os.path.join(args.rig, "src"), args.rig, os.path.join(REPO, "BM_Devel_Pi")]
    import cv2
    import numpy as np
    from PIL import Image
    from compression_study.methods import raw_planes as rp
    from compression_study.preview_loss import tg7_budget as TG
    from host_tools.tg7.orf_io import read_orf

    import rc_jpeg_encoder as J
    import rc_raw_jxl as X

    out = os.path.join(args.out, args.label)
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="s28cs_")
    cjxl = shutil.which("cjxl")
    target = int(FILL * 3 * ((CAP * CHUNK) // 4))
    f = read_orf(args.orf)
    crops = [tuple(int(v) for v in c.split("x")) for c in args.crops.split(",")]

    expo = [None]

    def setup(rw, rh):
        scene, tbox, s_map = TG.map_to_imx(f, (rw, rh))
        return scene, list(tbox), s_map

    def demosaic(m, scene):
        code = getattr(cv2, {"BGGR": "COLOR_BayerRG2RGB", "RGGB": "COLOR_BayerBG2RGB",
                             "GRBG": "COLOR_BayerGB2RGB", "GBRG": "COLOR_BayerGR2RGB"}[scene["cfa"]])
        x = np.clip((np.asarray(m, np.float32) - scene["black"]) / (scene["white"] - scene["black"]), 0, 1)
        return cv2.cvtColor((x * 65535 + 0.5).astype(np.uint16), code).astype(np.float32) / 65535

    def finish(lin, scene, wb_applied=False):
        g3 = np.asarray(scene["gains"], np.float32)
        ccm = np.asarray(scene["ccm"], np.float32)
        rgb = lin if wb_applied else np.minimum(lin * g3, 1.0)
        rgb = np.clip(rgb @ ccm.T, 0, None)
        if expo[0] is None:
            expo[0] = 0.9 / max(float(np.percentile(rgb[..., 1], 99.5)), 1e-6)
        rgb = np.clip(rgb * expo[0], 0, 1)
        s = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
        return (np.clip(s, 0, 1) * 255 + 0.5).astype(np.uint8)

    def metric(ref, img, b3=True):
        a, b = os.path.join(tmp, "r.png"), os.path.join(tmp, "c.png")
        Image.fromarray(ref).save(a)
        Image.fromarray(img).save(b)
        s2 = float(subprocess.run(["ssimulacra2", a, b], capture_output=True,
                                  text=True).stdout.split()[-1])
        out_ = {"ssimulacra2": round(s2, 3)}
        if b3:
            ba = subprocess.run(["butteraugli_main", a, b], capture_output=True, text=True).stdout
            out_["butteraugli_3norm"] = round(float(re.search(r"3-norm:\s*([0-9.]+)", ba).group(1)), 3)
        return out_

    def pjpg(img8, out_w):
        h = int(round(out_w * img8.shape[0] / img8.shape[1]))
        src = Image.fromarray(img8).resize((out_w, h), Image.Resampling.LANCZOS)
        for q in LADDER:
            enc = J.encode_progressive(src, q, CHUNK)
            if enc["message_count"] <= CAP:
                break
        dec = Image.open(io.BytesIO(enc["jpeg_data"])).convert("RGB")
        up = np.asarray(dec.resize((img8.shape[1], img8.shape[0]), Image.Resampling.LANCZOS))
        return up, enc

    def fit(make, hi):
        b = make(hi)
        if len(b) > target:
            return hi, b, False
        lo, best = 0.1, (hi, b)
        for _ in range(11):
            mid = round((lo + hi) / 2, 3)
            bb = make(mid)
            if len(bb) <= target:
                hi, best = mid, (mid, bb)
            else:
                lo = mid
            if hi - lo < 0.02:
                break
        return best[0], best[1], True

    def rgb_enc(codes, d):
        h, w, _ = codes.shape
        ppm, jxl = os.path.join(tmp, "x.ppm"), os.path.join(tmp, "x.jxl")
        with open(ppm, "wb") as fh:
            fh.write(f"P6\n{w} {h}\n4095\n".encode("ascii"))
            fh.write(np.ascontiguousarray(codes, dtype=">u2").tobytes())
        subprocess.run([cjxl, ppm, jxl, "-m", "0", "-e", "5", "-d", f"{d:.4f}", "--num_threads=0",
                        "--quiet"], check=True, capture_output=True)
        with open(jxl, "rb") as fh:
            return fh.read()

    def rgb_dec(blob):
        jxl, png = os.path.join(tmp, "y.jxl"), os.path.join(tmp, "y.png")
        with open(jxl, "wb") as fh:
            fh.write(blob)
        subprocess.run(["djxl", jxl, png, "--bits_per_sample=16", "--quiet"], check=True,
                       capture_output=True)
        return cv2.cvtColor(cv2.imread(png, cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB)

    rows, slider = [], []
    # the anchor first: its central reference sets the frame's exposure scale
    for rw, rh in sorted(crops):
        scene, tbox, s_map = setup(rw, rh)
        black, white = scene["black"], scene["white"]
        lin = demosaic(scene["mosaic"], scene)
        if expo[0] is None:
            cx0, cy0 = (rw - 1600) // 2 // 2 * 2, (rh - 900) // 2 // 2 * 2
            finish(lin[cy0:cy0 + 900, cx0:cx0 + 1600], scene)          # sets expo
        ref = finish(lin, scene)
        cx, cy = (rw - 1600) // 2 // 2 * 2, (rh - 900) // 2 // 2 * 2
        ref_c = ref[cy:cy + 900, cx:cx + 1600]
        jc, jenc = pjpg(ref_c, 1000)
        jrow = {"crop": f"{rw}x{rh}", "row": "jpeg_central", "quality": jenc["quality"],
                "bytes": jenc["jpeg_bytes"], "msgs": jenc["message_count"], **metric(ref_c, jc)}
        jf, jfenc = pjpg(ref, 1000)
        jfs = metric(ref, jf, b3=False)["ssimulacra2"] if (rw, rh) != (1600, 900) else jrow["ssimulacra2"]
        jrow["whole_jpeg_sameFOV_q"] = jfenc["quality"]
        jrow["whole_jpeg_sameFOV_ssimulacra2"] = jfs
        rows.append(jrow)
        print(f"[CS] {args.label} {rw}x{rh}: JPEG central q{jenc['quality']} s2 {jrow['ssimulacra2']} "
              f"b3 {jrow['butteraugli_3norm']} | same-FOV JPEG q{jfenc['quality']} whole s2 {jfs}",
              flush=True)
        g3 = np.asarray(scene["gains"], np.float32)
        lut = X.sqrt_lut(black, white)
        scale = 4095 / np.sqrt(white - black)
        todo = [args.rgb_row] + ([] if args.no_bayer else ["bayer"])
        for row in todo:
            if row == "bayer":
                crop = {"mosaic": scene["mosaic"], "cfa": scene["cfa"], "black": black,
                        "white": white}

                def mk(d):
                    with tempfile.TemporaryDirectory(prefix="s28csb_") as work:
                        pl, _ = X.encode_rung(X.code_planes(crop), min(d, 15.0), 5, work,
                                              cjxl=cjxl, runner=X.run_capped, timeout_s=1200)
                    params = X.build_params(crop_xywh=[0, 0, rw, rh], native_wh=(4608, 2592),
                                            crc=0, colour={"exposure_us": 0, "again_x1000": 1000,
                                                           "gains_x10000": [10000, 10000],
                                                           "ccm_x10000": [0] * 9,
                                                           "temp_x10": X.SENTINEL,
                                                           "dgain_x1000": X.SENTINEL,
                                                           "ct_k": X.SENTINEL},
                                            distance=min(d, 15.0), effort=5)
                    return X.seal_container(w=rw, h=rh, cfa=scene["cfa"], black=black,
                                            white=white, params=params, payloads=pl)[0]
                d, blob, ok = fit(mk, 15.0)
                img = finish(demosaic(rp.decode(blob), scene), scene)
            else:
                if row == "rgbw":
                    coded, sc_ = np.minimum(lin * g3, 1.0), None
                else:
                    sc_ = max(1.0, float((lin * g3).max()))
                    coded = lin * (g3 / sc_)
                dn = np.clip(np.rint(coded * (white - black) + black), 0, white).astype(np.int32)
                codes = lut[dn]
                d, blob, ok = fit(lambda dd: rgb_enc(codes, dd), 25.0)
                v = (rgb_dec(blob).astype(np.float64) / 65535 * 4095 / scale) ** 2
                dec = np.clip(v / (white - black), 0, 1).astype(np.float32)
                img = finish(dec, scene, wb_applied=True) if row == "rgbw" else \
                    finish(dec * (sc_ / g3), scene)
            c = metric(ref_c, img[cy:cy + 900, cx:cx + 1600])
            whole = metric(ref, img, b3=False)["ssimulacra2"] if (rw, rh) != (1600, 900) \
                else c["ssimulacra2"]
            r = {"crop": f"{rw}x{rh}", "row": row, "distance": d, "fits": ok, "bytes": len(blob),
                 "msgs": X.message_count(len(blob), CHUNK), **c, "whole_ssimulacra2": whole,
                 "whole_jpeg_sameFOV_ssimulacra2": jfs,
                 "d_s2": round(c["ssimulacra2"] - jrow["ssimulacra2"], 3),
                 "b3_ratio": round(c["butteraugli_3norm"] / jrow["butteraugli_3norm"], 3),
                 "tg7_box_xywh": tbox, "scale_to_imx": round(s_map, 4)}
            r["pass"] = ok and r["d_s2"] >= 0 and r["b3_ratio"] <= 1
            r["sig"] = ok and r["d_s2"] >= SIG_S2 and r["b3_ratio"] <= SIG_B3
            rows.append(r)
            print(f"[CS] {args.label} {rw}x{rh} {row}: d={d}{'' if ok else ' (does NOT fit)'} "
                  f"{len(blob)} B central s2 {c['ssimulacra2']} ({r['d_s2']:+}) b3 x{r['b3_ratio']} "
                  f"{'SIG' if r['sig'] else 'PASS' if r['pass'] else 'FAIL'} | whole s2 {whole} vs "
                  f"same-FOV JPEG {jfs}", flush=True)
            if args.slider and row == args.rgb_row:
                sd = os.path.join(out, "slider_sameFOV")
                os.makedirs(sd, exist_ok=True)
                for name, im in ((f"jpeg_sameFOV_{rw}x{rh}.jpg", jf),
                                 (f"nrjxl_{row}_{rw}x{rh}.jpg", img)):
                    Image.fromarray(im).save(os.path.join(sd, name), quality=92)
                    slider.append({"file": f"{args.label}/slider_sameFOV/{name}", "crop": f"{rw}x{rh}",
                                   "what": "today's JPEG of the same FOV (1000 px wide, upsampled)"
                                   if name.startswith("jpeg") else f"nrjxl {row} at native density",
                                   **({k: r[k] for k in ("distance", "bytes", "whole_ssimulacra2")}
                                      if not name.startswith("jpeg") else {"ssimulacra2": jfs})})
    keys = ["crop", "row", "distance", "fits", "quality", "bytes", "msgs", "ssimulacra2",
            "butteraugli_3norm", "d_s2", "b3_ratio", "pass", "sig", "whole_ssimulacra2",
            "whole_jpeg_sameFOV_ssimulacra2", "whole_jpeg_sameFOV_q", "tg7_box_xywh", "scale_to_imx"]
    with open(os.path.join(out, "scores.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump({"label": args.label, "orf": args.orf, "rgb_row": args.rgb_row, "rows": rows,
                   "slider": slider, "exposure_scale": expo[0], "target_bytes": target,
                   "label_note": "TG-7 resampled to IMX708 geometry, an approximation",
                   "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, fh, indent=1,
                  default=str)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
