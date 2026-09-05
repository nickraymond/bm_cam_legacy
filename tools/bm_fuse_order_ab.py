#!/usr/bin/env python3
# filename: bm_fuse_order_ab.py
# description: Sprint21 day-3 — settle ask B: colour-correct before fusing, after fusing,
#              or normalise first, and anchored on the card or on the reef?
"""
Ordering experiment for temporal fusion + Nereus v3 colour correction.

THE QUESTION
Nick asked whether v3 should run per frame and then stack, or stack and then correct once.
Neither is quite the real choice, because the interesting variable is not WHEN colour runs
but WHAT the per-frame photometric anchor is. Four arms, identical frames, identical
fusion machinery, identical weights:

  A1 correct-then-fuse    v3 on every frame, then fuse the rendered images
  A2 fuse-then-correct    fuse the raw frames untouched, then v3 once on the composite
  A3 card-anchored        normalise each frame so its CARD WHITE matches the reference,
                          fuse, then v3 once
  A4 reef-anchored        normalise each frame by an affine fit on STATIC REEF pixels,
                          fuse, then v3 once   (the day-1 proposal)

WHY THE ANCHOR IS THE REAL VARIABLE
The card lies on bright near-field sand in direct downwelling light; the coral wall is a
shaded, more distant vertical surface. Measured on the real frames, card-anchored gain
leaves coral tiles as variable as no normalisation at all (CV 0.30 vs 0.064 for a reef
anchor). A3 exists to test that claim inside the full pipeline rather than in isolation.

THE METRIC THAT DECIDES IT
Split-half repeatability of COLONY COLOUR: build a composite from one half of the day's
frames and another from the other half, through the same arm, and compare the blue-over-
green ratio and lightness of fixed colony patches. The winning arm is the one whose answer
about a colony does not depend on which frames it happened to see. That is exactly the
property ask D needs.

Card delta-E is reported as a sanity check only. Every arm ends anchored on the card by
construction, so it ties and cannot discriminate.

INPUTS
  --run-dir DIR      intake run folder (frames/ + frames_manifest.json)
  --day YYYY-MM-DD   which day to fuse (the minted grouping unit); omit to use all
  --output-dir DIR
  --render-targets   Nereus v3 preset JSON
  --colonies         semicolon-separated x0,y0,x1,y1 boxes on stable coral
  --truth IMG        optional ground-truth image; when given, arms are also scored against it

OUTPUTS
  arms/<arm>/composite.jpg          the fused, corrected result for each arm
  arms/<arm>/half_{a,b}.jpg         the two half-composites the gate is measured on
  order_scores.csv                  per-arm, per-colony repeatability + card sanity
  cut_sheets/arms.jpg               the four arms side by side
  run_manifest.json

EXAMPLE
  python3 tools/bm_fuse_order_ab.py --run-dir runs/sprint21_day0_20260905 \
      --day 2026-09-04 --output-dir runs/sprint21_day3_order_20260905

ASSUMPTIONS / KNOWN LIMITATIONS
  - Colony boxes are hand-placed on stable coral; they are NOT segmented colonies. Ask D
    replaces them with real outlines.
  - A1 fuses already-rendered 8-bit images, which is what "correct then stack" means in
    practice; its tone curve and warm grade are therefore applied before averaging.
  - With five frames a half is two or three frames, so the repeatability numbers are
    indicative, not tight. The RANKING is the deliverable, not the absolute values.
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
import bm_temporal_fuse as tf                      # noqa: E402
import bm_grvi_correct as grvi                     # noqa: E402
import reference_card_color_utils as ccu           # noqa: E402

DEFAULT_COLONIES = "80,120,180,220;300,90,400,190;700,110,800,210"
RUNG = "L6_multiscale"
CARD_BOX = (440, 370, 860, 540)


def card_white(bgr, layout):
    """Median linear RGB of the card's white patch, or None when no card is found."""
    samples, _ = grvi.detect_and_sample(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), layout)
    if not samples:
        return None, None
    white = max((s for s in samples if s.patch_type == "gray"),
                key=lambda s: float(np.mean(s.median_srgb)), default=None)
    if white is None:
        return None, samples
    return ccu.srgb_to_linear(np.asarray(white.median_srgb) / 255.0), samples


