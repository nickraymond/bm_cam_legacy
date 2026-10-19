# Underwater calibration — pool049_openmv_raw

Pre-registered bars: `docs/pool_calib_prereg_20261014.md`. Pixels = native 1280×800 sensor px unless labelled.
B4/B5c model disagreement scored on the **literal** reading; mask / residual / flag rules: `docs/pool_calib_prereg_20261016.md`.

## n6
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 78: screen {'PASS': 9, 'WARN': 25, 'FAIL': 44}, 26 distinct poses, 25 used after outlier rejection; board at 0.84–1.06 m (median 0.93 m)
- Model **standard** (lowest held-out RMS 0.295 px). Held-out RMS: standard 0.295 px, rational 0.298 px, fisheye 0.313 px; training RMS 0.272 px
- Full frame 1280×800: fx 1628.9, fy 1630.0, cx 637.4, cy 490.4, dist [-0.42141, 0.25358, 0.0026488, 0.0012108, -0.10987]
- Production crop 1280×800 at [0, 0, 1280, 800]: fx 1628.9, fy 1630.0, cx 637.4, cy 490.4 (same distortion)
- Underwater fx / in-air nominal (933 px, --nominal-fx) = 1.745; FOV H 45.7 deg, V 28.3 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 25 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.295 | < 1.0 px (standard) |
| B3 coverage | FAIL | covered_frac 0.675, corner_cells top-left corner True, top-right corner True, bottom-left corner False, bottom-right corner False | >= 60% of 8x5 cells and all 4 corner cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px 1.43, worst_model_disagreement_px 1.43, literal_ray_px 9.11, literal_models_px 24.9 | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at 4 frame corners + 4 edge midpoints (ray uncertainty homography-aligned (A1), model disagreement LITERAL (metric, d33)) |
| B5 stable | PASS | rel_std_f 0.00101, std_c_px 3.74, replicates_ok 100/100 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px (>= 95% of replicates fit) |
| B5c production crop | FAIL | worst_ray_uncertainty_px 1.43, worst_model_disagreement_px 1.43, literal_ray_px 9.11, literal_models_px 24.9 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (ray uncertainty homography-aligned (A1), model disagreement LITERAL (metric, d33)) |
| B6 focal sanity | WARN | 1.75 | fx / 933 px (in-air nominal, --nominal-fx) in [1.25, 1.42] |

Cross-model intrinsics (information, not a bar; the shape-only reading of B4/B5c absorbs these):

| models | Δfx | Δfy | Δcx | Δcy |
|---|---|---|---|---|
| standard vs rational | -0.01 % | -0.02 % | -3 px | -2 px |
| standard vs fisheye | +0.35 % | +0.27 % | +13 px | +12 px |
| rational vs fisheye | +0.36 % | +0.28 % | +16 px | +14 px |

Held-out residuals vs radius (r = distance from the frame centre / half-diagonal (frame corner = 1.0)): inner r <= 0.5 0.257 px (n 739), outer r > 0.8 0.313 px (n 33), ratio 1.22

| r_norm bin | n | held-out RMS px |
|---|---|---|
| 0.0–0.2 | 149 | 0.286 |
| 0.2–0.4 | 349 | 0.246 |
| 0.4–0.5 | 241 | 0.255 |
| 0.5–0.6 | 190 | 0.374 |
| 0.6–0.7 | 167 | 0.283 |
| 0.7–0.8 | 166 | 0.351 |
| 0.8–0.9 | 33 | 0.313 |
| 0.9–1.0 | 0 | None |

| board distance bin (m) | n corners | views | held-out RMS px |
|---|---|---|---|
| 0.75–1.00 | 1174 | 23 | 0.298 |
| 1.00–1.25 | 121 | 3 | 0.259 |

Tilt / decentre flags (report-only): principal point dx -3 px, dy +90 px (flag True); standard-fit p1 0.00265, p2 0.00121 (flag True); outer/inner residual 1.22 (flag False); **any flag: True**

Cross-group check (standard, report-only):

| fit on | score on | views | cross RMS px | own held-out px | ratio |
|---|---|---|---|---|---|
| cal_a | cal_b | 13 → 13 | 0.342 | 0.332 | 1.03 |
| cal_b | cal_a | 13 → 13 | 0.247 | 0.237 | 1.04 |

Capture recipe / notes:
- This session: board at a median 0.93 m (range 0.84-1.06 m); there the frame is 0.73 m wide, so the 560 mm pattern filled only 77% of the width.
- Closest corner to each frame edge: left 53 px (4%), right 38 px (3%), top 48 px (6%), bottom 293 px (37%).
- Empty corner cells: bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~1.6-2.1 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

