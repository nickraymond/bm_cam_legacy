# Underwater calibration — pool1014

Pre-registered bars: `docs/pool_calib_prereg_20261014.md`. Pixels = native 4608×2592 sensor px unless labelled.

## bmcam003
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 120: screen {'PASS': 22, 'WARN': 13, 'FAIL': 85}, 30 distinct poses, 29 used after outlier rejection; board at 1.21–1.81 m (median 1.55 m)
- Model **standard** (held-out RMS 0.354 px within 2% of the best (0.354 px): preference order standard > fisheye > rational). Held-out RMS: standard 0.354 px, rational 0.354 px, fisheye 0.621 px; training RMS 0.301 px
- Full frame 4608×2592: fx 2617.3, fy 2637.3, cx 2314.9, cy 1175.7, dist [0.27511, 0.62733, -0.040147, 0.0018875, -1.1158]
- Production crop 1600×900 at [1504, 846, 1600, 900]: fx 2617.3, fy 2637.3, cx 810.9, cy 329.7 (same distortion)
- Underwater fx / in-air nominal (1964 px, datasheet assumption) = 1.332; FOV H n/a (model folds before the edge: extrapolating), V 49.4 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 29 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.354 | < 1.0 px (standard) |
| B3 coverage | FAIL | covered_frac 0.225, corner_cells top-left corner False, top-right corner False, bottom-left corner False, bottom-right corner False | >= 60% of 8x5 cells and all 4 corner cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px inf, worst_model_disagreement_px inf, literal_ray_px inf, literal_models_px inf | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at 4 frame corners + 4 edge midpoints (homography-aligned, amendment A1) |
| B5 stable | PASS | rel_std_f 0.00346, std_c_px 5.87, replicates_ok 100/100 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px (>= 95% of replicates fit) |
| B5c production crop | FAIL | worst_ray_uncertainty_px 4.62, worst_model_disagreement_px 5.33, literal_ray_px 20.6, literal_models_px 188 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (homography-aligned, amendment A1) |
| B6 focal sanity | PASS | 1.33 | fx / 1964 px (in-air nominal) in [1.25, 1.42] |

Cross-model intrinsics (information, not a bar; the shape-only reading of B4/B5c absorbs these):

| models | Δfx | Δfy | Δcx | Δcy |
|---|---|---|---|---|
| standard vs rational | +0.01 % | +0.01 % | -0 px | -0 px |
| standard vs fisheye | +2.23 % | +1.37 % | -6 px | +166 px |
| rational vs fisheye | +2.22 % | +1.36 % | -5 px | +167 px |

Capture recipe / notes:
- This session: board at a median 1.55 m (range 1.21-1.81 m); there the frame is 2.73 m wide, so the 560 mm pattern filled only 20% of the width.
- Closest corner to each frame edge: left 1393 px (30%), right 1544 px (34%), top 707 px (27%), bottom 906 px (35%).
- Empty corner cells: top-left corner, top-right corner, bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~0.7-0.9 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- 61/120 frames failed on clipping (white squares at 255): lock exposure ~1 stop darker for the calibration block, or tilt the board out of the sun glint.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

## bmcam004
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 120: screen {'PASS': 24, 'WARN': 11, 'FAIL': 85}, 32 distinct poses, 32 used after outlier rejection; board at 1.21–1.80 m (median 1.55 m)
- Model **standard** (held-out RMS 0.334 px within 2% of the best (0.332 px): preference order standard > fisheye > rational). Held-out RMS: standard 0.334 px, rational 0.332 px, fisheye 0.492 px; training RMS 0.322 px
- Full frame 4608×2592: fx 2628.2, fy 2636.8, cx 2300.3, cy 1195.6, dist [0.24779, 0.59272, -0.026737, -0.0011085, 0.044912]
- Production crop 1600×900 at [1504, 846, 1600, 900]: fx 2628.2, fy 2636.8, cx 796.3, cy 349.6 (same distortion)
- Underwater fx / in-air nominal (1964 px, datasheet assumption) = 1.338; FOV H 69.6 deg, V 49.1 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 32 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.334 | < 1.0 px (standard) |
| B3 coverage | FAIL | covered_frac 0.225, corner_cells top-left corner False, top-right corner False, bottom-left corner False, bottom-right corner False | >= 60% of 8x5 cells and all 4 corner cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px inf, worst_model_disagreement_px inf, literal_ray_px inf, literal_models_px inf | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at 4 frame corners + 4 edge midpoints (homography-aligned, amendment A1) |
| B5 stable | PASS | rel_std_f 0.00207, std_c_px 6.79, replicates_ok 100/100 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px (>= 95% of replicates fit) |
| B5c production crop | FAIL | worst_ray_uncertainty_px 4.31, worst_model_disagreement_px 4.12, literal_ray_px 22.2, literal_models_px 127 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (homography-aligned, amendment A1) |
| B6 focal sanity | PASS | 1.34 | fx / 1964 px (in-air nominal) in [1.25, 1.42] |