def fuse_frames(frames, ref, mode, layout, whites):
    """Fuse `frames` under one anchoring mode. Returns uint8 BGR (not yet colour-corrected)."""
    tf.register(frames, ref, 0.5)
    use_reg = True
    stack = np.stack([tf.srgb_to_linear(tf.warped(f, use_reg)) for f in frames])
    ref_lin = tf.srgb_to_linear(tf.warped(ref, use_reg))
    mask = tf.static_mask(np.concatenate([stack, ref_lin[None]]), CARD_BOX)
    if mode == "card":
        ref_w = whites.get(ref.stamp)   # global anchor, even when ref is not in `frames`
        for i, f in enumerate(frames):
            w = whites.get(f.stamp)
            if w is None or ref_w is None:
                continue
            stack[i] = np.clip(stack[i] * (ref_w / np.maximum(w, 1e-6))[None, None, :], 0, None)
    elif mode == "reef":
        stack, _ = tf.normalise_to_reference(stack, ref_lin, mask)
    ref_i = frames.index(ref) if ref in frames else None
    comp, _ = tf.fuse(stack, frames, ref_i, rung=RUNG, mask=mask, ref_lin=ref_lin,
                      detail_exp=0.25)
    return tf.linear_to_srgb(comp).astype(np.uint8)


def run_grvi(paths, out_dir, targets):
    """Run the production colour runner (never reimplemented) over `paths`."""
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "bm_grvi_correct.py"),
           "--images", *paths, "--output-dir", out_dir]
    if targets:
        cmd += ["--render-targets", targets]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   [warn] grvi exit {r.returncode}: {r.stderr.strip()[-200:]}")
    got = {}
    for p in paths:
        stem = Path(p).stem
        cand = os.path.join(out_dir, stem, "after_grvi.jpg")
        if os.path.exists(cand):
            got[stem] = cv2.imread(cand, cv2.IMREAD_COLOR)
    return got


def _as_frame(src, img):
    """Wrap an already-corrected image in a Frame so the fusion machinery can use it."""
    g = tf.Frame.__new__(tf.Frame)
    g.stamp, g.date, g.hour = src.stamp, src.date, src.hour
    g.bgr, g.prefix, g.sharp = img, src.prefix, src.sharp
    g.shift, g.rotation_deg = (0.0, 0.0), 0.0
    return g