## ae3
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 78: screen {'PASS': 8, 'WARN': 25, 'FAIL': 45}, 25 distinct poses, 25 used after outlier rejection; board at 0.85–1.01 m (median 0.92 m)
- Model **standard** (held-out RMS 0.290 px within 2% of the best (0.288 px): preference order standard > fisheye > rational). Held-out RMS: standard 0.290 px, rational 0.288 px, fisheye 0.293 px; training RMS 0.283 px
- Full frame 1280×800: fx 1394.2, fy 1393.3, cx 677.4, cy 375.1, dist [0.39104, -0.15982, 0.0043035, -0.0023332, 1.2676]
- Production crop 1280×800 at [0, 0, 1280, 800]: fx 1394.2, fy 1393.3, cx 677.4, cy 375.1 (same distortion)
- Underwater fx / in-air nominal (1033 px, --nominal-fx) = 1.349; FOV H 46.2 deg, V 31.1 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 25 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.29 | < 1.0 px (standard) |
| B3 coverage | FAIL | covered_frac 0.6, corner_cells top-left corner True, top-right corner True, bottom-left corner False, bottom-right corner False | >= 60% of 8x5 cells and all 4 corner cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px 1.83, worst_model_disagreement_px inf, literal_ray_px 6.58, literal_models_px inf | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at 4 frame corners + 4 edge midpoints (ray uncertainty homography-aligned (A1), model disagreement LITERAL (metric, d33)) |
| B5 stable | PASS | rel_std_f 0.00148, std_c_px 2.1, replicates_ok 100/100 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px (>= 95% of replicates fit) |
| B5c production crop | FAIL | worst_ray_uncertainty_px 1.83, worst_model_disagreement_px 6.27, literal_ray_px 6.58, literal_models_px 17.4 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (ray uncertainty homography-aligned (A1), model disagreement LITERAL (metric, d33)) |
| B6 focal sanity | PASS | 1.35 | fx / 1033 px (in-air nominal, --nominal-fx) in [1.25, 1.42] |

Cross-model intrinsics (information, not a bar; the shape-only reading of B4/B5c absorbs these):

| models | Δfx | Δfy | Δcx | Δcy |
|---|---|---|---|---|
| standard vs rational | -0.30 % | -0.30 % | +0 px | +0 px |
| standard vs fisheye | -0.20 % | -0.22 % | +4 px | -8 px |
| rational vs fisheye | +0.10 % | +0.08 % | +4 px | -8 px |

Held-out residuals vs radius (r = distance from the frame centre / half-diagonal (frame corner = 1.0)): inner r <= 0.5 0.268 px (n 783), outer r > 0.8 0.446 px (n 4), ratio None

| r_norm bin | n | held-out RMS px |
|---|---|---|
| 0.0–0.2 | 163 | 0.383 |
| 0.2–0.4 | 388 | 0.216 |
| 0.4–0.5 | 232 | 0.246 |
| 0.5–0.6 | 157 | 0.255 |
| 0.6–0.7 | 110 | 0.413 |
| 0.7–0.8 | 73 | 0.351 |
| 0.8–0.9 | 4 | 0.446 |
| 0.9–1.0 | 0 | None |

| board distance bin (m) | n corners | views | held-out RMS px |
|---|---|---|---|
| 0.75–1.00 | 1002 | 23 | 0.274 |
| 1.00–1.25 | 125 | 2 | 0.397 |

Tilt / decentre flags (report-only): principal point dx +37 px, dy -25 px (flag True); standard-fit p1 0.0043, p2 -0.00233 (flag True); outer/inner residual None (flag None); **any flag: True**

Cross-group check (standard, report-only):

| fit on | score on | views | cross RMS px | own held-out px | ratio |
|---|---|---|---|---|---|
| cal_a | cal_b | 16 → 9 | 0.259 | 0.257 | 1.01 |
| cal_b | cal_a | 9 → 16 | 0.382 | 0.307 | 1.24 |

Capture recipe / notes:
- This session: board at a median 0.92 m (range 0.85-1.01 m); there the frame is 0.84 m wide, so the 560 mm pattern filled only 66% of the width.
- Closest corner to each frame edge: left 82 px (6%), right 71 px (6%), top 49 px (6%), bottom 322 px (40%).
- Empty corner cells: bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~1.4-1.8 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

