#!/usr/bin/env python3
# filename: bm_reef_change_sensitivity.py
# description: Sprint21 day-4 — how big a coral change would this camera actually detect?
"""
Change-detection sensitivity for fixed-mount reef cameras.

WHAT THIS ANSWERS
Not "can we detect bleaching" -- that needs ground truth nobody has yet. It answers the
narrower, honest question: given the noise this camera and link actually produce, how
large a change on a colony of a given size, over a baseline of a given length, would rise
above that noise?

THE INDEX
Bleaching lifts BLUE OVER GREEN on a colony: symbiont pigment absorbs blue, so losing it
raises B/G. Lightness rises too, but turbidity, sediment, sun angle and a fouled port all
raise lightness as well, so lightness is reported and never leads. Red is unusable at
current camera settings (it is Cr noise), which is what Sprint 20 exists to fix.

The index is DIFFERENTIAL: a colony's log(B/G) minus the log(B/G) of the scene's stable
substrate in the same composite. A whole-scene shift (a hazier day, a different sun angle)
moves both and cancels; a change on the colony alone does not.

THE THREE PARTS
  A NULL        Measure the index on the real daily composites across days with no known
                change. Its spread is the noise floor -- everything else is scaled by it.
  B TRANSFER    Inject a KNOWN change into a colony before compression, push it through the
                whole simulated chain (per-cycle lighting drift, particles, q70 progressive
                encode, truncation at the real delivered fractions, backend re-encode,
                fusion, Nereus v3), and measure how much survives. Anything the pipeline
                attenuates raises the change a real event has to make.
  C SENSITIVITY Combine: minimum detectable change = k * sigma_null / transfer, reported
                against colony size and baseline length.

PRE-REGISTERED before looking at any result: k = 3, and a change must persist across at
least 2 consecutive composites to count. Those are fixed here so they cannot be tuned to
flatter the answer.

INPUTS
  --composites SPEC   day=path pairs, the daily fused+corrected composites (arm A2)
  --colonies SPEC     semicolon-separated x,y centre boxes (any size; --window-sizes rules)
  --window-sizes      analysis window edges in px (default 24,40,56,72,96)
  --truth-run DIR     a day-2 style ground-truth run, for part B
  --inject            relative B/G changes to inject (default 0.05,0.10,0.20,0.40)
  --output-dir DIR

OUTPUTS
  null_index.csv, transfer.csv, sensitivity.csv, cut_sheets/, run_manifest.json

ASSUMPTIONS / KNOWN LIMITATIONS
  - THREE days of baseline. A standard deviation from n=3 carries roughly 50% error, and
    the five frames within a day are not independent of each other -- the day is the unit.
    Every number here is a PILOT ESTIMATE and is reported with that caveat attached.
  - The null is UNVERIFIED. Early September is peak thermal stress in the Florida Keys, so
    "three quiet days" is an assumption, not a fact. Ask AOML.
  - Colony boxes are stable textured patches on the coral wall, not segmented colonies.
  - The injection is a spectral MODEL of bleaching (B/G lift plus a lightness lift), not a
    measured bleached-coral spectrum. Calibrate it against real bleached pixels before any
    number here is quoted outside engineering.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_temporal_fuse as tf                        # noqa: E402
import bm_temporal_fuse_validate as val              # noqa: E402
import bm_grvi_correct as grvi                       # noqa: E402
import reference_card_color_utils as ccu             # noqa: E402

K_SIGMA = 3.0            # pre-registered
PERSIST = 2              # pre-registered: consecutive composites required
CARD_BOX = (440, 370, 860, 540)


def fit_core(bgr, layout, targets_json, model=None):
    """Fit the production GRVI model and return ONLY its physical core applied, in linear.

    apply_core is veil removal, per-channel amp, the chroma matrix and the exposure
    anchor -- the stages that undo the water. It deliberately stops before apply_render,
    the water prior, coral_tan and the warm grade, which exist to make the picture look
    right for a person and which measurably destroy real chroma change.

    Pass `model` to REUSE a model fitted on another composite. That is the point of
    core_shared: a correction that is constant across composites still cancels in the
    differential index, whereas re-fitting per composite injects the fit's own day-to-day
    variation into the measurement.

    Returns (core_linear_bgr, model), or (None, None) when no card is found."""
    img_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    lin = ccu.srgb_to_linear(img_rgb.astype(np.float64) / 255.0)
    if model is not None:
        return model.apply_core(lin)[..., ::-1], model
    samples, _ = grvi.detect_and_sample(img_rgb, layout)
    if not samples:
        return None, None
    override = None
    cal = {}
    if targets_json and os.path.exists(targets_json):
        cal = json.loads(open(targets_json).read())
        override = {pid: np.asarray(v, dtype=float)
                    for pid, v in cal.get("targets_linear", {}).items()}
    model = ccu.solve_grvi(samples, lin, target_override=override)
    clar = cal.get("clarity") or {}
    if clar.get("veil_scale"):
        model.veil = model.veil * clar["veil_scale"]     # match the production pick
    core = model.apply_core(lin)                          # RGB, linear
    return core[..., ::-1], model                         # -> BGR to match everything else


def bg_index_linear(lin_bgr, box, ref_mask):
    """The same differential index, on an array that is ALREADY linear."""
    x0, y0, x1, y1 = box
    p = lin_bgr[y0:y1, x0:x1].reshape(-1, 3)
    cb, cg = np.median(p[:, 0]), np.median(p[:, 1])
    r = lin_bgr[ref_mask]
    rb, rg = np.median(r[:, 0]), np.median(r[:, 1])
    return (float(np.log(max(cb, 1e-9) / max(cg, 1e-9)) - np.log(max(rb, 1e-9) / max(rg, 1e-9))),
            float(np.median(p.mean(-1))))


def substrate_mask(bgr):
    """Stable scene substrate used as the differential reference."""
    g = tf.gray_of(bgr)
    h, w = g.shape
    m = np.zeros((h, w), bool)
    m[int(0.08 * h):int(0.72 * h), 20:w - 20] = True
    m[CARD_BOX[1]:CARD_BOX[3], CARD_BOX[0]:CARD_BOX[2]] = False
    return m


def bg_index(bgr, box, ref_mask):
    """log(colony B/G) - log(substrate B/G), plus the raw lightness, in linear space."""
    lin = tf.srgb_to_linear(bgr.astype(np.float32))
    x0, y0, x1, y1 = box
    p = lin[y0:y1, x0:x1].reshape(-1, 3)
    cb, cg = np.median(p[:, 0]), np.median(p[:, 1])
    r = lin[ref_mask]
    rb, rg = np.median(r[:, 0]), np.median(r[:, 1])
    return (float(np.log(max(cb, 1e-6) / max(cg, 1e-6)) - np.log(max(rb, 1e-6) / max(rg, 1e-6))),
            float(np.median(p.mean(-1))))


def boxes_at(centres, size, shape):
    """Re-cut each colony centre at a given analysis window size, clipped to the frame."""
    h, w = shape[:2]
    out = []
    for (cx, cy) in centres:
        x0, y0 = int(cx - size // 2), int(cy - size // 2)
        x0 = max(0, min(w - size, x0)); y0 = max(0, min(h - size, y0))
        out.append((x0, y0, x0 + size, y0 + size))
    return out


def inject(bgr, box, d, feather=9):
    """Apply a modelled bleaching change of relative magnitude `d` inside `box`.

    B/G is multiplied by (1+d) and overall lightness lifted by d/2, feathered at the
    boundary so the edge itself is not the thing being detected."""
    lin = tf.srgb_to_linear(bgr.astype(np.float32))
    m = np.zeros(lin.shape[:2], np.float32)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = 1.0
    m = cv2.GaussianBlur(m, (0, 0), feather)[..., None]
    s = np.sqrt(1.0 + d)
    gain = np.array([s, 1.0 / s, 1.0], np.float32) * (1.0 + 0.5 * d)
    out = lin * (1 - m) + lin * gain[None, None, :] * m
    return np.clip(tf.linear_to_srgb(out), 0, 255).astype(np.uint8)


def build_composite(frames_bgr, prefixes, quality, rng, out_dir, tag, targets,
                    layer="raw"):
    """One day of cycles -> fused (raw, arm A2) -> Nereus v3 once. Returns the composite."""
    os.makedirs(out_dir, exist_ok=True)
    served = []
    for i, (src, pfx) in enumerate(zip(frames_bgr, prefixes)):
        img, _ = val.transmit(src, quality, pfx)
        served.append(img)
    stack = np.stack([tf.srgb_to_linear(s.astype(np.float32)) for s in served])
    ref_lin = stack[int(np.argmax(prefixes))]
    mask = tf.static_mask(stack, CARD_BOX)

    class _F:                                        # minimal frame view for fuse()
        pass
    fs = []
    for s, pfx in zip(served, prefixes):
        f = _F(); f.prefix = pfx; f.cut_row_frac = (lambda p=pfx: max(
            0.0, min(1.0, (p - (1 - val.__dict__.get("FINAL", 0.32))) / 0.32)))
        fs.append(f)
    comp, _ = tf.fuse(stack, fs, int(np.argmax(prefixes)), rung="L6_multiscale",
                      mask=mask, ref_lin=ref_lin, detail_exp=0.25)
    raw = tf.linear_to_srgb(comp).astype(np.uint8)
    p = os.path.join(out_dir, f"raw_{tag}.jpg")
    cv2.imwrite(p, raw, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "bm_grvi_correct.py"),
           "--images", p, "--output-dir", os.path.join(out_dir, "grvi")]
    if targets:
        cmd += ["--render-targets", targets]
    r = subprocess.run(cmd, capture_output=True, text=True)
    got = os.path.join(out_dir, "grvi", f"raw_{tag}", "after_grvi.jpg")
    if layer == "raw":
        return raw
    if layer.startswith("core"):
        return raw          # the caller applies the core itself, on the RAW composite
    if r.returncode == 0 and os.path.exists(got):
        return cv2.imread(got, cv2.IMREAD_COLOR)
    print(f"   [warn] grvi failed for {tag}; scoring the uncorrected composite")
    return raw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--composites", nargs="+", required=True, help="day=path")
    ap.add_argument("--colonies", required=True)
    ap.add_argument("--window-sizes", default="24,40,56,72,96")
    ap.add_argument("--truth-run", default="runs/sprint21_day2_truth_20260905")
    ap.add_argument("--inject", default="0.05,0.10,0.20,0.40")
    ap.add_argument("--prefixes", default="0.742,0.766,0.882,0.910,0.911")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--render-targets",
                    default="tools/reference_card_color_correction/render_targets_p9.json")
    ap.add_argument("--measure-layer", choices=["raw", "core", "core_shared", "rendered"], default="raw",
                    help="raw = the fused composite before any colour work. core = v3's "
                         "physical stages only (veil removal, gain, chroma matrix, "
                         "exposure), re-fitted on EVERY composite. core_shared = the "
                         "same stages from ONE model fitted once per epoch and reused, so "
                         "the correction is constant and still cancels in the differential "
                         "index. rendered = the full v3 picture, whose tone curve and "
                         "saturation stage compress chroma and attenuate real change.")
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args(argv)
    out = args.output_dir
    os.makedirs(os.path.join(out, "cut_sheets"), exist_ok=True)

    layout = ccu.load_template(Path(args.render_targets).parent
                               / "reference_card_template_v2" / "template_layout.json")
    _core_cache = {}

    _shared = {"model": None}

    def measure(bgr, box, ref_mask):
        """Compute the index on whichever layer was selected."""
        if args.measure_layer in ("raw", "rendered"):
            return bg_index(bgr, box, ref_mask)
        # id() is NOT a safe cache key: a freed array's address gets reused, so a later
        # composite can collide with an earlier one's fit. Key on content.
        key = hash(bgr.tobytes())
        if key not in _core_cache:
            shared = args.measure_layer == "core_shared"
            core, model = fit_core(bgr, layout, args.render_targets,
                                   model=_shared["model"] if shared else None)
            if shared and _shared["model"] is None:
                _shared["model"] = model      # first composite defines the epoch's model
            _core_cache[key] = core
        core = _core_cache[key]
        if core is None:
            print("   [warn] no card on this composite; falling back to the raw layer")
            return bg_index(bgr, box, ref_mask)
        return bg_index_linear(core, box, ref_mask)

    def reset_shared_model():
        """The transfer experiment is a different scene, so it needs its own epoch model."""
        _shared["model"] = None
        _core_cache.clear()

    comps = {}
    for spec in args.composites:
        d, p = spec.split("=", 1)
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            print(f"[ERROR] cannot read {p}", file=sys.stderr); return 1
        comps[d] = img
    sizes = [int(v) for v in args.window_sizes.split(",")]
    centres = []
    for b in args.colonies.split(";"):
        x0, y0, x1, y1 = (int(v) for v in b.split(","))
        centres.append(((x0 + x1) // 2, (y0 + y1) // 2))
    print(f"[sens] {len(comps)} daily composites, {len(centres)} colonies, "
          f"windows {sizes}")

    # ---------------- A. NULL -------------------------------------------------
    null_rows = []
    ref_mask = substrate_mask(next(iter(comps.values())))
    sigma_by_size = {}
    for size in sizes:
        boxes = boxes_at(centres, size, next(iter(comps.values())).shape)
        sig = []
        for ci, box in enumerate(boxes):
            vals = [measure(comps[d], box, ref_mask)[0] for d in sorted(comps)]
            lums = [measure(comps[d], box, ref_mask)[1] for d in sorted(comps)]
            s = float(np.std(vals, ddof=1))
            sig.append(s)
            null_rows.append({"window_px": size, "colony": f"C{ci+1}",
                              "area_px": size * size,
                              "index_by_day": " ".join(f"{v:+.4f}" for v in vals),
                              "sigma_log_bg": round(s, 5),
                              "sigma_pct_bg": round((np.exp(s) - 1) * 100, 2),
                              "lum_cv_pct": round(float(np.std(lums, ddof=1) /
                                                        max(np.mean(lums), 1e-9) * 100), 2)})
        sigma_by_size[size] = float(np.median(sig))
        print(f"   window {size:3d}px  median sigma {np.median(sig):.4f} log-units "
              f"= {(np.exp(np.median(sig))-1)*100:5.2f}% in B/G")

    # ---------------- B. TRANSFER --------------------------------------------
    truth = cv2.imread(os.path.join(args.truth_run, "truth", "ground_truth.png"),
                       cv2.IMREAD_COLOR)
    if truth is None:
        print(f"[ERROR] no ground truth in {args.truth_run}", file=sys.stderr); return 1
    prefixes = [float(v) for v in args.prefixes.split(",")]
    inject_levels = [float(v) for v in args.inject.split(",")]
    t_centre = centres[0]
    t_box = boxes_at([t_centre], 56, truth.shape)[0]
    t_mask = substrate_mask(truth)
    trans_rows = []
    reset_shared_model()
    print(f"[sens] transfer: injecting into {t_box} through the full chain")
    base_comp = None
    SEEDS = [args.seed, args.seed + 101, args.seed + 202]
    base_by_seed = {}
    for d in [0.0] + inject_levels:
        recs, lums = [], []
        for sd in SEEDS:
            rng = np.random.default_rng(sd)          # paired: same degradation per seed
            frames = [val.degrade(inject(truth, t_box, d) if d > 0 else truth, rng)
                      for _ in prefixes]
            comp = build_composite(frames, prefixes, args.quality, rng,
                                   os.path.join(out, "transfer", f"d{int(d*100):03d}_s{sd}"),
                                   f"d{int(d*100):03d}_s{sd}", args.render_targets,
                                   layer=args.measure_layer)
            idx, lum = measure(comp, t_box, t_mask)
            if d == 0.0:
                base_by_seed[sd] = (idx, lum)
            else:
                recs.append(float(np.exp(idx - base_by_seed[sd][0]) - 1))
                lums.append(lum / base_by_seed[sd][1] - 1)
        if d == 0.0:
            print(f"   inject {d*100:5.1f}%  control over {len(SEEDS)} seeds")
            continue
        rec = float(np.mean(recs))
        trans_rows.append({"injected_pct": round(d * 100, 1),
                           "recovered_pct": round(rec * 100, 2),
                           "recovered_sd_pct": round(float(np.std(recs, ddof=1)) * 100, 2),
                           "transfer": round(rec / d, 3),
                           "lum_change_pct": round(float(np.mean(lums)) * 100, 2)})
        print(f"   inject {d*100:5.1f}%  recovered {rec*100:5.2f}% "
              f"(sd {np.std(recs, ddof=1)*100:.2f})  transfer {rec/d:.2f}")
    transfer = float(np.median([r["transfer"] for r in trans_rows])) if trans_rows else 1.0

    # ---------------- C. SENSITIVITY -----------------------------------------
    sens_rows = []
    print(f"\n[sens] minimum detectable B/G change, k={K_SIGMA:.0f}, "
          f"persistence {PERSIST} composites, transfer {transfer:.2f}")
    print(f"   {'window':>8}{'area px':>9}" + "".join(f"{f'n={n}d':>9}" for n in (3, 7, 14, 28)))
    for size in sizes:
        s = sigma_by_size[size]
        line = f"   {size:>6}px{size*size:>9}"
        for n in (3, 7, 14, 28):
            mdc_log = K_SIGMA * s * np.sqrt(1.0 + 1.0 / n) / max(transfer, 1e-6)
            mdc = (np.exp(mdc_log) - 1) * 100
            sens_rows.append({"window_px": size, "area_px": size * size, "baseline_days": n,
                              "mdc_bg_pct": round(float(mdc), 2)})
            line += f"{mdc:>8.1f}%"
        print(line)

    for name, rows in (("null_index.csv", null_rows), ("transfer.csv", trans_rows),
                       ("sensitivity.csv", sens_rows)):
        if not rows:
            continue
        with open(os.path.join(out, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    json.dump({"run_tag": f"sens_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
               "args": vars(args), "k_sigma": K_SIGMA, "persistence": PERSIST,
               "transfer_median": round(transfer, 3),
               "sigma_by_window": {k: round(v, 5) for k, v in sigma_by_size.items()},
               "baseline_days": len(comps),
               "caveat": "n=3 days; sigma carries ~50% error; the null is UNVERIFIED"},
              open(os.path.join(out, "run_manifest.json"), "w"), indent=2)
    print(f"\n[sens] wrote {out}/null_index.csv, transfer.csv, sensitivity.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
