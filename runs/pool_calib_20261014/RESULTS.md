# Underwater calibration, pool test Wed 2026-10-14: results

Tool: `tools/pool_calib_pipeline.py` (commit 5b52e2e4; QE #176 nits applied). Pass bars were written before the run:
`docs/pool_calib_prereg_20261014.md` (+ amendment A1). Full run folders, with images, cut sheets and
CSVs, are on Nick's Mac only (not in git):

- primary: `~/NereusPool/20261014_CALIB/20261014_173833_pool1014/`
- sensitivity, NOT pre-registered: `~/NereusPool/20261014_CALIB/20261014_173920_pool1014_clipsens_NONPREREG/`
- earlier runs of the same data (before the QE fixes, same verdicts): `~/NereusPool/20261014_CALIB/superseded/`
- review page (sliders): https://claude.ai/artifact/Cxq6XbGYbNmjJQ4scvvoac

This folder keeps the small JSON/Markdown outputs: `primary/summary.md`, the per-camera intrinsics,
coverage, models and before/after numbers, and both manifests (input file sha256s included).

## Verdict

The pool frames give a solid **underwater focal length**: fx ≈ 2,617–2,628 px, which is 1.33× the in-air
nominal, exactly the flat-port prediction, on all three cameras. They also give a sub-0.4 px fit where the
board was. They do **not** calibrate the edges of the frame, so every camera FAILS full-frame (B3 coverage,
B4 edges) and also FAILS the production crop (B5c).

Nick's hypothesis is right:
- The board sat at 1.2–1.8 m (median 1.55 m), where its pattern fills ~20 % of the frame width.
- The live preview showed only the 1600×900 centre crop.
- So every corner landed in the central 22 % of the frame. The closest one was ~1,700 px from each frame
  corner, and no frame-corner cell was reached.

Outside that region the model is guessing; the full-frame undistortion tears at the sides and bottom.
Inside the production crop, the bottom crop corners are known only to 2–5 px (bar 0.5 px), because the
board was mostly held in the upper middle.

**Use now (provisional):**
- **Standard-model** fx ≈ 2,620 px ±1 % and the correction across the middle of the crop.
- The fisheye model fits worse, but it puts fx +2.2 % (003) / +1.4 % (004) higher. Across model
  families, fx is only known to ±2 %.
- Do NOT use these numbers for the full frame or video.

**Fix:** one ~15-minute dive (recipe below).

| camera | model (held-out pick) | fx / fy | cx, cy (full) | cx, cy (crop) | fx ÷ in-air | held-out RMS | poses | covered | full / crop |
|---|---|---|---|---|---|---|---|---|---|
| bmcam003 | standard | 2617 / 2637 | 2315, 1176 | 811, 330 | 1.332 | 0.354 px | 29 | 22 %, 0/4 corners | FAIL / FAIL |
| bmcam004 | standard | 2628 / 2637 | 2300, 1196 | 796, 350 | 1.338 | 0.334 px | 32 | 22 %, 0/4 corners | FAIL / FAIL |
| nereus002 | standard | 2621 / 2620 | 2337, 1272 | 833, 426 | 1.334 | 0.290 px | 10 | 22 %, 0/4 corners | FAIL / FAIL |

Pixels are native 4608×2592 sensor px. The crop is `[1504, 846, 1600, 900]`, a pure pixel crop: fx, fy and
the distortion are unchanged, and only cx and cy shift. Bootstrap std(fx) is 0.2–0.35 %.

## What else the data say

- **Models.** Held-out RMS:
  - standard ≈ rational (0.33–0.35 px);
  - fisheye is worse: 0.62 px on 003, 0.49 px on 004.
  - On **both bmcam units** the standard and rational fits lean on a large tangential term
    (p1 = −0.040 / −0.027), and cy sits 120 / 100 px above centre. Fisheye has no tangential terms.
  - nereus002, in a different housing, shows neither (p1 ≈ 0, cy 24 px off).
  - This hints at real decentering in the bmcam housing, e.g. a port not quite parallel to the sensor.
    With centre-only data it cannot be separated from the principal point. Re-check after the edge
    dive.
- **The edges are unpinned.** The two units fit equally well in the centre but disagree completely about
  the edges: k3 = −1.12 (003) vs +0.04 (004). bmcam003's polynomial even folds before the left/right
  frame edges (the horizontal FOV is not computable).
- **fx depends on frame selection.** It moves 0.5–0.8 % depending on which frames go in (primary vs
  sensitivity). That is more than the bootstrap spread, so quote standard-model fx as ±1 % (±2 % across
  model families) until the edge dive.
- **Clipping.**
  - 61/120 (003) and 69/120 (004) frames FAIL the registered clipping rule: the white squares are
    saturated under sun and auto exposure.
  - The sensitivity run lets them in: poses go to 62–70 and coverage to 30–32 %, but still 0/4
    corners.
  - Coverage, not exposure, is the limit.
- **nereus002.**
  - Its port type is unknown, but fx ÷ in-air = 1.334, so it behaves like a flat port. A dome would
    read ~1.0. This is an inference, not a measurement of the port.
  - It has a strong circular port vignette (black frame corners, per the nereus002 session), so its
    corner cells cannot be covered at all.
  - Only 10 distinct poses (B1 FAIL) and std(cx, cy) 11.5 px (B5 FAIL).
- **Straight-line check** on board rows from a frame the model never saw (fold model): bend goes from
  0.40 → 0.13 px RMS (003), 0.50 → 0.11 px (004) and 1.06 → 0.14 px (nereus002). The crop centre is only
  mildly distorted, so these numbers are small.

## Two readings of the model-disagreement check (B4 / B5c), and A2 pending

QE (#176) pointed out that the aligned, shape-only reading (amendment A1) hides how far the models'
intrinsics differ. Both readings, at the worst production-crop corner:

| camera | shape-only (aligned) | metric (literal) | widest K difference between models |
|---|---|---|---|
| bmcam003 | 5.3 px | 188 px | fisheye vs standard: Δfx +2.2 %, Δcy +166 px |
| bmcam004 | 4.1 px | 127 px | fisheye vs standard: Δfx +1.4 %, Δcy +113 px |
| nereus002 | 2.4 px | 48 px | fisheye vs standard: Δcx −43 px |

Bar: model disagreement ≤ 1 px. Every camera FAILs under both readings, so no 10/14 verdict depends
on the choice. Which reading governs the edge-dive verdict (**A2**) is Nick's call; the EM is taking
it to him before that rerun is judged. Details: `docs/pool_calib_prereg_20261014.md`.

## Capture recipe for the next dive

1. **Distance.** Board at **0.7–0.9 m**, where the pattern is 35–45 % of the frame width.
2. **Positions.** Put it in **9 places**: the 4 frame corners, the 4 edge midpoints and the centre, with a
   board corner touching the frame corner or edge each time.
3. **Poses.** At each place shoot flat, tilted ~30° left/right and tilted ~30° up/down: ~27 poses. Add
   3–5 flat poses at 1.5 m. Hold still for 2 frames per pose.
4. **Preview.** Pose on a **full-frame preview**, not the 1600×900 crop.
5. **Exposure.** Lock it ~1 stop darker for the calibration block, or tilt out of the sun glint.
6. **Focus.** Keep LensPosition 0.8, the production focus.
7. **Rerun.** Run the same command (below) on the new frames and compare against the same bars.

```bash
.venv/bin/python tools/pool_calib_pipeline.py \
  bmcam003=~/NereusPool/<date>/<bmcam003 run>/calibration \
  bmcam004=~/NereusPool/<date>/<bmcam004 run>/calibration \
  --out-root ~/NereusPool/<date>_CALIB --tag <tag>
```

## Not tested / limits

- **JPEG only.** The DNGs are still on the units until the Friday bench pull. JPEG and DNG share the
  pixel grid; re-running on DNG is a check, not expected to change the verdict.
- **In-air nominal fx (1964 px)** is a datasheet assumption: no in-air ChArUco set exists. The 10/12
  "calibration" frames contain no board.
- **Fresh water.** In seawater fx is ~+0.5 % (n 1.340 vs 1.333).
- **Amendment A1** changed how B4/B5c are measured (homography-aligned) after one debug run. Both numbers
  are in every bar row; see the prereg doc, its ground-truth test, and the negative control (centre-only
  data still reads > 2 px at the frame corners).
