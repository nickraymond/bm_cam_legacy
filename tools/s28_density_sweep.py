#!/usr/bin/env python3
# filename: s28_density_sweep.py
# description: Sprint28 density sweep (Nick, via the EM, 2026-10-05): fixed ROI 1600x900 + fixed 195-msg cap; at which output width does nrjxl give SIGNIFICANTLY more detail than today's JPEG (1000x562)?
"""
Nick's question, worked in reverse: ROI [1504, 846, 1600, 900] and still.message_cap 195 are
fixed. Sweep the nrjxl output width of the SAME ROI: 1600 (native), 1440, 1360, 1280, 1200,
1120, 1000 (= the JPEG's density). At each width the byte target is production's: fill 0.97 of
the 195-chunk room (rc_raw_jxl target_fill 0.97, room_bytes(195) = 56160 B), cap 195.

Two ways to make a W x H image that keeps the RAW value:
  A (Nick's first choice) "bayer": each Bayer plane resampled in raw DN (area filter + a
    sub-pixel phase correction so R / G1 / G2 / B stay co-sited at the new pitch), re-mosaicked
    at W x H. Still a mosaic: the production 4-plane encoder (code_planes -> cjxl -m 1 -e 5) and
    the rig's study decoder run unchanged; a DNG of the decode is still possible.
  B "rgb": demosaic (bilinear) at native, area-resize LINEAR camera RGB to W x H, the same sqrt
    12-bit curve per channel, ONE 3-channel cjxl -m 1 -e 5 (16-bit PPM). No mosaic: no DNG, and
    the demosaic is fixed on the unit (no backend edge-aware demosaic later).
  B" "rgbw": as B' but with the camera WB (x DigitalGain) applied BEFORE coding, clipped at 1
    (the render's own first step, so nothing visible is lost; RAW headroom above the WB clip
    is). Added because B' on underwater TG-7 frames showed red-channel blotches: camera-space
    red is dim underwater, the codec quantises it as dark, and the large red WB gain then
    amplifies the error (metrics still favoured B'; the eye did not).
  B' "rgbv": the same RGB, cjxl VarDCT (-m 0 -e 5): the codec made for RGB. Added because the
    smoke test showed lossy modular on noisy 12-bit RGB codes wastes bytes (cool -1, 1000 px,
    ~55 kB: modular 57.4 vs VarDCT 72.9 SSIMULACRA2).
For A the PRODUCTION search itself (rc_raw_jxl.choose_rate, d_max lifted to 15, ample time)
is also run and its d / bytes reported (it lands at 0.93..0.97 fill, so its d may differ a
little from the scored one).

Scoring (as s28_r4_options.py): reference = the LOSSLESS neutral render of the RAW ROI at
native 1600x900; every candidate is rendered by the same neutral function and UPSAMPLED to
1600x900 (lanczos) for scoring AND display. Today's JPEG = today's exact pjpg path
(rc_jpeg_encoder.encode_progressive, ladder 90..9, first q <= 195 msgs at 384 b64 chars) on
the same neutral pixels at 1000x562. Metrics: SSIMULACRA2 (higher better), butteraugli 3-norm
(lower better). PASS = both at least the JPEG's; SIG ("significantly better", Nick's bar) =
SSIMULACRA2 >= JPEG + 5 AND butteraugli 3-norm <= 0.9 x JPEG.

Inputs:  --dng, --metadata (rpicam --metadata JSON), --rig (compression_study + src), --label.
Outputs (--out/<label>/): scores.csv, summary.json, cutsheet_<row>.png (3 windows x
         reference | JPEG | widths, 200x200 native px shown 2x nearest), slider/*.jpg (1600x900
         display renders, JPEG q92), run_manifest.json.
Example:
  PYTHONPATH=$RIG/src:$RIG ~/Documents/GitHub/nereus-camera-test-rig/.venv/bin/python \\
      tools/s28_density_sweep.py --rig $RIG --dng X.dng --metadata X.json \\
      --label study_cool_s-1 --out runs/s28_density_sweep_20261005
Known limitations: indoor study scenes only (daylight waits for Tue's lossless frame); the
neutral render has no lens shading / denoise / sharpening (all sides alike); B's container
bytes = payload + the A container's header size (no B container exists); the NR container has
no output-scale param yet (a width < 1600 needs one before it ships).
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
CAP = 195
CHUNK = 384
FILL = 0.97
LADDER = [90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 13, 11, 9]
WIDTHS = [1600, 1440, 1360, 1280, 1200, 1120, 1000]
SIG_S2, SIG_B3 = 5.0, 0.9


def size_for(w):
    """W x H of the same 16:9 ROI, both even (A keeps the 2x2 CFA)."""
    h = int(round(w * ROI[3] / ROI[2]))
    return w, h - (h % 2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rig", required=True)
    ap.add_argument("--dng", help="IMX708 DNG (with --metadata)")
    ap.add_argument("--metadata")
    ap.add_argument("--orf", help="TG-7 ORF instead: mapped to IMX708 geometry by the rig's "
                    "tg7_budget.map_to_imx (needs --rig at rig 73fdbe2 or later)")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rows", default="bayer,rgb,rgbv")
    ap.add_argument("--widths", default=",".join(str(w) for w in WIDTHS))
    ap.add_argument("--row-widths", default="",
                    help="per-row widths, e.g. 'bayer:1600;rgbv:1600,1440,1000' (overrides "
                         "--widths for the rows named)")
    ap.add_argument("--no-slider", action="store_true")
    ap.add_argument("--equal-quality", action="store_true",
                    help="only: B3a (rgbwa) at 1600, bisect d to the JPEG's SSIMULACRA2 "
                         "(equal quality); writes equal_quality.json")
    ap.add_argument("--merge", action="store_true",
                    help="keep the rows of an existing summary.json that this run does not redo")
    args = ap.parse_args(argv)
    sys.path[:0] = [os.path.join(args.rig, "src"), args.rig, os.path.join(REPO, "BM_Devel_Pi")]
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from compression_study.methods import raw_planes as rp

    import rc_jpeg_encoder as J
    import rc_raw_jxl as X

    out = os.path.join(args.out, args.label)
    os.makedirs(out, exist_ok=True)
    tmp_png = tempfile.mkdtemp(prefix="s28ds_")
    source = {}
    if args.orf:
        # "TG-7 raw, resampled to IMX708 geometry, an approximation" (rig tg7_budget.py,
        # 73fdbe2): the TG-7 crop with the 1600x900 ROI's FOV fraction (1394x784 TG-7 px),
        # each CFA plane lanczos-resampled x1.148, 10-bit (black 64, white 1023), TG-7 noise
        # kept. Render = reef_budget.render_lin (as-shot WB, Olympus CCM, clip at 1) + an
        # exposure scale putting the REFERENCE's G p99.5 at 0.9 (the same scale for every image).
        from compression_study.preview_loss import tg7_budget as TG
        from host_tools.tg7.orf_io import read_orf
        f = read_orf(args.orf)
        scene, tbox, s_map = TG.map_to_imx(f, (ROI[2], ROI[3]))
        crop = {"mosaic": scene["mosaic"], "cfa": scene["cfa"], "black": scene["black"],
                "white": scene["white"], "native_w": 4608, "native_h": 2592}
        g3 = np.asarray(scene["gains"], np.float32)
        ccm = np.asarray(scene["ccm"], np.float32)
        meta = {"ExposureTime": float(f.exposure_s) * 1e6, "AnalogueGain": float(f.iso) / 100,
                "ColourGains": [float(g3[0] / g3[1]), float(g3[2] / g3[1])],
                "ColourCorrectionMatrix": [float(v) for v in ccm.ravel()], "DigitalGain": 1.0}
        ex = f.source.get("exif", {})
        source = {"kind": "tg7_orf", "orf": args.orf, "tg7_box_xywh": list(tbox),
                  "scale_to_imx": round(s_map, 4), "exposure_s": f.exposure_s, "iso": f.iso,
                  "fnumber": f.fnumber, "water_depth_m": ex.get("water_depth_m"),
                  "label": "TG-7 resampled to IMX708 geometry, an approximation"}
        render_mode = "tg7"
    else:
        with open(args.metadata) as fh:
            meta = json.load(fh)
        crop = X.read_dng_crop(args.dng, ROI)
        g3 = np.array([meta["ColourGains"][0], 1.0, meta["ColourGains"][1]], np.float32)
        ccm = np.array(meta["ColourCorrectionMatrix"], np.float32).reshape(3, 3)
        source = {"kind": "imx708_dng", "dng": args.dng, "metadata": args.metadata}
        render_mode = "imx708"
    cfa, black, white = crop["cfa"], crop["black"], crop["white"]
    dg = float(meta.get("DigitalGain") or 1.0)
    expo = [1.0]                           # tg7: set from the reference below
    bayer_code = getattr(cv2, {"BGGR": "COLOR_BayerRG2RGB", "RGGB": "COLOR_BayerBG2RGB",
                               "GRBG": "COLOR_BayerGB2RGB", "GBRG": "COLOR_BayerGR2RGB"}[cfa])
    target_bytes = int(FILL * 3 * ((CAP * CHUNK) // 4))
    cjxl = shutil.which("cjxl")

    # ---- the neutral render (backend v1, CONTAINER.md §5), split so B can skip the demosaic --
    def demosaic_linear(mosaic):
        m = np.clip((np.asarray(mosaic, np.float32) - black) / (white - black), 0, 1)
        return cv2.cvtColor((m * 65535 + 0.5).astype(np.uint16), bayer_code).astype(np.float32) / 65535

    def wb(rgb_lin):
        """Camera WB (and DigitalGain) applied, clipped at 1: exactly the render's own first
        step (tg7: min(rgb x gains, 1); imx708: rgb x dg x gains, clipped at 1 here)."""
        g = g3 if render_mode == "tg7" else dg * g3
        return np.minimum(rgb_lin * g, 1.0)

    def finish(rgb_lin, _lin_out=None, wb_applied=False):
        if wb_applied:                     # rgbw: the decode is already white-balanced
            rgb = np.clip(rgb_lin @ ccm.T, 0, None if render_mode == "tg7" else 1)
            if render_mode == "tg7":
                rgb = np.clip(rgb * expo[0], 0, 1)
        elif render_mode == "tg7":         # reef_budget.render_lin, then x the exposure scale
            rgb = np.clip(np.minimum(rgb_lin * g3, 1.0) @ ccm.T, 0, None)
            if _lin_out is not None:
                _lin_out.append(rgb)
            rgb = np.clip(rgb * expo[0], 0, 1)
        else:                              # the backend's neutral render v1 (IMX708)
            rgb = rgb_lin * dg * g3
            rgb = np.clip(rgb @ ccm.T, 0, 1)
        s = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
        return (np.clip(s, 0, 1) * 255 + 0.5).astype(np.uint8)

    def up(img):
        if img.shape[1] == ROI[2] and img.shape[0] == ROI[3]:
            return img
        return np.asarray(Image.fromarray(img).resize((ROI[2], ROI[3]), Image.Resampling.LANCZOS))

    if render_mode == "tg7":
        lin = []
        finish(demosaic_linear(crop["mosaic"]), lin)
        expo[0] = 0.9 / max(float(np.percentile(lin[0][..., 1], 99.5)), 1e-6)
        source["exposure_scale"] = round(expo[0], 4)
    ref = finish(demosaic_linear(crop["mosaic"]))
    ref_path = os.path.join(tmp_png, "ref.png")
    Image.fromarray(ref).save(ref_path)

    def score(img):
        path = os.path.join(tmp_png, "cand.png")
        Image.fromarray(img).save(path)
        s2 = subprocess.run(["ssimulacra2", ref_path, path], capture_output=True, text=True)
        ba = subprocess.run(["butteraugli_main", ref_path, path], capture_output=True, text=True)
        b3 = re.search(r"3-norm:\s*([0-9.]+)", ba.stdout)
        return {"ssimulacra2": round(float(s2.stdout.strip().split()[-1]), 3),
                "butteraugli_3norm": round(float(b3.group(1)), 3)}

    # ---- today's JPEG (1000x562, the ladder) ---------------------------------------------
    src = Image.fromarray(ref).resize((1000, 562), Image.Resampling.LANCZOS)
    pick = None
    for q in LADDER:
        pick = J.encode_progressive(src, q, CHUNK)
        if pick["message_count"] <= CAP:
            break
    jimg = up(np.asarray(Image.open(io.BytesIO(pick["jpeg_data"])).convert("RGB")))
    jrow = {"row": "jpeg", "width": 1000, "height": 562, "distance": "", "quality": pick["quality"],
            "bytes": pick["jpeg_bytes"], "msgs": pick["message_count"], **score(jimg)}
    print(f"[DS] {args.label} today pjpg q{pick['quality']} {pick['jpeg_bytes']} B "
          f"{pick['message_count']} msgs s2 {jrow['ssimulacra2']} b3 {jrow['butteraugli_3norm']}",
          flush=True)

    # ---- A: Bayer-plane resample ------------------------------------------------------------
    offsets = X.plane_offsets(cfa)

    def resample_mosaic(w, h):
        """Each plane area-resized to (w/2, h/2) in raw DN, then shifted so a plane sample sits
        where the new mosaic's CFA site is: plane pixel k (offset d) has native centre
        (2k + d + 0.5)/s - 0.5; area resize puts it at (k + 0.5)/s - 0.5 in plane px, so the
        remaining shift is (d/2 - 0.25)(1/s - 1) source plane px = that x s output plane px."""
        if (w, h) == (ROI[2], ROI[3]):
            return crop["mosaic"]
        sx, sy = w / ROI[2], h / ROI[3]
        new = np.zeros((h, w), np.float32)
        planes = X.split_planes(crop["mosaic"], cfa)
        for name, (dy, dx) in offsets.items():
            p = cv2.resize(planes[name].astype(np.float32), (w // 2, h // 2),
                           interpolation=cv2.INTER_AREA)
            shx = (dx / 2 - 0.25) * (1 / sx - 1) * sx
            shy = (dy / 2 - 0.25) * (1 / sy - 1) * sy
            gx, gy = np.meshgrid(np.arange(w // 2, dtype=np.float32) + shx,
                                 np.arange(h // 2, dtype=np.float32) + shy)
            new[dy::2, dx::2] = cv2.remap(p, gx, gy, cv2.INTER_LINEAR,
                                          borderMode=cv2.BORDER_REFLECT)
        return np.clip(np.rint(new), 0, white).astype(np.uint16)

    colour = X.colour_params(meta)

    def bayer_blob(mosaic, d):
        c = dict(crop, mosaic=mosaic)
        with tempfile.TemporaryDirectory(prefix="s28dsa_") as work:
            payloads, _ = X.encode_rung(X.code_planes(c), d, 5, work, cjxl=cjxl,
                                        runner=X.run_capped, timeout_s=600)
        params = X.build_params(crop_xywh=ROI, native_wh=(crop["native_w"], crop["native_h"]),
                                crc=0, colour=colour, distance=d, effort=5)
        h, w = mosaic.shape
        return X.seal_container(w=w, h=h, cfa=cfa, black=black, white=white, params=params,
                                payloads=payloads)[0]

    def bayer_decode(blob):
        return finish(demosaic_linear(rp.decode(blob)))

    class _Budget:                         # ample time: only the 195 cap binds (R4 at 195 msgs)
        seconds_per_message = 1.3

        @staticmethod
        def remaining_s():
            return 100000.0

        @staticmethod
        def messages_fit(n):
            return True

    def production_pick(mosaic):
        c = dict(crop, mosaic=mosaic)
        cfg = dict(X.DEFAULT_CONFIG, d_max=15.0, encode_max_s=600, target_fill=FILL)
        with tempfile.TemporaryDirectory(prefix="s28dsp_") as work:
            try:
                r = X.choose_rate(X.slim_crop(c), X.code_planes(c), colour, cfg, crop_xywh=ROI,
                                  budget=_Budget, message_cap=CAP, chunk_b64_chars=CHUNK,
                                  reserve_msgs=0, fallback_msgs=CAP, work_dir=work, cjxl=cjxl,
                                  runner=X.run_capped, log=lambda *_: None)
            except X.RawFallback as exc:
                return {"prod_rfb": exc.code}
        return {"prod_distance": r["distance"], "prod_bytes": len(r["blob"]),
                "prod_msgs": r["message_count"], "prod_attempts": r["attempts"]}

    # ---- B: linear RGB resample, 3-channel cjxl -------------------------------------------
    lut = X.sqrt_lut(black, white)
    scale = (2 ** X.CODE_BITS - 1) / np.sqrt(white - black)
    rgb_native = None

    def rgb_lin(w, h):
        nonlocal rgb_native
        if rgb_native is None:
            rgb_native = demosaic_linear(crop["mosaic"])        # 0..1 linear, black removed
        if (w, h) == (ROI[2], ROI[3]):
            return rgb_native
        return cv2.resize(rgb_native, (w, h), interpolation=cv2.INTER_AREA)

    def rgb_codes(lin):
        dn = np.clip(np.rint(lin * (white - black) + black), 0, white).astype(np.int64)
        return lut[dn]

    def rgb_blob(codes, d, mode="1"):
        h, w, _ = codes.shape
        with tempfile.TemporaryDirectory(prefix="s28dsb_") as work:
            ppm, jxl = os.path.join(work, "x.ppm"), os.path.join(work, "x.jxl")
            with open(ppm, "wb") as fh:
                fh.write(f"P6\n{w} {h}\n{2 ** X.CODE_BITS - 1}\n".encode("ascii"))
                fh.write(np.ascontiguousarray(codes, dtype=">u2").tobytes())
            subprocess.run([cjxl, ppm, jxl, "-m", mode, "-e", "5", "-d", f"{d:.4f}",
                            "--num_threads=0", "--quiet"], check=True, capture_output=True)
            with open(jxl, "rb") as fh:
                return fh.read()

    def rgb_decode(blob, w, h, wb_applied=False):
        with tempfile.TemporaryDirectory(prefix="s28dsd_") as work:
            jxl, png = os.path.join(work, "x.jxl"), os.path.join(work, "x.png")
            with open(jxl, "wb") as fh:
                fh.write(blob)
            subprocess.run(["djxl", jxl, png, "--bits_per_sample=16", "--quiet"], check=True,
                           capture_output=True)
            dec = cv2.cvtColor(cv2.imread(png, cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB)
        codes = dec.astype(np.float64) / 65535 * (2 ** X.CODE_BITS - 1)
        v = (codes / scale) ** 2                                # DN above black
        lin = np.clip(v / (white - black), 0, 1).astype(np.float32)
        return lin if wb_applied is None else finish(lin, wb_applied=wb_applied)

    header_b = None                        # the A container's header bytes (added to B)

    def fit_d(make, lo=0.1, hi=25.0):
        """Smallest d (best quality) whose bytes <= the 0.97 target; bisection on d."""
        b_hi = make(hi)
        if len(b_hi) > target_bytes:
            return hi, b_hi, False
        best = (hi, b_hi)
        for _ in range(11):
            mid = round((lo + hi) / 2, 3)
            b = make(mid)
            if len(b) <= target_bytes:
                hi, best = mid, (mid, b)
            else:
                lo = mid
            if hi - lo < 0.02:
                break
        return best[0], best[1], True

    if args.equal_quality:
        # equal quality (EM / Nick 2026-10-05): the largest d (fewest bytes) whose B3a decode
        # still scores SSIMULACRA2 >= the JPEG's. Container header: a nominal 60 B is added
        # (the 4-plane container's header is ~50-60 B; B3a has no container yet).
        w, h = ROI[2], ROI[3]
        lin = rgb_lin(w, h)
        g = g3 if render_mode == "tg7" else dg * g3
        sc_ = max(1.0, float((lin * g).max()))
        codes = rgb_codes(lin * (g / sc_))

        def at(d):
            b = rgb_blob(codes, d, "0")
            img = finish((rgb_decode(b, w, h, wb_applied=None) * (sc_ / g)).astype(np.float32))
            return b, score(img)
        lo, hi = 0.1, 25.0
        b, sc = at(lo)
        best = (lo, b, sc) if sc["ssimulacra2"] >= jrow["ssimulacra2"] else None
        if best is not None:
            for _ in range(12):
                mid = round((lo + hi) / 2, 3)
                b, sc = at(mid)
                if sc["ssimulacra2"] >= jrow["ssimulacra2"]:
                    lo, best = mid, (mid, b, sc)
                else:
                    hi = mid
                if hi - lo < 0.02:
                    break
        res = {"label": args.label, "source": source, "jpeg": jrow, "header_b_nominal": 60}
        if best is None:
            res["note"] = "B3a cannot reach the JPEG's SSIMULACRA2 even at d 0.1"
        else:
            d, b, sc = best
            n = len(b) + 60
            res.update({"distance": d, "bytes": n, "msgs": X.message_count(n, CHUNK), **sc,
                        "bytes_ratio_vs_jpeg": round(n / jrow["bytes"], 3),
                        "headroom_scale": round(sc_, 4)})
        with open(os.path.join(out, "equal_quality.json"), "w") as fh:
            json.dump(res, fh, indent=1, default=str)
        print(f"[EQ] {args.label}: JPEG {jrow['bytes']} B s2 {jrow['ssimulacra2']} -> B3a "
              f"d={res.get('distance')} {res.get('bytes')} B {res.get('msgs')} msgs s2 "
              f"{res.get('ssimulacra2')} ratio {res.get('bytes_ratio_vs_jpeg')}", flush=True)
        shutil.rmtree(tmp_png, ignore_errors=True)
        return 0

    rows = [jrow]
    imgs = {("jpeg", 1000): jimg}
    row_widths = {k: v for k, v in (item.split(":", 1) for item in args.row_widths.split(";")
                                    if item)}
    for row in args.rows.split(","):
        for w in [int(x) for x in row_widths.get(row, args.widths).split(",")]:
            w, h = size_for(w)
            if row == "bayer":
                mos = resample_mosaic(w, h)
                d, blob, ok = fit_d(lambda dd: bayer_blob(mos, min(dd, 15.0)), hi=15.0)
                if header_b is None:
                    header_b = len(blob) - sum(len(p) for p in X.unpack_container(blob)[1])
                img = bayer_decode(blob)
                extra = production_pick(mos)
            else:
                lin = rgb_lin(w, h)
                g = g3 if render_mode == "tg7" else dg * g3
                extra = {}
                if row == "rgbw":
                    # the clip count: pixels with ANY channel over 1 after WB (lost headroom)
                    extra["clip_pct"] = round(100.0 * float(np.mean((lin * g > 1.0).any(-1))), 4)
                    coded = wb(lin)
                elif row in ("rgbwi", "rgbwa"):
                    # B3 (Nick): WB as a CODING transform only, never clipped, undone exactly
                    # at decode -> camera-native linear RGB. rgbwi: / max gain (worst case, a
                    # constant); rgbwa: / max(1, this frame's WB peak) (data-adaptive). The
                    # gains + the scale go in the header (gains / CCM params already exist).
                    sc_ = float(g.max()) if row == "rgbwi" else max(1.0, float((lin * g).max()))
                    extra["headroom_scale"] = round(sc_, 4)
                    coded = lin * (g / sc_)
                else:
                    coded = lin
                codes = rgb_codes(coded)
                hb = header_b or 0
                mode = "1" if row == "rgb" else "0"
                d, payload, ok = fit_d(lambda dd: b"\0" * hb + rgb_blob(codes, dd, mode))
                blob = payload
                if row in ("rgbwi", "rgbwa"):
                    dec = rgb_decode(payload[hb:], w, h, wb_applied=None) * (sc_ / g)
                    img = finish(dec.astype(np.float32))      # camera-native linear -> render
                else:
                    img = rgb_decode(payload[hb:], w, h, wb_applied=row == "rgbw")
            disp = up(img)
            sc = score(disp)
            r = {"row": row, "width": w, "height": h, "distance": d, "quality": "",
                 "bytes": len(blob), "msgs": X.message_count(len(blob), CHUNK), "fits": ok, **sc,
                 **extra}
            r["pass"] = r["ssimulacra2"] >= jrow["ssimulacra2"] and \
                r["butteraugli_3norm"] <= jrow["butteraugli_3norm"]
            r["sig"] = r["ssimulacra2"] >= jrow["ssimulacra2"] + SIG_S2 and \
                r["butteraugli_3norm"] <= SIG_B3 * jrow["butteraugli_3norm"]
            r["d_s2"] = round(r["ssimulacra2"] - jrow["ssimulacra2"], 3)
            r["b3_ratio"] = round(r["butteraugli_3norm"] / jrow["butteraugli_3norm"], 3)
            rows.append(r)
            imgs[(row, w)] = disp
            print(f"[DS] {args.label} {row} {w}x{h}: d={d} {len(blob)} B {r['msgs']} msgs "
                  f"s2 {r['ssimulacra2']} ({r['d_s2']:+}) b3 {r['butteraugli_3norm']} "
                  f"(x{r['b3_ratio']}) {'SIG' if r['sig'] else 'PASS' if r['pass'] else 'FAIL'}"
                  + (f" | production d={extra.get('prod_distance')} {extra.get('prod_bytes')} B"
                     if "prod_distance" in extra else "")
                  + (f" | clip {extra['clip_pct']} %" if "clip_pct" in extra else "")
                  + (f" | headroom /{extra['headroom_scale']}" if "headroom_scale" in extra else ""),
                  flush=True)

    # ---- outputs ----------------------------------------------------------------------------
    old_slider = []
    if args.merge and os.path.isfile(os.path.join(out, "summary.json")):
        with open(os.path.join(out, "summary.json")) as fh:
            old = json.load(fh)
        redo = set(args.rows.split(","))
        kept = [r for r in old["rows"] if r["row"] not in redo and r["row"] != "jpeg"]
        rows = [jrow] + kept + rows[1:]
        old_slider = [x for x in old.get("slider", []) if x.get("row") not in redo]
    keys = ["row", "width", "height", "distance", "quality", "bytes", "msgs", "fits",
            "ssimulacra2", "butteraugli_3norm", "d_s2", "b3_ratio", "pass", "sig",
            "prod_distance", "prod_bytes", "prod_msgs", "prod_attempts", "prod_rfb", "clip_pct",
            "headroom_scale"]
    with open(os.path.join(out, "scores.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)

    gray = cv2.cvtColor(ref, cv2.COLOR_RGB2GRAY).astype(np.float32)
    win = 200
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    sob = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    box = lambda x: cv2.boxFilter(x, -1, (win, win))  # noqa: E731
    pick_win = lambda m: np.unravel_index(  # noqa: E731
        np.argmax(m[win // 2:-win // 2, win // 2:-win // 2]), (900 - win, 1600 - win))
    windows = {"fine texture": pick_win(box(lap)), "edges": pick_win(box(sob)),
               "dark area": pick_win(box(lap) / (box(gray) + 8.0) ** 2)}
    font = ImageFont.load_default()
    for row in args.rows.split(","):
        cols = [("reference (lossless neutral, native)", ref, "")]
        cols.append((f"today pjpg 1000x562 q{pick['quality']} (UPSAMPLED)", jimg,
                     f"{jrow['bytes']} B {jrow['msgs']} msgs s2 {jrow['ssimulacra2']} "
                     f"b3 {jrow['butteraugli_3norm']}"))
        for r in rows:
            if r["row"] == row:
                tag = "SIG" if r["sig"] else "PASS" if r["pass"] else "FAIL"
                cols.append((f"nrjxl {row} {r['width']}x{r['height']} d={r['distance']}"
                             + (" (UPSAMPLED)" if r["width"] != 1600 else ""),
                             imgs[(row, r["width"])],
                             f"{r['bytes']} B {r['msgs']} msgs s2 {r['ssimulacra2']} "
                             f"b3 {r['butteraugli_3norm']} {tag}"))
        tw = win * 2
        sheet = Image.new("RGB", (len(cols) * (tw + 8) + 8, 3 * (tw + 40) + 60), (255, 255, 255))
        dr = ImageDraw.Draw(sheet)
        dr.text((8, 6), f"Sprint28 density sweep | {args.label} | row {row} | ROI {ROI} native px | "
                f"cap {CAP} msgs, nrjxl byte target {target_bytes} B (fill {FILL}) | tiles = "
                f"200x200 native px shown 2x NEAREST; every non-native image UPSAMPLED (lanczos) "
                f"to 1600x900 first", fill=(0, 0, 0), font=font)
        dr.text((8, 22), f"vs the lossless neutral render: SSIMULACRA2 (higher better), "
                f"butteraugli 3-norm (lower better). PASS = both >= JPEG; SIG = s2 >= JPEG+{SIG_S2:g} "
                f"AND b3 <= {SIG_B3:g} x JPEG. {source.get('label') or 'Indoor study scene; daylight not covered.'}",
                fill=(0, 0, 0), font=font)
        for ri, (wname, (wy, wx)) in enumerate(windows.items()):
            for ci, (cname, img, info) in enumerate(cols):
                tile = Image.fromarray(img[wy:wy + win, wx:wx + win]).resize((tw, tw), Image.NEAREST)
                px, py = 8 + ci * (tw + 8), 44 + ri * (tw + 40)
                sheet.paste(tile, (px, py + 28))
                dr.text((px, py), f"{wname} @({wx},{wy})"[:66], fill=(0, 0, 0), font=font)
                dr.text((px, py + 13), (cname + " | " + info)[:66], fill=(60, 60, 60), font=font)
        sheet.save(os.path.join(out, f"cutsheet_{row}.png"))

    slider = []
    if not args.no_slider:
        sdir = os.path.join(out, "slider")
        os.makedirs(sdir, exist_ok=True)

        def put(name, img, r):
            Image.fromarray(img).save(os.path.join(sdir, name), quality=92)
            slider.append({"file": f"{args.label}/slider/{name}", **r})
        put("reference.jpg", ref, {"row": "reference", "width": 1600, "height": 900})
        slider.extend(x for x in old_slider if x.get("row") not in ("reference", "jpeg"))
        for r in rows:
            if (r["row"], r["width"]) not in imgs:
                continue                  # a merged row from an earlier run: files kept
            name = ("jpeg_1000.jpg" if r["row"] == "jpeg"
                    else f"nrjxl_{r['row']}_{r['width']}.jpg")
            put(name, imgs[(r["row"], r["width"])], {k: r.get(k) for k in keys if k in r})
    summary = {"label": args.label, "source": source, "roi": ROI, "cap_msgs": CAP, "target_bytes": target_bytes,
               "jpeg": jrow, "rows": rows, "slider": slider,
               "largest_sig_width": {row: max([r["width"] for r in rows
                                               if r["row"] == row and r["sig"]], default=None)
                                     for row in args.rows.split(",")},
               "largest_pass_width": {row: max([r["width"] for r in rows
                                                if r["row"] == row and r["pass"]], default=None)
                                      for row in args.rows.split(",")},
               "display_note": "every image is shown and scored at 1600x900; non-native ones are "
                               "UPSAMPLED (lanczos); slider files are JPEG q92 display renders",
               "windows_crop_px": {k: [int(v[1]), int(v[0]), win, win] for k, v in windows.items()}}
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    with open(os.path.join(out, "run_manifest.json"), "w") as fh:
        json.dump({"utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "inputs": source,
                   "command": " ".join(sys.argv),
                   "cjxl": subprocess.run(["cjxl", "--version"], capture_output=True,
                                          text=True).stdout.split("\n")[0]}, fh, indent=1)
    shutil.rmtree(tmp_png, ignore_errors=True)
    print(f"[DS] {args.label} largest SIG width: {summary['largest_sig_width']} | largest PASS "
          f"width: {summary['largest_pass_width']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