def colony_stats(bgr, boxes):
    """Blue-over-green and lightness per colony box, in linear space."""
    lin = tf.srgb_to_linear(bgr.astype(np.float32))
    out = []
    for (x0, y0, x1, y1) in boxes:
        p = lin[y0:y1, x0:x1].reshape(-1, 3)          # BGR
        b, g = float(np.median(p[:, 0])), float(np.median(p[:, 1]))
        out.append({"bg": b / max(g, 1e-6), "lum": float(np.median(p.mean(-1)))})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--day", default="")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--render-targets",
                    default="tools/reference_card_color_correction/render_targets_p9.json")
    ap.add_argument("--colonies", default=DEFAULT_COLONIES)
    args = ap.parse_args(argv)

    out = args.output_dir
    os.makedirs(os.path.join(out, "cut_sheets"), exist_ok=True)
    boxes = [tuple(int(v) for v in b.split(",")) for b in args.colonies.split(";")]

    recs = json.load(open(os.path.join(args.run_dir, "frames_manifest.json")))
    frames = sorted([tf.Frame(r, args.run_dir) for r in recs], key=lambda f: f.stamp)
    if args.day:
        frames = [f for f in frames if f.date == args.day]
    if len(frames) < 4:
        print(f"[ERROR] need >=4 frames, got {len(frames)}", file=sys.stderr)
        return 1
    ref = max(frames, key=lambda f: (round(f.prefix, 2), f.sharp))
    print(f"[order] {len(frames)} frames, day={args.day or 'all'}, reference={ref.stamp}")

    layout = ccu.load_template(Path(args.render_targets).parent
                               / "reference_card_template_v2" / "template_layout.json")
    whites = {}
    for f in frames:
        w, _ = card_white(f.bgr, layout)
        whites[f.stamp] = w
    print(f"[order] card read on {sum(1 for v in whites.values() if v is not None)}/{len(frames)} frames")

    half_a = frames[0::2]
    half_b = frames[1::2]
    arms = {"A1_correct_then_fuse": "a1", "A2_fuse_then_correct": "none",
            "A3_card_anchored": "card", "A4_reef_anchored": "reef"}
    rows, panels = [], []

    # A1 needs every frame corrected first; do it once and reuse for the halves.
    a1_dir = os.path.join(out, "arms", "A1_correct_then_fuse", "per_frame")
    os.makedirs(a1_dir, exist_ok=True)
    src_paths = []
    for f in frames:
        p = os.path.join(a1_dir, f"{f.stamp.replace(':', '-')}.jpg")
        cv2.imwrite(p, f.bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        src_paths.append(p)
    print("[order] A1: correcting each frame with the production runner ...")
    a1_corrected = run_grvi(src_paths, os.path.join(a1_dir, "grvi"), args.render_targets)

    for arm, mode in arms.items():
        adir = os.path.join(out, "arms", arm)
        os.makedirs(adir, exist_ok=True)
        results = {}
        for tag, subset in (("composite", frames), ("half_a", half_a), ("half_b", half_b)):
            # BOTH halves must be anchored on the SAME global reference frame, which
            # need not belong to the subset. Anchoring each half on its own best frame
            # makes the gate measure the difference between two anchors instead of
            # reproducibility, and unfairly penalises every normalising arm.
            if mode == "a1":
                sub = []
                for f in subset:
                    img = a1_corrected.get(f.stamp.replace(":", "-"))
                    if img is None:
                        continue
                    sub.append(_as_frame(f, img))
                if len(sub) < 2:
                    continue
                ref_img = a1_corrected.get(ref.stamp.replace(":", "-"))
                sref = _as_frame(ref, ref_img) if ref_img is not None else sub[0]
                results[tag] = fuse_frames(sub, sref, "none", layout, whites)
            else:
                fused = fuse_frames(list(subset), ref, mode, layout, whites)
                p = os.path.join(adir, f"raw_{tag}.jpg")
                cv2.imwrite(p, fused, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                got = run_grvi([p], os.path.join(adir, "grvi"), args.render_targets)
                results[tag] = got.get(f"raw_{tag}", fused)
            cv2.imwrite(os.path.join(adir, f"{tag}.jpg"), results[tag],
                        [int(cv2.IMWRITE_JPEG_QUALITY), 94])

        if "half_a" not in results or "half_b" not in results:
            print(f"   {arm}: halves unavailable, skipped")
            continue
        sa, sb = colony_stats(results["half_a"], boxes), colony_stats(results["half_b"], boxes)
        bg_err = [abs(a["bg"] - b["bg"]) / max((a["bg"] + b["bg"]) / 2, 1e-6) * 100
                  for a, b in zip(sa, sb)]
        lum_err = [abs(a["lum"] - b["lum"]) / max((a["lum"] + b["lum"]) / 2, 1e-6) * 100
                   for a, b in zip(sa, sb)]
        _, samples = card_white(results["composite"], layout)
        de = (float(np.mean(ccu.delta_e2000(
            np.array([s.median_srgb for s in samples]),
            np.array([s.target_srgb for s in samples])))) if samples else float("nan"))
        rows.append({"arm": arm, "bg_repeat_pct": round(float(np.mean(bg_err)), 2),
                     "bg_worst_pct": round(float(np.max(bg_err)), 2),
                     "lum_repeat_pct": round(float(np.mean(lum_err)), 2),
                     "card_de2000": round(de, 2) if de == de else "",
                     "colonies": len(boxes)})
        panels.append((arm, results["composite"],
                       [f"colony B/G repeat {np.mean(bg_err):.2f}%",
                        f"lightness repeat {np.mean(lum_err):.2f}%  card dE {de:.1f}"]))
        print(f"   {arm:22} colony B/G repeatability {np.mean(bg_err):5.2f}%   "
              f"lightness {np.mean(lum_err):5.2f}%   card dE2000 {de:5.1f}")

    if rows:
        with open(os.path.join(out, "order_scores.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        best = min(rows, key=lambda r: r["bg_repeat_pct"])
        print(f"\n[order] WINNER on colony repeatability: {best['arm']} "
              f"({best['bg_repeat_pct']}% B/G)")
        tf.ladder_sheet(os.path.join(out, "cut_sheets", "arms.jpg"),
                        f"Ask B - four orderings, {args.day or 'all frames'} "
                        f"(n={len(frames)})", panels, (60, 340, 60, 480))
    json.dump({"run_tag": f"order_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
               "args": vars(args), "reference": ref.stamp, "rung": RUNG,
               "frames": [f.stamp for f in frames], "scores": rows},
              open(os.path.join(out, "run_manifest.json"), "w"), indent=2)
    print(f"[order] wrote {out}/order_scores.csv, arms/, cut_sheets/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
