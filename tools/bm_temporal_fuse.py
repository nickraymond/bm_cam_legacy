#!/usr/bin/env python3
# filename: bm_temporal_fuse.py
# description: Sprint21 — cross-cycle temporal fusion of fixed-mount reef frames, built as an
#              attributable ladder so every claimed gain can be traced to one added term.
"""
Cross-cycle temporal fusion for fixed-mount underwater reef cameras.

WHAT PROBLEM THIS SOLVES
A bmcam sits on one reef and photographs the same scene 4-5x/day. Each transmitted frame
is a partial delivery (bmcam001 delivered 41-93% of each image over 2026-09-01..05, never
100%), so every frame is soft below a row that moves cycle to cycle, and each carries its
own fish, particles, caustics and lighting. Fusing them should give a cleaner, more
complete picture of the reef than any single frame -- and a per-pixel map of what holds
still, which is the only place later change detection can be trusted.

WHY A LADDER, NOT A METHOD
Naive stacking makes this scene WORSE (measured: plain mean/median roughly halves edge
detail, because most of the high-frequency energy that differs between frames is caustics,
particles and swaying soft coral, not noise). So this tool does not offer one blessed
method. It runs a ladder where each rung adds exactly ONE term over the previous rung, and
reports the same metrics for all of them. A gain that cannot be attributed to a rung is
not a gain.

    L0  median                      the dumb control
    L1  mean                        does averaging help at all?
    L2  + registration              sub-pixel alignment (skipped below --min-shift-px)
    L3  + reef normalisation        per-frame per-channel affine fit to the reference,
                                    solved on STATIC REEF pixels only -- NOT on the
                                    reference card (measured: card-anchored gain leaves
                                    coral tiles as variable as no normalisation at all,
                                    because the card lies on bright near-field sand while
                                    the wall is shaded and further away)
    L4  + delivery weighting        frame weight from usable prefix; row weight from the
                                    modelled progressive cut row
    L5  + transient rejection       robust agreement weighting against the reference
    L6  + multi-scale detail        fuse COARSE bands by agreement (kills noise, haze and
                                    lighting drift) but take FINE detail from whichever
                                    frames actually resolved it. Frames differ in
                                    effective resolution because they differ in delivered
                                    prefix, so a single-scale average of a 93%-delivered
                                    frame with a 41%-delivered one throws away the detail
                                    the good frame paid for. (the proposed method)

METRICS (all reported per rung, in cut_sheets/ladder.jpg and metrics.csv)
  edge_energy    mean Sobel magnitude at strong STATIC edges  -> detail retained
  flat_noise     Laplacian std in flat STATIC regions         -> noise removed
  blockiness     8-px grid gradient ratio                     -> JPEG grid visibility
  split_half     RMS between composites of odd- vs even-indexed frames, on static
                 coral ROIs -> REPRODUCIBILITY, the gate metric. Single-frame pairs are
                 reported alongside as the baseline to beat.

INPUTS
  --run-dir DIR      an intake run folder from tools/bm_reef_frame_intake.py
                     (uses frames/ + frames_manifest.json, incl. delivery records)
  --output-dir DIR   where this fusion run writes (default: <run-dir>/fusion)
  --group SPEC       all | day | hour  (default all)
  --reference NAME   capture timestamp substring; default = best delivered prefix,
                     tie-broken by sharpness

OUTPUTS
  composites/<group>_<rung>.jpg    8-bit sRGB composite, card intact, ready for
                                   tools/bm_grvi_correct.py (render ONCE, at the end)
  maps/stability_<group>.png       per-pixel temporal std, the "what holds still" map
  maps/coverage_<group>.png        effective frames contributing per pixel
  metrics.csv                      one row per (group, rung)
  cut_sheets/ladder_<group>.jpg    the rungs side by side, labelled
  run_manifest.json

EXAMPLE
  python3 tools/bm_temporal_fuse.py \
      --run-dir runs/sprint21_day0_20260905 \
      --output-dir runs/sprint21_day1_fusion --group day

ASSUMPTIONS / KNOWN LIMITATIONS
  - Inputs are backend DISPLAY DERIVATIVES (re-encoded baseline JPEG). Fusion happens in
    linearised sRGB, which is an approximation: the frames are ISP output with auto
    AE/AWB, not a calibrated linear space. Good enough to make frames comparable; NOT
    good enough to call the result colorimetric.
  - The progressive cut row is MODELLED from the delivered prefix, not read from the
    original byte stream (the derivatives no longer carry it). The model assumes the
    final luma refinement scan occupies the last ~32% of the file. Labelled as modelled
    everywhere it is used.
  - Registration is translation-only. Rotation is reported, not corrected.
  - This tool does NOT colour-correct. Render the chosen composite once with
    tools/bm_grvi_correct.py afterwards.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

import cv2
import numpy as np

# Fraction of a progressive JPEG occupied by the final luma refinement scan. Measured on
# this scene with the Pi's exact encoder call (Pillow progressive+optimize). Used only to
# MODEL which rows of a partial frame kept full precision.
FINAL_SCAN_FRAC = 0.32


# ----------------------------------------------------------------- basics --
def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    y = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.clip(x, 0, None) ** (1 / 2.4) - 0.055)
    return np.clip(y * 255.0, 0, 255)


def gray_of(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(bgr, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)


def blockiness(bgr: np.ndarray) -> float:
    """Mean gradient at 8-px block boundaries / mean gradient elsewhere. 1.0 = invisible grid."""
    g = gray_of(bgr)
    dx, dy = np.abs(np.diff(g, axis=1)), np.abs(np.diff(g, axis=0))
    return float(((dx[:, 7::8].mean() / (dx.mean() + 1e-9))
                  + (dy[7::8, :].mean() / (dy.mean() + 1e-9))) / 2)


# ------------------------------------------------------------ frame model --
class Frame:
    def __init__(self, rec, run_dir):
        self.stamp = rec["capture_utc"]
        self.date, self.hour = rec["date"], rec["hour_utc"]
        self.bgr = cv2.imread(os.path.join(run_dir, rec["archived_as"]), cv2.IMREAD_COLOR)
        d = rec.get("delivery", {}) or {}
        self.prefix = (d.get("usable_prefix_pct") or 100.0) / 100.0
        self.planned = d.get("planned_chunks")
        self.sharp = rec.get("sharpness_lapvar", 0.0)
        self.shift = (0.0, 0.0)
        self.rotation_deg = 0.0

    def cut_row_frac(self) -> float:
        """Modelled: fraction of image height that kept the final refinement scan."""
        start = 1.0 - FINAL_SCAN_FRAC
        if self.prefix <= start:
            return 0.0
        return min(1.0, (self.prefix - start) / FINAL_SCAN_FRAC)


def register(frames, ref: Frame, min_shift: float):
    """Translation-only alignment to `ref`. Shifts below min_shift are NOT applied:
    a sub-pixel resample costs real edge energy and must be earned."""
    ref_g = gray_of(ref.bgr)
    for f in frames:
        (dx, dy), _ = cv2.phaseCorrelate(ref_g, gray_of(f.bgr))
        f.shift = (dx, dy)
        f.applied_shift = abs(complex(dx, dy)) >= min_shift
    return frames


def warped(f: Frame, apply: bool) -> np.ndarray:
    if not apply or not getattr(f, "applied_shift", False):
        return f.bgr.astype(np.float32)
    dx, dy = f.shift
    m = np.float32([[1, 0, -dx], [0, 1, -dy]])
    return cv2.warpAffine(f.bgr.astype(np.float32), m, (f.bgr.shape[1], f.bgr.shape[0]),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


# --------------------------------------------------------------- masking ---
def static_mask(stack_lin: np.ndarray, card_box=None) -> np.ndarray:
    """Pixels that hold still across the stack: the massive corals and rock.

    Excludes the top water band, the bottom sand band, and the reference card, all of
    which move (ripples, particles, and the card's own edges/text)."""
    y = stack_lin.mean(axis=-1)
    tstd = y.std(axis=0)
    h, w = tstd.shape
    ok = tstd < np.percentile(tstd, 40)
    ok[: int(0.06 * h), :] = False      # open water
    ok[int(0.90 * h):, :] = False       # sand
    if card_box:
        x0, y0, x1, y1 = card_box
        ok[y0:y1, x0:x1] = False
    return ok


def normalise_to_reference(stack_lin: np.ndarray, ref: np.ndarray, mask: np.ndarray):
    """Per-frame, per-channel AFFINE fit (gain + offset) onto the reference, solved on
    static reef pixels only.

    This is the step that makes frames comparable. It is deliberately anchored on the
    REEF, not on the reference card: the card sits on bright near-field sand in direct
    downwelling light while the coral wall is shaded and further away, and card-anchored
    gain was measured to leave coral variability unchanged (CV 0.30 vs 0.064 for a
    reef anchor)."""
    out = stack_lin.copy()
    coeffs = []
    for i in range(stack_lin.shape[0]):
        per_ch = []
        for c in range(3):
            x = stack_lin[i][..., c][mask]
            y = ref[..., c][mask]
            if x.size < 50 or float(x.std()) < 1e-6:
                per_ch.append((1.0, 0.0))
                continue
            a, b = np.polyfit(x, y, 1)
            a = float(np.clip(a, 0.2, 5.0))
            out[i][..., c] = np.clip(stack_lin[i][..., c] * a + b, 0, None)
            per_ch.append((a, float(b)))
        coeffs.append(per_ch)
    return out, coeffs


# ----------------------------------------------------------------- fusion --
def _bands(img, s1=1.2, s2=3.5):
    """Split into (fine detail, mid detail, base). Cheap 3-band Laplacian-style stack."""
    g1 = cv2.GaussianBlur(img, (0, 0), s1)
    g2 = cv2.GaussianBlur(g1, (0, 0), s2)
    return img - g1, g1 - g2, g2


def fuse(stack_lin, frames, ref_i, *, rung, mask, ref_lin=None, detail_exp=0.5):
    """Return (composite_linear, effective_frame_count_map). One term per rung."""
    n, h, w, _ = stack_lin.shape
    if rung == "L0_median":
        return np.median(stack_lin, axis=0), np.full((h, w), float(n))
    if rung in ("L1_mean", "L2_register", "L3_normalise"):
        return stack_lin.mean(axis=0), np.full((h, w), float(n))

    # L4: delivery weighting -- whole-frame confidence x modelled row confidence
    wts = np.zeros((n, h, w), np.float32)
    for i, f in enumerate(frames):
        row_w = np.full(h, 0.45, np.float32)          # rows past the modelled cut
        keep = int(round(f.cut_row_frac() * h))
        row_w[:keep] = 1.0
        wts[i] = (f.prefix ** 2) * row_w[:, None]
    if rung == "L4_delivery":
        wsum = wts.sum(0)[..., None] + 1e-9
        return (stack_lin * wts[..., None]).sum(0) / wsum, wts.sum(0)

    # L5: + transient rejection. Down-weight pixels that disagree with the reference by
    # more than the local temporal spread -- fish, particles, swaying gorgonians.
    ref = stack_lin[ref_i] if ref_lin is None else ref_lin
    sigma = np.maximum(stack_lin.std(axis=0).mean(-1), 0.010)[None, ..., None]
    agree = np.exp(-0.5 * ((stack_lin - ref[None]) / sigma) ** 2).mean(-1)
    if ref_i is not None and ref_lin is None:
        agree[ref_i] = 1.0
    wts = wts * agree.astype(np.float32)
    wsum = wts.sum(0)[..., None] + 1e-9
    if rung == "L5_transient":
        return (stack_lin * wts[..., None]).sum(0) / wsum, wts.sum(0)

    # L6: + multi-scale. Coarse bands average (that is where noise, haze and lighting
    # drift live); fine detail is taken from the frames that actually resolved it,
    # weighted by local band energy. A frame whose refinement scan never arrived has
    # little fine energy there and contributes little, instead of blurring the frames
    # that do.
    fine, mid, base = [], [], []
    for i in range(n):
        f, m, b = _bands(stack_lin[i])
        fine.append(f); mid.append(m); base.append(b)
    fine, mid, base = np.stack(fine), np.stack(mid), np.stack(base)

    def detail_weight(bands, sharpen):
        # local band energy per frame -- how much detail this frame actually carries here
        e = np.stack([cv2.GaussianBlur(b, (0, 0), 2.0)
                      for b in (bands ** 2).mean(-1).astype(np.float32)])
        return (wts * (e + 1e-7) ** sharpen).astype(np.float32)

    out = np.zeros_like(stack_lin[0])
    for bands, sharpen in ((base, 0.0), (mid, detail_exp * 0.5), (fine, detail_exp)):
        w = wts if sharpen == 0.0 else detail_weight(bands, sharpen)
        out += (bands * w[..., None]).sum(0) / (w.sum(0)[..., None] + 1e-9)
    return out, wts.sum(0)


RUNGS = ["L0_median", "L1_mean", "L2_register", "L3_normalise", "L4_delivery",
         "L5_transient", "L6_multiscale"]


def build(frames, ref_frame, rung, card_box, detail_exp=0.5):
    """Assemble the stack for one rung and fuse it.

    `ref_frame` is the photometric and geometric anchor and NEED NOT be a member of
    `frames`. That matters for the split-half gate: both halves must be normalised onto
    the SAME anchor, or the two composites land on different photometry and the metric
    measures the anchor difference instead of reproducibility.
    """
    use_reg = RUNGS.index(rung) >= RUNGS.index("L2_register")
    stack = np.stack([srgb_to_linear(warped(f, use_reg)) for f in frames])
    ref_lin = srgb_to_linear(warped(ref_frame, use_reg))
    mask = static_mask(np.concatenate([stack, ref_lin[None]]), card_box)
    ref_i = frames.index(ref_frame) if ref_frame in frames else None
    if RUNGS.index(rung) >= RUNGS.index("L3_normalise"):
        stack, _ = normalise_to_reference(stack, ref_lin, mask)
    comp, cov = fuse(stack, frames, ref_i, rung=rung, mask=mask, ref_lin=ref_lin,
                     detail_exp=detail_exp)
    return linear_to_srgb(comp).astype(np.uint8), cov, mask, stack


# ---------------------------------------------------------------- metrics --
def grad_mag(bgr) -> np.ndarray:
    g = gray_of(bgr)
    return np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))


