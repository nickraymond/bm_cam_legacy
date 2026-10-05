#!/usr/bin/env python3
# filename: s28_r4_options.py
# description: Sprint28 R4 options for Nick: is nrjxl (1600x900 native) >= today's JPEG (1000x562) of the SAME scene, per message budget / distance? Metrics vs a lossless neutral RAW render + 1:1 cut sheet.
"""
Nick's bar (via the EM, 2026-10-05): apples to apples.
  ROI fixed: still.crop [1504, 846, 1600, 900], native density (no bin, no smaller crop).
  Today (bmcam003/004, repo profiles + configs pulled 2026-09-28): the same crop -> lanczos to
    1000x562 (output_width 1000) -> progressive JPEG, quality ladder 90,80,...,15,13,11,9,
    first quality that fits still.message_cap 195 at 384 b64 chars per message.
  Reference: the LOSSLESS neutral render of the RAW ROI (no compression at all).
  Scored, both against the reference:
    (1) nrjxl: the production encoder (rc_raw_jxl, cjxl e5, byte-target search or a fixed
        distance) -> the rig's study decoder -> the SAME neutral render;
    (2) today's JPEG: today's exact pjpg path (rc_jpeg_encoder.encode_progressive + the
        ladder) applied to the SAME neutral render, decoded, UPSAMPLED to 1600x900 (lanczos).
  Fairness: today's real JPEG starts from the ISP output (tone curve, denoise, sharpening,
  lens shading), which a neutral RAW render does not have; scoring it against the neutral
  reference would mostly measure the LOOK. So the scored "today" is today's density + JPEG
  compression on the same neutral pixels; the real ISP JPEG is shown in the cut sheet
  (labelled, unscored).
  Metrics: SSIMULACRA2 (higher = better; libjxl `ssimulacra2`), butteraugli 3-norm (lower =
  better; `butteraugli_main`), SSIM on luma. PASS = SSIMULACRA2 >= JPEG's AND butteraugli
  3-norm <= JPEG's.

Inputs: --dng, --metadata (rpicam --metadata JSON of the same exposure), --isp-jpg (rpicam's
        native JPEG, optional), --rig (compression_study + src from rig origin/main).
Outputs (--out): scores.csv, summary.json, cutsheet.png (1:1 native-px windows: fine
        texture, strongest edges, darkest textured area), the PNGs scored, run_manifest.json.
Run (rig venv: cv2, the study decoder):
  PYTHONPATH=$RIG/src:$RIG ~/Documents/GitHub/nereus-camera-test-rig/.venv/bin/python \\
      tools/s28_r4_options.py --rig $RIG --dng X.dng --metadata X.json --isp-jpg X.jpg \\
      --label study_cool_s-1 --out runs/s28_r4_options_20261005/study_cool_s-1
Limits: one scene per run (the answer is scene-dependent: daylight R4 frames need ~2x the
bytes of the indoor study frame at the same distance). The neutral render has no lens
shading / denoise / sharpening (both sides alike). Noise-vs-gain is a simulation (--noise).
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
ROI = [1504, 846, 1600, 900]
CHUNK = 384
LADDER = [90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 13, 11, 9]
BUDGETS = [174, 195, 230, 260, 300, 350, 400, 500]
FIXED_D = [3.8, 6.0, 9.7, 12.0, 15.0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rig", required=True)
    ap.add_argument("--dng", required=True)
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--isp-jpg")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--noise", action="store_true", help="also the low-gain byte simulation")
    args = ap.parse_args(argv)
    sys.path[:0] = [os.path.join(args.rig, "src"), args.rig, os.path.join(REPO, "BM_Devel_Pi")]
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from compression_study.methods import raw_planes as rp

    import rc_jpeg_encoder as J
    import rc_raw_jxl as X

    out = args.out
    os.makedirs(out, exist_ok=True)
    with open(args.metadata) as fh:
        meta = json.load(fh)
    crop = X.read_dng_crop(args.dng, ROI)
    cfa, black, white = crop["cfa"], crop["black"], crop["white"]
    dg = float(meta.get("DigitalGain") or 1.0)
    gains = meta["ColourGains"]
    ccm = np.array(meta["ColourCorrectionMatrix"], np.float32).reshape(3, 3)
    bayer_code = {"BGGR": cv2.COLOR_BayerRG2RGB, "RGGB": cv2.COLOR_BayerBG2RGB,
                  "GRBG": cv2.COLOR_BayerGB2RGB, "GBRG": cv2.COLOR_BayerGR2RGB}[cfa]

    def render(mosaic):
        """The backend's neutral render v1 (CONTAINER.md §5): normalise -> bilinear demosaic
        -> x DigitalGain -> camera WB -> CCM -> sRGB 8-bit. Same function for every input."""
        m = np.clip((np.asarray(mosaic, np.float32) - black) / (white - black), 0, 1)
        rgb = cv2.cvtColor((m * 65535 + 0.5).astype(np.uint16), bayer_code).astype(np.float32) / 65535
        rgb *= dg * np.array([gains[0], 1.0, gains[1]], np.float32)
        rgb = np.clip(rgb @ ccm.T, 0, 1)
        s = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
        return (np.clip(s, 0, 1) * 255 + 0.5).astype(np.uint8)

    def save(img, name):
        path = os.path.join(out, name)
        Image.fromarray(img).save(path)
        return path

    def ssim_luma(a, b):
        ya = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float64)
        yb = cv2.cvtColor(b, cv2.COLOR_RGB2GRAY).astype(np.float64)
        c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
        blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
        ma, mb = blur(ya), blur(yb)
        va, vb, cov = blur(ya * ya) - ma * ma, blur(yb * yb) - mb * mb, blur(ya * yb) - ma * mb
        s = ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))
        return float(s.mean())

    def score(ref_path, ref_img, img, name):
        path = save(img, name)
        s2 = subprocess.run(["ssimulacra2", ref_path, path], capture_output=True, text=True)
        ba = subprocess.run(["butteraugli_main", ref_path, path], capture_output=True, text=True)
        ssim2 = float(s2.stdout.strip().split()[-1])
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", ba.stdout)
        pnorm = re.search(r"3-norm:\s*([0-9.]+)", ba.stdout)
        return {"ssimulacra2": round(ssim2, 3), "butteraugli_max": round(float(nums[0]), 3),
                "butteraugli_3norm": round(float(pnorm.group(1)), 3) if pnorm else None,
                "ssim_luma": round(ssim_luma(ref_img, img), 5), "png": name}

    ref = render(crop["mosaic"])
    ref_path = save(ref, "reference_lossless_neutral.png")
    rows = []

    # ---- today's JPEG on the neutral pixels (scored) ----------------------------------
    src = Image.fromarray(ref).resize((1000, 562), Image.Resampling.LANCZOS)
    pick = None
    for q in LADDER:
        enc = J.encode_progressive(src, q, CHUNK)
        if enc["message_count"] <= 195:
            pick = enc
            break
    if pick is None:
        pick = enc
    jdec = np.asarray(Image.open(io.BytesIO(pick["jpeg_data"])).convert("RGB")
                      .resize((1600, 900), Image.Resampling.LANCZOS))
    jrow = {"option": "today pjpg (1000x562 q-ladder, UPSAMPLED to 1600x900)",
            "budget_msgs": 195, "distance": "", "quality": pick["quality"],
            "bytes": pick["jpeg_bytes"], "msgs": pick["message_count"]}
    jrow.update(score(ref_path, ref, jdec, "today_pjpg_upsampled.png"))
    rows.append(jrow)
    print(f"[Q] today pjpg q{pick['quality']} {pick['jpeg_bytes']} B {pick['message_count']} msgs "
          f"ssimulacra2 {jrow['ssimulacra2']}", flush=True)

    # ---- nrjxl: per message budget (byte-target search, d_max lifted) and per distance --
    codes = X.code_planes(crop)
    colour = X.colour_params(meta)
    tiles = {}

    def nrjxl_at(d):
        with tempfile.TemporaryDirectory(prefix="s28q_") as work:
            payloads, _ = X.encode_rung(codes, d, 5, work, cjxl=shutil.which("cjxl"),
                                        runner=X.run_capped, timeout_s=600)
        params = X.build_params(crop_xywh=ROI, native_wh=(crop["native_w"], crop["native_h"]),
                                crc=0, colour=colour, distance=d, effort=5)
        return X.seal_container(w=ROI[2], h=ROI[3], cfa=cfa, black=black, white=white,
                                params=params, payloads=payloads)[0]

    def d_for_budget(budget):
        """Largest-quality distance whose blob fits `budget` msgs (bisection, 0.1..15)."""
        lo, hi = 0.1, 15.0
        if X.message_count(len(nrjxl_at(hi)), CHUNK) > budget:
            return None
        for _ in range(12):
            mid = round((lo + hi) / 2, 3)
            if X.message_count(len(nrjxl_at(mid)), CHUNK) <= budget:
                hi = mid
            else:
                lo = mid
        return hi

    def nrjxl_row(option, budget, d):
        blob = nrjxl_at(d)
        img = render(rp.decode(blob))
        r = {"option": option, "budget_msgs": budget, "distance": d, "quality": "",
             "bytes": len(blob), "msgs": X.message_count(len(blob), CHUNK)}
        r.update(score(ref_path, ref, img, f"nrjxl_d{d}.png"))
        r["pass_vs_jpeg"] = (r["ssimulacra2"] >= jrow["ssimulacra2"]
                             and (r["butteraugli_3norm"] or 0) <= (jrow["butteraugli_3norm"] or 0))
        tiles[option] = (img, r)
        print(f"[Q] {option}: d={d} {len(blob)} B {r['msgs']} msgs ssimulacra2 "
              f"{r['ssimulacra2']} b3 {r['butteraugli_3norm']} -> "
              f"{'PASS' if r['pass_vs_jpeg'] else 'FAIL'}", flush=True)
        return r

    for b in BUDGETS:
        d = d_for_budget(b)
        if d is None:
            rows.append({"option": f"(a) nrjxl at {b} msgs", "budget_msgs": b,
                         "distance": ">15", "note": "does not fit even at d 15"})
            continue
        rows.append(nrjxl_row(f"(a) nrjxl at {b} msgs", b, d))
    for d in FIXED_D:
        rows.append(nrjxl_row(f"(b) nrjxl at d={d}", "", d))

    # ---- (d) low gain: bytes vs simulated analogue gain at fixed d ----------------------
    noise_rows = []
    if args.noise:
        g0 = float(meta["AnalogueGain"])
        base = crop["mosaic"].astype(np.float64)
        rng = np.random.default_rng(1)
        for g in (g0, 2.0, 4.0, 8.0):
            extra = max(0.0, g / g0 - 1.0)          # ASSUMPTION: DN shot variance ~ gain * DN
            sig = np.clip(base - black, 0, None)
            mos = np.clip(np.rint(base + rng.normal(0, 1, base.shape) * np.sqrt(extra * sig)),
                          0, white).astype(np.uint16)
            c2 = dict(crop, mosaic=mos)
            cc = X.code_planes(c2)
            for d in (6.0, 9.7):
                with tempfile.TemporaryDirectory(prefix="s28n_") as work:
                    payloads, _ = X.encode_rung(cc, d, 5, work, cjxl=shutil.which("cjxl"),
                                                runner=X.run_capped, timeout_s=600)
                n = sum(len(p) for p in payloads)
                noise_rows.append({"sim_analogue_gain": round(g, 3), "distance": d, "bytes": n})
                print(f"[Q] noise gain {g:.2f} d={d}: {n} B", flush=True)

    # ---- outputs -------------------------------------------------------------------------
    keys = ["option", "budget_msgs", "distance", "quality", "bytes", "msgs", "ssimulacra2",
            "butteraugli_3norm", "butteraugli_max", "ssim_luma", "pass_vs_jpeg", "note", "png"]
    with open(os.path.join(out, "scores.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    if noise_rows:
        with open(os.path.join(out, "noise.csv"), "w", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(noise_rows[0]))
            wr.writeheader()
            wr.writerows(noise_rows)
    passing = [r for r in rows if str(r.get("option", "")).startswith("(a)") and r.get("pass_vs_jpeg")]
    smallest = min(passing, key=lambda r: r["budget_msgs"]) if passing else None

    # cut sheet: 3 windows chosen from the reference (fine texture, edges, dark + textured)
    gray = cv2.cvtColor(ref, cv2.COLOR_RGB2GRAY).astype(np.float32)
    win = 200
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    sob = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    box = lambda x: cv2.boxFilter(x, -1, (win, win))  # noqa: E731
    pick_win = lambda score_map: np.unravel_index(  # noqa: E731
        np.argmax(score_map[win // 2:-win // 2, win // 2:-win // 2]), (900 - win, 1600 - win))
    windows = {"fine texture": pick_win(box(lap)), "edges": pick_win(box(sob)),
               "dark area": pick_win(box(lap) / (box(gray) + 8.0) ** 2)}
    cols = [("reference (lossless neutral)", ref, ""),
            ("today pjpg q%s (UPSAMPLED)" % pick["quality"], jdec,
             f"{pick['jpeg_bytes']} B {pick['message_count']} msgs s2={jrow['ssimulacra2']}")]
    if args.isp_jpg:
        isp = Image.open(args.isp_jpg).convert("RGB").crop((ROI[0], ROI[1], ROI[0] + ROI[2],
                                                            ROI[1] + ROI[3]))
        isp_small = isp.resize((1000, 562), Image.Resampling.LANCZOS)
        ienc = None
        for q in LADDER:
            ienc = J.encode_progressive(isp_small, q, CHUNK)
            if ienc["message_count"] <= 195:
                break
        idec = np.asarray(Image.open(io.BytesIO(ienc["jpeg_data"])).convert("RGB")
                          .resize((1600, 900), Image.Resampling.LANCZOS))
        cols.append((f"real ISP pjpg q{ienc['quality']} (UPSAMPLED, NOT scored: ISP look)", idec,
                     f"{ienc['jpeg_bytes']} B {ienc['message_count']} msgs"))
    for name in ("(a) nrjxl at 195 msgs",
                 smallest["option"] if smallest else None, "(b) nrjxl at d=12.0"):
        if name and name in tiles and name not in [c[0] for c in cols]:
            img, r = tiles[name]
            cols.append((f"{name} d={r['distance']}", img,
                         f"{r['bytes']} B {r['msgs']} msgs s2={r['ssimulacra2']} "
                         f"{'PASS' if r['pass_vs_jpeg'] else 'FAIL'}"))
    tw = win * 2
    sheet = Image.new("RGB", (len(cols) * (tw + 8) + 8, 3 * (tw + 40) + 60), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    dr.text((8, 6), f"Sprint28 nrjxl vs today's JPEG | {args.label} | ROI {ROI} native px | "
            "tiles = 200x200 native px shown 2x (nearest) | JPEG tiles are 1000x562 UPSAMPLED to "
            "1600x900 | neutral render both sides", fill=(0, 0, 0), font=font)
    dr.text((8, 22), "scores vs the lossless neutral RAW render: SSIMULACRA2 (higher better), "
            "butteraugli 3-norm (lower better); PASS = both at least as good as today's JPEG",
            fill=(0, 0, 0), font=font)
    for ri, (wname, (wy, wx)) in enumerate(windows.items()):
        for ci, (cname, img, info) in enumerate(cols):
            tile = Image.fromarray(img[wy:wy + win, wx:wx + win]).resize((tw, tw), Image.NEAREST)
            px, py = 8 + ci * (tw + 8), 44 + ri * (tw + 40)
            sheet.paste(tile, (px, py + 28))
            dr.text((px, py), f"{wname} @({wx},{wy}) | {cname}"[:70], fill=(0, 0, 0), font=font)
            dr.text((px, py + 13), info[:70], fill=(60, 60, 60), font=font)
    sheet.save(os.path.join(out, "cutsheet.png"))
    summary = {"label": args.label, "roi": ROI, "today": jrow,
               "smallest_passing_budget_msgs": smallest["budget_msgs"] if smallest else None,
               "smallest_passing": smallest, "windows_crop_px": {k: [int(v[1]), int(v[0]), win, win]
                                                                 for k, v in windows.items()}}
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    with open(os.path.join(out, "run_manifest.json"), "w") as fh:
        json.dump({"utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "inputs": {"dng": args.dng, "metadata": args.metadata, "isp_jpg": args.isp_jpg},
                   "command": " ".join(sys.argv), "cjxl": subprocess.run(
                       ["cjxl", "--version"], capture_output=True, text=True).stdout.split("\n")[0],
                   "outputs": ["scores.csv", "summary.json", "cutsheet.png", "noise.csv"]}, fh,
                  indent=1)
    print(f"[Q] smallest message budget with nrjxl >= today's JPEG on this scene: "
          f"{summary['smallest_passing_budget_msgs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
