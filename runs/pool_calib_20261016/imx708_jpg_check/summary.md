# Underwater calibration — pool049_imx708_jpg_check

Pre-registered bars: `docs/pool_calib_prereg_20261014.md`. Pixels = native 4608×2592 sensor px unless labelled.
B4/B5c model disagreement scored on the **literal** reading; mask / residual / flag rules: `docs/pool_calib_prereg_20261016.md`.

## imx708
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 78: screen {'PASS': 28, 'WARN': 39, 'FAIL': 11}, 57 distinct poses, 54 used after outlier rejection; board at 0.85–1.87 m (median 0.97 m)
- Model **rational** (lowest held-out RMS 0.551 px). Held-out RMS: standard 0.732 px, rational 0.551 px; training RMS 0.474 px
- Full frame 4608×2592: fx 2627.6, fy 2626.9, cx 2312.8, cy 1293.3, dist [-5.2288, 8.8994, 0.004358, 0.0049091, -4.8769, -5.5194, 10.134, -6.1966]
- Production crop 1600×900 at [1504, 846, 1600, 900]: fx 2627.6, fy 2626.9, cx 808.8, cy 447.3 (same distortion)
- Underwater fx / in-air nominal (1964 px, datasheet assumption) = 1.338; FOV H n/a (model folds before the edge: extrapolating), V 49.1 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 54 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.551 | < 1.0 px (rational) |
| B3 coverage | FAIL | covered_frac 0.526, corner_cells top-left corner False, top-right corner True, bottom-left corner False, bottom-right corner False | >= 60% of the 38 of 8x5 cells >= 50% visible (--mask) and the 4 extreme visible cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px inf, worst_model_disagreement_px inf, literal_ray_px inf, literal_models_px inf | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at the visible area's 4 corners + 4 edge midpoints (--mask) (ray uncertainty homography-aligned (A1), model disagreement LITERAL (metric, d33)) |
| B5 stable | PASS | rel_std_f 0.00113, std_c_px 2.06, replicates_ok 100/100 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px (>= 95% of replicates fit) |
| B5c production crop | FAIL | worst_ray_uncertainty_px 0.261, worst_model_disagreement_px 0.269, literal_ray_px 4.99, literal_models_px 3.56 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (ray uncertainty homography-aligned (A1), model disagreement LITERAL (metric, d33)) |
| B6 focal sanity | PASS | 1.34 | fx / 1964 px (in-air nominal) in [1.25, 1.42] |

Cross-model intrinsics (information, not a bar; the shape-only reading of B4/B5c absorbs these):

| models | Δfx | Δfy | Δcx | Δcy |
|---|---|---|---|---|
| rational vs standard | +0.05 % | +0.07 % | +1 px | -3 px |

Mask (`imx708_port_mask.png`, sha256 599d1ab04b95…): 4.40% of pixels masked; B3 on 38 eligible cells; corner cells (row, col) {'top-left corner': [1, 0], 'top-right corner': [1, 7], 'bottom-left corner': [4, 0], 'bottom-right corner': [4, 7]}; B4 probes at TL (325, 182), TR (4270, 235), BL (194, 2507), BR (4455, 2529), top (2304, 0), bottom (2304, 2591), left (0, 1296), right (4607, 1296)

Held-out residuals vs radius (r = distance from the frame centre / half-diagonal (frame corner = 1.0)): inner r <= 0.5 0.492 px (n 3190), outer r > 0.8 None px (n 0), ratio None

| r_norm bin | n | held-out RMS px |
|---|---|---|
| 0.0–0.2 | 642 | 0.422 |
| 0.2–0.4 | 1653 | 0.475 |
| 0.4–0.5 | 895 | 0.562 |
| 0.5–0.6 | 374 | 0.72 |
| 0.6–0.7 | 121 | 0.969 |
| 0.7–0.8 | 19 | 1.55 |
| 0.8–0.9 | 0 | None |
| 0.9–1.0 | 0 | None |

| board distance bin (m) | n corners | views | held-out RMS px |
|---|---|---|---|
| 0.75–1.00 | 2645 | 32 | 0.493 |
| 1.00–1.25 | 723 | 19 | 0.614 |
| 1.75–2.00 | 336 | 6 | 0.786 |

Tilt / decentre flags (report-only): principal point dx +9 px, dy -3 px (flag False); standard-fit p1 0.00388, p2 0.00509 (flag True); outer/inner residual None (flag None); **any flag: True**

Cross-group check (rational, report-only):

| fit on | score on | views | cross RMS px | own held-out px | ratio |
|---|---|---|---|---|---|
| cal_a | cal_b | 32 → 25 | 0.81 | 0.615 | 1.32 |
| cal_b | cal_a | 25 → 32 | 0.649 | 0.489 | 1.33 |

Capture recipe / notes:
- This session: board at a median 0.97 m (range 0.85-1.87 m); there the frame is 1.71 m wide, so the 560 mm pattern filled only 33% of the width.
- Closest corner to each frame edge: left 683 px (15%), right 331 px (7%), top 90 px (3%), bottom 1013 px (39%).
- Empty corner cells: top-left corner, bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~0.7-0.9 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