def selection_map(frames) -> np.ndarray:
    """Where the SCENE has structure, decided independently of any one frame.

    Choosing edge locations from the reference frame biases every metric in that frame's
    favour -- nothing but the reference can score well on its own noise realisation, and
    a perfectly good second frame lands ~0.7x. Taking the median gradient across all
    frames makes the yardstick independent of both the reference and the composite."""
    return np.median(np.stack([grad_mag(f.bgr) for f in frames]), axis=0)


def quality_metrics(bgr, sel_map, mask):
    """Detail and noise on STATIC pixels, at locations fixed by `sel_map`."""
    edges = mask & (sel_map > np.percentile(sel_map[mask], 90))
    flat = mask & (sel_map < np.percentile(sel_map[mask], 30))
    g = gray_of(bgr)
    gm = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return (float(gm[edges].mean()), float(cv2.Laplacian(g, cv2.CV_32F)[flat].std()),
            blockiness(bgr))


def split_half(frames, ref_frame, rung, card_box, mask, detail_exp=0.5):
    """RMS difference between composites built from odd- vs even-indexed frames.

    This is the GATE. A fusion that is merely smoother scores well on noise metrics but
    reproduces badly; a fusion that genuinely recovers the reef gives the same answer
    from either half of the data. Both halves are anchored on the SAME reference frame,
    so this measures reproducibility and not a difference of anchors.

    Reported against the RMS between two single frames -- the number to beat."""
    odd = [f for i, f in enumerate(frames) if i % 2 == 1]
    even = [f for i, f in enumerate(frames) if i % 2 == 0]
    if len(odd) < 2 or len(even) < 2:
        return None, None
    a, _, _, _ = build(odd, ref_frame, rung, card_box, detail_exp)
    b, _, _, _ = build(even, ref_frame, rung, card_box, detail_exp)
    d = gray_of(a) - gray_of(b)
    comp_rms = float(np.sqrt((d[mask] ** 2).mean()))
    pairs = [float(np.sqrt(((gray_of(o.bgr) - gray_of(e.bgr))[mask] ** 2).mean()))
             for o, e in zip(odd, even)]
    return comp_rms, float(np.median(pairs))


