#!/usr/bin/env python3
# filename: bm_temporal_fuse_validate.py
# description: Sprint21 day-2 — score the fusion ladder against KNOWN ground truth by
#              simulating the real capture/transmit/truncation chain on a full-res reef frame.
"""
Ground-truth validation for cross-cycle temporal fusion.

WHY
Day-1 metrics compare delivered frames against each other. That can only ever say which
damaged frame a composite resembles, never whether it recovered the reef. bmcam001 has
delivered no complete image since at least 2026-09-01, so no true reference exists in the
received data at all. This tool manufactures one: it starts from a full-resolution reef
frame, pushes copies through a model of the real capture -> encode -> partial-delivery
chain, fuses them, and scores every rung against the original.

THE CHAIN MODELLED (each step's parameters come from measured bmcam001 data)
  1. RC geometry            16:9 crop -> Lanczos to 1000x562, the transmitted size
  2. Per-cycle illumination per-channel gain and additive veil drift; spread taken from
                            the GRVI card solves across the real frames (gain G 0.28-0.41,
                            veil G 0.057-0.113 -> ~15% and ~30% coefficients of variation)
  3. Transients             drifting particles and an occasional fish-sized blob, the
                            things day-1's L5 rung exists to reject
  4. Sensor/ISP noise       Gaussian, sigma matched to the measured flat-region noise
  5. Progressive encode     Pillow progressive+optimize at the OBSERVED quality (q70)
  6. Partial delivery       keep the first M of N messages at the REAL delivered prefixes
                            (default: the 2026-09-04 set, 74.2/76.6/88.2/91.0/91.1%),
                            288 raw bytes per message (image_buffer_size 384 base64 chars)
  7. Backend derivative     decode the truncated stream with LOAD_TRUNCATED_IMAGES and
                            re-encode baseline q85, exactly what the gallery serves

OUTPUT is an intake-shaped folder, so tools/bm_temporal_fuse.py runs on it unmodified,
plus truth/ and a score table (PSNR and SSIM of every rung against the original).

INPUTS
  --source IMG          full-resolution reef frame (default: the P9 card reference)
  --output-dir DIR      run folder to create
  --prefixes A,B,...    delivered fractions to simulate (default: the real 09-04 set)
  --quality Q           progressive JPEG quality (default 70, the observed ladder rung)
  --seed N              reproducibility

EXAMPLE
  python3 tools/bm_temporal_fuse_validate.py \
      --output-dir runs/sprint21_day2_truth_20260905
  python3 tools/bm_temporal_fuse.py --run-dir runs/sprint21_day2_truth_20260905 --group all

ASSUMPTIONS / KNOWN LIMITATIONS
  - The source is a different camera and reef than bmcam001. That is deliberate: this
    validates the FUSION ALGORITHM against truth, not the AOML site.
  - Illumination drift is modelled as a per-channel affine change. Real drift also moves
    shadows as the sun moves; this model does NOT reproduce that, so it flatters any
    method that assumes a static scene. Cross-day fusion will therefore score BETTER here
    than it does on real frames -- read the within-day numbers as the trustworthy ones.
  - The truncation model assumes tail loss (no internal gap), which matched 20 of 24 real
    cycles.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone

import cv2
import numpy as np
from PIL import Image, ImageFile

CHUNK_B64_CHARS = 384          # bmcam001 image_buffer_size
CHUNK_RAW_BYTES = CHUNK_B64_CHARS // 4 * 3      # 288
OUT_W, OUT_H = 1000, 562
DEFAULT_PREFIXES = "0.742,0.766,0.882,0.910,0.911"
DEFAULT_SOURCE = "reference_images/reference_reef_coral_card_01.jpg"


def rc_geometry(src_bgr):
    """Full-res frame -> the transmitted 1000x562, mimicking the RC crop + Lanczos."""
    h, w = src_bgr.shape[:2]
    target_ar = OUT_W / OUT_H
    ch = int(min(h, w / target_ar))
    cw = int(ch * target_ar)
    y0 = (h - ch) // 2
    x0 = (w - cw) // 2
    crop = src_bgr[y0:y0 + ch, x0:x0 + cw]
    return cv2.resize(crop, (OUT_W, OUT_H), interpolation=cv2.INTER_LANCZOS4)


def degrade(truth, rng, *, gain_cv=0.15, veil_cv=0.30, noise_sigma=1.5, n_particles=25):
    """One cycle's worth of real-world variation, before compression."""
    img = truth.astype(np.float32)
    gains = np.exp(rng.normal(0.0, gain_cv, 3)).astype(np.float32)
    veil_base = np.array([4.0, 14.0, 10.0], np.float32)      # BGR, green-dominant water
    veils = veil_base * np.exp(rng.normal(0.0, veil_cv, 3)).astype(np.float32)
    img = img * gains + veils
    for _ in range(n_particles):                              # drifting marine snow
        cx, cy = rng.integers(0, OUT_W), rng.integers(0, OUT_H)
        r = int(rng.integers(1, 4))
        cv2.circle(img, (int(cx), int(cy)), r,
                   tuple(float(v) for v in (img[cy, cx] + rng.uniform(25, 70))), -1)
    if rng.random() < 0.6:                                    # a fish transiting
        cx, cy = rng.integers(100, OUT_W - 100), rng.integers(60, OUT_H - 120)
        cv2.ellipse(img, (int(cx), int(cy)), (int(rng.integers(28, 60)), int(rng.integers(9, 18))),
                    float(rng.uniform(-20, 20)), 0, 360,
                    tuple(float(v) for v in (img[cy, cx] * 0.55 + 40)), -1)
    img += rng.normal(0.0, noise_sigma, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def transmit(bgr, quality, prefix):
    """Progressive encode -> keep the first M of N messages -> what the backend serves."""
    buf = io.BytesIO()
    Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).save(
        buf, format="JPEG", quality=quality, progressive=True, optimize=True)
    raw = buf.getvalue()
    n_msg = math.ceil(len(base64.b64encode(raw)) / CHUNK_B64_CHARS)
    keep = max(1, int(math.floor(n_msg * prefix)))
    cut = min(len(raw), keep * CHUNK_RAW_BYTES)
    prior = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        im = Image.open(io.BytesIO(raw[:cut]))
        im.load()
        rgb = np.asarray(im.convert("RGB"))
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = prior
    out = io.BytesIO()                                       # backend display derivative
    Image.fromarray(rgb).save(out, format="JPEG", quality=85, optimize=True)
    served = cv2.imdecode(np.frombuffer(out.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    return served, {"planned_chunks": n_msg, "received_chunks": keep,
                    "usable_prefix_pct": round(keep / n_msg * 100, 1),
                    "first_gap_index": keep, "end_seen": False, "complete": False,
                    "encoded_bytes": len(raw)}


def score(a, b):
    """PSNR and SSIM of `a` against truth `b`."""
    from skimage.metrics import structural_similarity as ssim
    a32, b32 = a.astype(np.float32), b.astype(np.float32)
    mse = float(((a32 - b32) ** 2).mean())
    psnr = 10 * math.log10(255.0 ** 2 / mse) if mse > 0 else 99.0
    s = ssim(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), cv2.cvtColor(a, cv2.COLOR_BGR2GRAY))
    return round(psnr, 2), round(float(s), 4)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--prefixes", default=DEFAULT_PREFIXES)
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    out = args.output_dir
    os.makedirs(os.path.join(out, "frames"), exist_ok=True)
    os.makedirs(os.path.join(out, "truth"), exist_ok=True)

    src = cv2.imread(os.path.expanduser(args.source), cv2.IMREAD_COLOR)
    if src is None:
        print(f"[ERROR] cannot read source {args.source}", file=sys.stderr)
        return 1
    truth = rc_geometry(src)
    cv2.imwrite(os.path.join(out, "truth", "ground_truth.png"), truth)
    print(f"[truth] {args.source} {src.shape[1]}x{src.shape[0]} -> {OUT_W}x{OUT_H} ground truth")

    rng = np.random.default_rng(args.seed)
    prefixes = [float(x) for x in args.prefixes.split(",")]
    recs = []
    for i, pfx in enumerate(prefixes):
        deg = degrade(truth, rng)
        served, delivery = transmit(deg, args.quality, pfx)
        stamp = f"2026-09-04T{14 + i:02d}:00:00Z"
        name = stamp.replace(":", "-") + ".jpg"
        cv2.imwrite(os.path.join(out, "frames", name), served,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        p, s = score(served, truth)
        recs.append({"capture_utc": stamp, "date": stamp[:10], "hour_utc": stamp[11:13],
                     "archived_as": f"frames/{name}", "source_path": args.source,
                     "bytes": os.path.getsize(os.path.join(out, "frames", name)),
                     "width": OUT_W, "height": OUT_H, "duplicate_copies": [],
                     "sharpness_lapvar": round(float(cv2.Laplacian(
                         cv2.cvtColor(served, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()), 1),
                     "delivery": delivery, "simulated": True,
                     "truth_psnr": p, "truth_ssim": s})
        print(f"  cycle {i}  prefix {delivery['usable_prefix_pct']:5.1f}%  "
              f"{delivery['received_chunks']:3d}/{delivery['planned_chunks']:3d} msgs  "
              f"PSNR {p:5.2f}  SSIM {s:.4f}")

    json.dump(recs, open(os.path.join(out, "frames_manifest.json"), "w"), indent=2)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                         stderr=subprocess.DEVNULL).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "unknown"
    json.dump({"run_tag": f"truth_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
               "git_commit": commit, "args": vars(args),
               "chunk_raw_bytes": CHUNK_RAW_BYTES, "synthetic": True,
               "single_frame_scores": {r["capture_utc"]: [r["truth_psnr"], r["truth_ssim"]]
                                       for r in recs}},
              open(os.path.join(out, "run_manifest.json"), "w"), indent=2)
    best = max(recs, key=lambda r: r["truth_ssim"])
    print(f"[truth] best single frame: {best['capture_utc']} "
          f"PSNR {best['truth_psnr']} SSIM {best['truth_ssim']}")
    print(f"[truth] wrote {out}/ -- now run tools/bm_temporal_fuse.py --run-dir {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