Cross-model intrinsics (information, not a bar; the shape-only reading of B4/B5c absorbs these):

| models | Δfx | Δfy | Δcx | Δcy |
|---|---|---|---|---|
| standard vs rational | -0.00 % | -0.00 % | +0 px | -0 px |
| standard vs fisheye | +1.37 % | +0.95 % | +6 px | +113 px |
| rational vs fisheye | +1.37 % | +0.95 % | +5 px | +113 px |

Capture recipe / notes:
- This session: board at a median 1.55 m (range 1.21-1.80 m); there the frame is 2.71 m wide, so the 560 mm pattern filled only 21% of the width.
- Closest corner to each frame edge: left 1543 px (33%), right 1375 px (30%), top 677 px (26%), bottom 937 px (36%).
- Empty corner cells: top-left corner, top-right corner, bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~0.7-0.9 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- 69/120 frames failed on clipping (white squares at 255): lock exposure ~1 stop darker for the calibration block, or tilt the board out of the sun glint.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

## nereus002
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 50: screen {'PASS': 5, 'WARN': 9, 'FAIL': 36}, 10 distinct poses, 10 used after outlier rejection; board at 1.29–1.73 m (median 1.53 m)
- Model **standard** (lowest held-out RMS 0.290 px). Held-out RMS: standard 0.290 px, rational 0.799 px, fisheye 0.342 px; training RMS 0.271 px
- Full frame 4608×2592: fx 2621.3, fy 2620.2, cx 2336.8, cy 1272.0, dist [0.27812, 0.6399, 0.00034213, 0.01225, -0.41962]
- Production crop 1600×900 at [1504, 846, 1600, 900]: fx 2621.3, fy 2620.2, cx 832.8, cy 426.0 (same distortion)
- Underwater fx / in-air nominal (1964 px, datasheet assumption) = 1.334; FOV H 70.4 deg, V 49.1 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | FAIL | 10 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.29 | < 1.0 px (standard) |
| B3 coverage | FAIL | covered_frac 0.225, corner_cells top-left corner False, top-right corner False, bottom-left corner False, bottom-right corner False | >= 60% of 8x5 cells and all 4 corner cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px inf, worst_model_disagreement_px 176, literal_ray_px inf, literal_models_px 228 | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at 4 frame corners + 4 edge midpoints (homography-aligned, amendment A1) |
| B5 stable | FAIL | rel_std_f 0.00317, std_c_px 11.5, replicates_ok 100/100 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px (>= 95% of replicates fit) |
| B5c production crop | FAIL | worst_ray_uncertainty_px 2.46, worst_model_disagreement_px 2.4, literal_ray_px 28.7, literal_models_px 47.7 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (homography-aligned, amendment A1) |
| B6 focal sanity | PASS | 1.33 | fx / 1964 px (in-air nominal) in [1.25, 1.42] |

Cross-model intrinsics (information, not a bar; the shape-only reading of B4/B5c absorbs these):

| models | Δfx | Δfy | Δcx | Δcy |
|---|---|---|---|---|
| standard vs rational | +0.16 % | +0.17 % | -0 px | -0 px |
| standard vs fisheye | -0.12 % | -0.04 % | -43 px | -2 px |
| rational vs fisheye | -0.28 % | -0.21 % | -42 px | -2 px |

Capture recipe / notes:
- This session: board at a median 1.53 m (range 1.29-1.73 m); there the frame is 2.69 m wide, so the 560 mm pattern filled only 21% of the width.
- Closest corner to each frame edge: left 1348 px (29%), right 1635 px (35%), top 400 px (15%), bottom 1436 px (55%).
- Empty corner cells: top-left corner, top-right corner, bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~0.7-0.9 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- 9/50 frames failed on clipping (white squares at 255): lock exposure ~1 stop darker for the calibration block, or tilt the board out of the sun glint.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