# ------------------------------------------------------------------ sheet --
def ladder_sheet(path, title, panels, crop):
    """Rungs side by side over a fixed crop, each labelled with its metrics."""
    y0, y1, x0, x1 = crop
    tiles = []
    for name, img, txt in panels:
        t = np.ascontiguousarray(img[y0:y1, x0:x1]).copy()
        t = cv2.copyMakeBorder(t, 30, 46, 6, 6, cv2.BORDER_CONSTANT, value=(246, 246, 246))
        cv2.putText(t, name, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 30), 1, cv2.LINE_AA)
        for k, line in enumerate(txt):
            cv2.putText(t, line, (10, t.shape[0] - 30 + k * 15), cv2.FONT_HERSHEY_SIMPLEX,
                        0.40, (60, 60, 60), 1, cv2.LINE_AA)
        tiles.append(t)
    body = np.concatenate(tiles, axis=1)
    head = np.full((40, body.shape[1], 3), 246, np.uint8)
    cv2.putText(head, title, (12, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.imwrite(path, np.concatenate([head, body], axis=0), [int(cv2.IMWRITE_JPEG_QUALITY), 92])


# ------------------------------------------------------------------- main --
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--output-dir", default="")
    ap.add_argument("--group", choices=["all", "day", "hour"], default="all")
    ap.add_argument("--reference", default="")
    ap.add_argument("--min-shift-px", type=float, default=0.5)
    ap.add_argument("--card-box", default="440,370,860,540",
                    help="x0,y0,x1,y1 of the reference card, excluded from reef anchors")
    ap.add_argument("--hero-crop", default="60,330,60,460", help="y0,y1,x0,x1 for the cut sheet")
    ap.add_argument("--detail-exp", type=float, default=0.25,
                    help="L6 fine-band detail weighting exponent (0 = plain average)")
    args = ap.parse_args(argv)

    out = args.output_dir or os.path.join(args.run_dir, "fusion")
    for sub in ("composites", "maps", "cut_sheets"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    card_box = tuple(int(v) for v in args.card_box.split(","))
    crop = tuple(int(v) for v in args.hero_crop.split(","))

    recs = json.load(open(os.path.join(args.run_dir, "frames_manifest.json")))
    frames = [Frame(r, args.run_dir) for r in recs]
    frames = [f for f in frames if f.bgr is not None]
    frames.sort(key=lambda f: f.stamp)
    print(f"[fuse] {len(frames)} frames from {args.run_dir}")

    groups = {"all": frames}
    if args.group == "day":
        g = defaultdict(list)
        for f in frames:
            g[f.date].append(f)
        groups = {k: v for k, v in sorted(g.items()) if len(v) >= 3}
    elif args.group == "hour":
        g = defaultdict(list)
        for f in frames:
            g[f"{f.hour}Z"].append(f)
        groups = {k: v for k, v in sorted(g.items()) if len(v) >= 3}

    rows = []
    for gname, gframes in groups.items():
        if args.reference:
            ref = next((f for f in gframes if args.reference in f.stamp), None)
        else:
            ref = None
        if ref is None:
            ref = max(gframes, key=lambda f: (round(f.prefix, 2), f.sharp))
        register(gframes, ref, args.min_shift_px)
        n_shift = sum(1 for f in gframes if getattr(f, "applied_shift", False))
        print(f"\n[{gname}] n={len(gframes)} reference={ref.stamp} "
              f"(prefix {ref.prefix*100:.0f}%, lapvar {ref.sharp:.0f}); "
              f"registration applied to {n_shift}/{len(gframes)}")

        sel = selection_map(gframes)
        panels, mask_ref = [], None
        for rung in RUNGS:
            t0 = datetime.now(timezone.utc)
            comp, cov, mask, _ = build(gframes, ref, rung, card_box, args.detail_exp)
            if mask_ref is None:
                mask_ref = mask
            edge, flat, blk = quality_metrics(comp, sel, mask_ref)
            sh_c, sh_s = split_half(gframes, ref, rung, card_box, mask_ref, args.detail_exp)
            secs = (datetime.now(timezone.utc) - t0).total_seconds()
            cv2.imwrite(os.path.join(out, "composites", f"{gname}_{rung}.jpg"), comp,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 94])
            rows.append({"group": gname, "n_frames": len(gframes), "rung": rung,
                         "edge_energy": round(edge, 2), "flat_noise": round(flat, 3),
                         "blockiness": round(blk, 3),
                         "split_half_rms": round(sh_c, 3) if sh_c else "",
                         "single_pair_rms": round(sh_s, 3) if sh_s else "",
                         "eff_frames_median": round(float(np.median(cov)), 2),
                         "seconds": round(secs, 2)})
            panels.append((rung, comp, [f"edge {edge:.1f}  noise {flat:.2f}",
                                        f"block {blk:.2f}  split {sh_c:.2f}" if sh_c else
                                        f"block {blk:.2f}"]))
            print(f"   {rung:14} edge {edge:6.2f}  noise {flat:5.2f}  block {blk:.3f}  "
                  f"split-half {sh_c if sh_c is None else round(sh_c,2)}  ({secs:.1f}s)")

        singles = [quality_metrics(f.bgr, sel, mask_ref) for f in gframes]
        se = [x[0] for x in singles]
        print(f"   {'single frames':14} edge median {np.median(se):6.2f}  "
              f"best {max(se):6.2f}  worst {min(se):6.2f}   <- the honest yardstick")
        for (e_, f2, b2), fr in sorted(zip(singles, gframes), key=lambda t: -t[0][0])[:1]:
            rows.append({"group": gname, "n_frames": 1, "rung": "best_single_frame",
                         "edge_energy": round(e_, 2), "flat_noise": round(f2, 3),
                         "blockiness": round(b2, 3), "split_half_rms": "",
                         "single_pair_rms": round(sh_s, 3) if sh_s else "",
                         "eff_frames_median": 1, "seconds": 0})
        rows.append({"group": gname, "n_frames": 1, "rung": "median_single_frame",
                     "edge_energy": round(float(np.median(se)), 2),
                     "flat_noise": round(float(np.median([x[1] for x in singles])), 3),
                     "blockiness": round(float(np.median([x[2] for x in singles])), 3),
                     "split_half_rms": "", "single_pair_rms": round(sh_s, 3) if sh_s else "",
                     "eff_frames_median": 1, "seconds": 0})
        e, f_, b = quality_metrics(ref.bgr, sel, mask_ref)
        # split-half needs >=2 frames per half, i.e. n>=4; smaller groups report no gate
        pair_txt = f"block {b:.2f}  single-pair {sh_s:.2f}" if sh_s else f"block {b:.2f}"
        panels.insert(0, ("reference frame", ref.bgr,
                          [f"edge {e:.1f}  noise {f_:.2f}", pair_txt]))
        rows.append({"group": gname, "n_frames": 1, "rung": "reference_single_frame",
                     "edge_energy": round(e, 2), "flat_noise": round(f_, 3),
                     "blockiness": round(b, 3), "split_half_rms": "",
                     "single_pair_rms": round(sh_s, 3) if sh_s else "",
                     "eff_frames_median": 1, "seconds": 0})
        print(f"   {'reference':14} edge {e:6.2f}  noise {f_:5.2f}  block {b:.3f}"
              + (f"  (two single frames differ by {sh_s:.2f})" if sh_s else
                 "  (group too small for the split-half gate)"))

        ladder_sheet(os.path.join(out, "cut_sheets", f"ladder_{gname}.jpg"),
                     f"Sprint21 fusion ladder - {gname} (n={len(gframes)})", panels, crop)

        stack = np.stack([srgb_to_linear(warped(f, True)) for f in gframes])
        tstd = stack.mean(-1).std(0)
        cv2.imwrite(os.path.join(out, "maps", f"stability_{gname}.png"),
                    cv2.applyColorMap(np.clip(tstd / (np.percentile(tstd, 99) + 1e-9) * 255,
                                              0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO))
        _, cov, _, _ = build(gframes, ref, "L6_multiscale", card_box, args.detail_exp)
        cv2.imwrite(os.path.join(out, "maps", f"coverage_{gname}.png"),
                    cv2.applyColorMap(np.clip(cov / (cov.max() + 1e-9) * 255, 0, 255).astype(np.uint8),
                                      cv2.COLORMAP_VIRIDIS))

    with open(os.path.join(out, "metrics.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                         stderr=subprocess.DEVNULL).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "unknown"
    json.dump({"run_tag": f"fuse_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
               "git_commit": commit, "args": vars(args), "groups": list(groups),
               "rungs": RUNGS, "final_scan_frac_modelled": FINAL_SCAN_FRAC},
              open(os.path.join(out, "run_manifest.json"), "w"), indent=2)
    print(f"\n[fuse] wrote {out}/metrics.csv, composites/, maps/, cut_sheets/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
