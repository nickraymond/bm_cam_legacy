# Underwater calibration — pool049_imx708_dng

Pre-registered bars: `docs/pool_calib_prereg_20261014.md`. Pixels = native 4608×2592 sensor px unless labelled.
B4/B5c model disagreement scored on the **literal** reading; mask / residual / flag rules: `docs/pool_calib_prereg_20261016.md`.

## imx708
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 78: screen {'PASS': 40, 'WARN': 27, 'FAIL': 11}, 57 distinct poses, 57 used after outlier rejection; board at 0.85–1.87 m (median 0.98 m)
- Model **rational** (lowest held-out RMS 0.504 px). Held-out RMS: standard 0.548 px, rational 0.504 px, fisheye 0.609 px; training RMS 0.489 px
- Full frame 4608×2592: fx 2628.3, fy 2628.0, cx 2313.9, cy 1294.2, dist [11.339, -41.085, 0.0047187, 0.0051193, 27.277, 11.081, -44.98, 36.969]
- Production crop 1600×900 at [1504, 846, 1600, 900]: fx 2628.3, fy 2628.0, cx 809.9, cy 448.2 (same distortion)
- Underwater fx / in-air nominal (1964 px, datasheet assumption) = 1.338; FOV H n/a (model folds before the edge: extrapolating), V 49.1 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 57 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.504 | < 1.0 px (rational) |
| B3 coverage | FAIL | covered_frac 0.5, corner_cells top-left corner False, top-right corner False, bottom-left corner False, bottom-right corner False | >= 60% of the 38 of 8x5 cells >= 50% visible (--mask) and the 4 extreme visible cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px inf, worst_model_disagreement_px inf, literal_ray_px inf, literal_models_px inf | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at the visible area's 4 corners + 4 edge midpoints (--mask) (ray uncertainty homography-aligned (A1), model disagreement LITERAL (metric, d33)) |
| B5 stable | PASS | rel_std_f 0.00113, std_c_px 2.12, replicates_ok 100/100 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px (>= 95% of replicates fit) |
| B5c production crop | FAIL | worst_ray_uncertainty_px 0.243, worst_model_disagreement_px 1.19, literal_ray_px 5.46, literal_models_px 23.9 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (ray uncertainty homography-aligned (A1), model disagreement LITERAL (metric, d33)) |
| B6 focal sanity | PASS | 1.34 | fx / 1964 px (in-air nominal) in [1.25, 1.42] |

Cross-model intrinsics (information, not a bar; the shape-only reading of B4/B5c absorbs these):

| models | Δfx | Δfy | Δcx | Δcy |
|---|---|---|---|---|
| rational vs standard | +0.00 % | +0.00 % | +1 px | -1 px |
| rational vs fisheye | -0.26 % | -0.36 % | -12 px | -15 px |
| standard vs fisheye | -0.26 % | -0.36 % | -13 px | -13 px |

Mask (`imx708_port_mask.png`, sha256 599d1ab04b95…): 4.40% of pixels masked; B3 on 38 eligible cells; corner cells (row, col) {'top-left corner': [1, 0], 'top-right corner': [1, 7], 'bottom-left corner': [4, 0], 'bottom-right corner': [4, 7]}; B4 probes at TL (325, 182), TR (4270, 235), BL (194, 2507), BR (4455, 2529), top (2304, 0), bottom (2304, 2591), left (0, 1296), right (4607, 1296)

Held-out residuals vs radius (r = distance from the frame centre / half-diagonal (frame corner = 1.0)): inner r <= 0.5 0.47 px (n 3205), outer r > 0.8 None px (n 0), ratio None

| r_norm bin | n | held-out RMS px |
|---|---|---|
| 0.0–0.2 | 646 | 0.397 |
| 0.2–0.4 | 1674 | 0.443 |
| 0.4–0.5 | 885 | 0.561 |
| 0.5–0.6 | 338 | 0.668 |
| 0.6–0.7 | 94 | 0.809 |
| 0.7–0.8 | 3 | 1.07 |
| 0.8–0.9 | 0 | None |
| 0.9–1.0 | 0 | None |

| board distance bin (m) | n corners | views | held-out RMS px |
|---|---|---|---|
| 0.75–1.00 | 2626 | 32 | 0.463 |
| 1.00–1.25 | 699 | 19 | 0.515 |
| 1.75–2.00 | 315 | 6 | 0.748 |

Tilt / decentre flags (report-only): principal point dx +10 px, dy -2 px (flag False); standard-fit p1 0.00429, p2 0.00531 (flag True); outer/inner residual None (flag None); **any flag: True**

Cross-group check (rational, report-only):

| fit on | score on | views | cross RMS px | own held-out px | ratio |
|---|---|---|---|---|---|
| cal_a | cal_b | 32 → 25 | 0.678 | 0.56 | 1.21 |
| cal_b | cal_a | 25 → 32 | 0.578 | 0.454 | 1.27 |

Capture recipe / notes:
- This session: board at a median 0.98 m (range 0.85-1.87 m); there the frame is 1.72 m wide, so the 560 mm pattern filled only 32% of the width.
- Closest corner to each frame edge: left 716 px (16%), right 459 px (10%), top 90 px (3%), bottom 1043 px (40%).
- Empty corner cells: top-left corner, top-right corner, bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~0.7-0.9 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

