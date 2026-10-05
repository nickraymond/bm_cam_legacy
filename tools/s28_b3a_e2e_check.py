#!/usr/bin/env python3
# filename: s28_b3a_e2e_check.py
# description: Sprint28 B3a desk e2e check: the PRODUCTION encode_still (still.raw.layout rgb, real cjxl, the byte search at 195 msgs) on a real DNG, then an INDEPENDENT decode (djxl + numpy, not rc_raw_jxl) to camera linear, against an OpenCV bilinear of the same mosaic; plus cjxl's peak RSS on this host.
"""
Checks (exit 1 on any FAIL):
  1. the blob unpacks (crc), method 20 / flags 0x06 / profile 2 / 1 payload, <= 195 msgs;
  2. the independent decode (DESIGN_B3a.md §2 formula, implemented here) gives camera-native
     linear RGB: vs OpenCV's bilinear demosaic of the SAME normalised mosaic (the reference
     the sweep scored), mean |error| reported; no channel clipped at the headroom
     (max(lin x g) <= s);
  3. a neutral render of both (WB x CCM x sRGB, the v1 render) scored with SSIMULACRA2 /
     butteraugli (informational; the sweep's numbers are the comparison), and the decoded
     PNG saved for eyes;
  4. cjxl peak RSS at the chosen d on THIS host (/usr/bin/time -l on macOS; GNU time -v on
     Linux), next to the 250 MB guard (ulimit -v caps VIRTUAL memory: RSS is a lower bound,
     the Pi bench B0 measures VmPeak).
Inputs: --dng, and --metadata (rpicam JSON) or --params-from (an .nrjxl whose header carries
        the same exposure's gains / CCM), --crop, --rig (for cv2 via the rig venv).
Outputs (--out): e2e.json, decoded.png, reference.png, the blob.
Example:
  ~/Documents/GitHub/nereus-camera-test-rig/.venv/bin/python tools/s28_b3a_e2e_check.py \\
      --dng X__unpacked.dng --params-from X.nrjxl --crop 0,0,1600,900 --out runs/s28_b3a_e2e_20261005/57521
Known limitations: a DNG unpacked from an earlier nrjxl is already lossy (fine for the
pipeline, not a quality reference); macOS gives RSS, not VmPeak.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))


class _Budget:
    seconds_per_message = 1.3

    @staticmethod
    def remaining_s():
        return 100000.0

    @staticmethod
    def messages_fit(n):
        return True


def meta_from_nrjxl(path):
    import rc_raw_jxl as X
    with open(path, "rb") as fh:
        head, _ = X.unpack_container(fh.read())
    p = head["params"]
    sent = lambda v, s: None if v == X.SENTINEL else v / s  # noqa: E731
    return {"ExposureTime": p[6], "AnalogueGain": p[7] / 1000,
            "ColourGains": [p[8] / 10000, p[9] / 10000],
            "ColourCorrectionMatrix": [v / 10000 for v in p[10:19]],
            "SensorTemperature": sent(p[19], 10), "DigitalGain": sent(p[22], 1000),
            "ColourTemperature": sent(p[23], 1)}


def peak_rss_mb(cmd):
    if sys.platform == "darwin":
        r = subprocess.run(["/usr/bin/time", "-l"] + cmd, capture_output=True, text=True)
        m = re.search(r"(\d+)\s+maximum resident set size", r.stderr)
        return round(int(m.group(1)) / 1e6, 1) if m else None
    r = subprocess.run(["/usr/bin/time", "-v"] + cmd, capture_output=True, text=True)
    m = re.search(r"Maximum resident set size \(kbytes\): (\d+)", r.stderr)
    return round(int(m.group(1)) / 1024, 1) if m else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dng", required=True)
    ap.add_argument("--metadata")
    ap.add_argument("--params-from")
    ap.add_argument("--crop", default="1504,846,1600,900")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    import numpy as np
    import cv2
    from PIL import Image
    import rc_raw_jxl as X

    os.makedirs(args.out, exist_ok=True)
    if args.metadata:
        with open(args.metadata) as fh:
            meta = json.load(fh)
    else:
        meta = meta_from_nrjxl(args.params_from)
    crop_xywh = [int(v) for v in args.crop.split(",")]
    cfg = dict(X.DEFAULT_CONFIG, format="nrjxl", layout="rgb", encode_max_s=600)
    work = tempfile.mkdtemp(prefix="b3a_e2e_")
    log = []
    res = X.encode_still(args.dng, meta, cfg, crop_xywh=crop_xywh, budget=_Budget(),
                         message_cap=195, chunk_b64_chars=384, work_dir=work,
                         log=lambda m: (log.append(m), print(m)))
    ppm_for_rss = os.path.join(work, X.RGB_PPM)
    rss = peak_rss_mb([shutil.which("cjxl"), ppm_for_rss, os.path.join(work, "rss.jxl"),
                       "-m", "0", "-e", "5", "-d", f"{res['distance']:.4f}", "--num_threads=0",
                       "--quiet"])
    with open(os.path.join(args.out, "blob.nrjxl"), "wb") as fh:
        fh.write(res["blob"])
    head, payloads = X.unpack_container(res["blob"])
    checks = {"method_20": head["method"] == 20, "flags_0x06": head["flags"] == 0x06,
              "profile_2": head["params"][0] == 2, "one_payload": len(payloads) == 1,
              "fits_195": res["message_count"] <= 195}

    # ---- the independent decode (DESIGN_B3a.md §2), NOT rc_raw_jxl ------------------
    p = head["params"]
    dg = 1.0 if p[22] == -32768 else p[22] / 1000
    g = np.array([dg * p[8] / 10000, dg, dg * p[9] / 10000])
    s = p[24] / 10000
    with open(os.path.join(work, "dec.jxl"), "wb") as fh:
        fh.write(payloads[0])
    subprocess.run(["djxl", os.path.join(work, "dec.jxl"), os.path.join(work, "dec.png"),
                    "--bits_per_sample=16", "--quiet"], check=True)
    dec = cv2.cvtColor(cv2.imread(os.path.join(work, "dec.png"), cv2.IMREAD_UNCHANGED),
                       cv2.COLOR_BGR2RGB).astype(np.float64)
    code = np.rint(dec * 4095 / 65535)                    # 16-bit PNG: scale to 12-bit codes
    lin = (code / 4095) ** 2 * s / g

    crop = X.read_dng_crop(args.dng, crop_xywh)
    norm = np.clip((crop["mosaic"].astype(np.float32) - crop["black"]) /
                   (crop["white"] - crop["black"]), 0, 1)
    bayer = {"BGGR": cv2.COLOR_BayerRG2RGB, "RGGB": cv2.COLOR_BayerBG2RGB,
             "GRBG": cv2.COLOR_BayerGB2RGB, "GBRG": cv2.COLOR_BayerGR2RGB}[crop["cfa"]]
    ref = cv2.cvtColor((norm * 65535 + 0.5).astype(np.uint16), bayer).astype(np.float64) / 65535
    err = float(np.abs(lin - ref).mean())
    checks["no_clip"] = bool(float((ref * g).max()) <= s + 1e-9)
    checks["decode_close_to_cv2_bilinear"] = err < 0.02

    ccm = np.array(meta["ColourCorrectionMatrix"]).reshape(3, 3)

    def render(x):
        r = np.clip((x * g) @ ccm.T, 0, 1)
        t = np.where(r <= 0.0031308, 12.92 * r, 1.055 * np.power(r, 1 / 2.4) - 0.055)
        return (np.clip(t, 0, 1) * 255 + 0.5).astype(np.uint8)
    Image.fromarray(render(ref)).save(os.path.join(args.out, "reference.png"))
    Image.fromarray(render(lin)).save(os.path.join(args.out, "decoded.png"))
    s2 = subprocess.run(["ssimulacra2", os.path.join(args.out, "reference.png"),
                         os.path.join(args.out, "decoded.png")], capture_output=True, text=True)
    ba = subprocess.run(["butteraugli_main", os.path.join(args.out, "reference.png"),
                         os.path.join(args.out, "decoded.png")], capture_output=True, text=True)
    b3 = re.search(r"3-norm:\s*([0-9.]+)", ba.stdout)
    out = {"dng": args.dng, "params_from": args.params_from, "metadata": args.metadata,
           "crop": crop_xywh, "distance": res["distance"], "bytes": len(res["blob"]),
           "msgs": res["message_count"], "attempts": res["attempts"],
           "attempt_log": res["attempt_log"], "timings": res["timings"],
           "headroom_x10000": p[24], "mean_abs_err_vs_cv2_bilinear": round(err, 5),
           "ssimulacra2_vs_reference": float(s2.stdout.split()[-1]),
           "butteraugli_3norm": float(b3.group(1)) if b3 else None,
           "cjxl_peak_rss_mb_this_host": rss, "host": sys.platform,
           "guard_note": "ulimit -v 250 MB caps VIRTUAL memory; RSS is a lower bound. VmPeak "
                         "under the guard = Pi bench B0.",
           "checks": checks, "pass": all(checks.values())}
    with open(os.path.join(args.out, "e2e.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps({k: out[k] for k in ("distance", "bytes", "msgs", "attempts",
                                          "headroom_x10000", "mean_abs_err_vs_cv2_bilinear",
                                          "ssimulacra2_vs_reference", "butteraugli_3norm",
                                          "cjxl_peak_rss_mb_this_host", "checks", "pass")},
                     indent=1))
    shutil.rmtree(work, ignore_errors=True)
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
