# Underwater calibration — pool1014_clipsens_NONPREREG

Pre-registered bars: `docs/pool_calib_prereg_20261014.md`. Pixels = native 4608×2592 sensor px unless labelled.

**NON-PREREG SENSITIVITY RUN** — screen thresholds overridden: {'clip_fail': 1.01, 'clip_warn': 1.01}

## bmcam003
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 120: screen {'PASS': 56, 'WARN': 40, 'FAIL': 24}, 62 distinct poses, 62 used after outlier rejection; board at 1.20–2.15 m (median 1.54 m)
- Model **standard** (lowest held-out RMS 0.438 px). Held-out RMS: standard 0.438 px, rational 0.455 px, fisheye 0.630 px; training RMS 0.373 px
- Full frame 4608×2592: fx 2639.0, fy 2650.4, cx 2310.4, cy 1208.3, dist [0.2651, 0.80534, -0.034025, 5.7839e-05, -0.92887]
- Production crop 1600×900 at [1504, 846, 1600, 900]: fx 2639.0, fy 2650.4, cx 806.4, cy 362.3 (same distortion)
- Underwater fx / in-air nominal (1964 px, datasheet assumption) = 1.343; FOV H 71.2 deg, V 48.8 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 62 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.438 | < 1.0 px (standard) |
| B3 coverage | FAIL | covered_frac 0.325, corner_cells top-left corner False, top-right corner False, bottom-left corner False, bottom-right corner False | >= 60% of 8x5 cells and all 4 corner cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px inf, worst_model_disagreement_px inf, literal_ray_px inf, literal_models_px inf | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at 4 frame corners + 4 edge midpoints (homography-aligned, amendment A1) |
| B5 stable | PASS | rel_std_f 0.00246, std_c_px 9.02 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px |
| B5c production crop | FAIL | worst_ray_uncertainty_px 2.29, worst_model_disagreement_px 3.92, literal_ray_px 20.8, literal_models_px 137 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (homography-aligned, amendment A1) |
| B6 focal sanity | PASS | 1.34 | fx / 1964 px (in-air nominal) in [1.25, 1.42] |

Capture recipe / notes:
- This session: board at a median 1.54 m (range 1.20-2.15 m); there the frame is 2.68 m wide, so the 560 mm pattern filled only 21% of the width.
- Closest corner to each frame edge: left 975 px (21%), right 1428 px (31%), top 707 px (27%), bottom 870 px (34%).
- Empty corner cells: top-left corner, top-right corner, bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~0.7-0.9 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

## bmcam004
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 120: screen {'PASS': 64, 'WARN': 40, 'FAIL': 16}, 70 distinct poses, 69 used after outlier rejection; board at 1.19–2.15 m (median 1.53 m)
- Model **standard** (lowest held-out RMS 0.383 px). Held-out RMS: standard 0.383 px, rational 0.422 px, fisheye 0.498 px; training RMS 0.366 px
- Full frame 4608×2592: fx 2641.3, fy 2646.1, cx 2301.4, cy 1215.7, dist [0.25239, 0.64459, -0.022996, -0.00089632, -0.59852]
- Production crop 1600×900 at [1504, 846, 1600, 900]: fx 2641.3, fy 2646.1, cx 797.4, cy 369.7 (same distortion)
- Underwater fx / in-air nominal (1964 px, datasheet assumption) = 1.345; FOV H 71.2 deg, V 49.0 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 69 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.383 | < 1.0 px (standard) |
| B3 coverage | FAIL | covered_frac 0.3, corner_cells top-left corner False, top-right corner False, bottom-left corner False, bottom-right corner False | >= 60% of 8x5 cells and all 4 corner cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px inf, worst_model_disagreement_px inf, literal_ray_px inf, literal_models_px inf | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at 4 frame corners + 4 edge midpoints (homography-aligned, amendment A1) |
| B5 stable | PASS | rel_std_f 0.00166, std_c_px 5.85 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px |
| B5c production crop | FAIL | worst_ray_uncertainty_px 0.231, worst_model_disagreement_px 1.6, literal_ray_px 12.9, literal_models_px 98.5 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (homography-aligned, amendment A1) |
| B6 focal sanity | PASS | 1.34 | fx / 1964 px (in-air nominal) in [1.25, 1.42] |

Capture recipe / notes:
- This session: board at a median 1.53 m (range 1.19-2.15 m); there the frame is 2.67 m wide, so the 560 mm pattern filled only 21% of the width.
- Closest corner to each frame edge: left 1201 px (26%), right 1242 px (27%), top 677 px (26%), bottom 895 px (35%).
- Empty corner cells: top-left corner, top-right corner, bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~0.7-0.9 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

## nereus002
- Verdict: full-frame intrinsics **FAIL**, production-crop intrinsics **FAIL**
- Frames 50: screen {'PASS': 14, 'WARN': 9, 'FAIL': 27}, 17 distinct poses, 17 used after outlier rejection; board at 1.21–1.80 m (median 1.57 m)
- Model **standard** (lowest held-out RMS 0.435 px). Held-out RMS: standard 0.435 px, rational 6.610 px, fisheye 1.050 px; training RMS 0.366 px
- Full frame 4608×2592: fx 2630.3, fy 2627.4, cx 2329.6, cy 1280.3, dist [0.24707, 0.84981, 0.00077235, 0.0099788, -0.80228]
- Production crop 1600×900 at [1504, 846, 1600, 900]: fx 2630.3, fy 2627.4, cx 825.6, cy 434.3 (same distortion)
- Underwater fx / in-air nominal (1964 px, datasheet assumption) = 1.339; FOV H 70.6 deg, V 49.0 deg (from the edge-midpoint rays)

| bar | status | value | rule |
|---|---|---|---|
| B1 poses | PASS | 17 | >= 15 distinct usable poses |
| B2 held-out RMS | PASS | 0.435 | < 1.0 px (standard) |
| B3 coverage | FAIL | covered_frac 0.275, corner_cells top-left corner False, top-right corner False, bottom-left corner False, bottom-right corner False | >= 60% of 8x5 cells and all 4 corner cells |
| B4 edges constrained | FAIL | worst_ray_uncertainty_px inf, worst_model_disagreement_px inf, literal_ray_px inf, literal_models_px inf | ray uncertainty <= 2.0 px and model disagreement <= 2.0 px at 4 frame corners + 4 edge midpoints (homography-aligned, amendment A1) |
| B5 stable | PASS | rel_std_f 0.00236, std_c_px 7.06 | bootstrap std(f)/f <= 0.5% and std(cx, cy) <= 10.0 px |
| B5c production crop | FAIL | worst_ray_uncertainty_px 6.2, worst_model_disagreement_px 1.91, literal_ray_px 18.7, literal_models_px 48.8 | ray uncertainty <= 0.5 px and model disagreement <= 1.0 px at the 4 crop corners (homography-aligned, amendment A1) |
| B6 focal sanity | PASS | 1.34 | fx / 1964 px (in-air nominal) in [1.25, 1.42] |

Capture recipe / notes:
- This session: board at a median 1.57 m (range 1.21-1.80 m); there the frame is 2.75 m wide, so the 560 mm pattern filled only 20% of the width.
- Closest corner to each frame edge: left 623 px (14%), right 1635 px (35%), top 269 px (10%), bottom 1436 px (55%).
- Empty corner cells: top-left corner, top-right corner, bottom-left corner, bottom-right corner.
- Next dive: hold the board at ~0.7-0.9 m (pattern ~35-45% of the frame width) and put it in 9 places: the 4 corners, the 4 edge midpoints and the centre, with a board corner touching the frame corner/edge each time.
- At each place take 3 poses: flat, tilted ~30 deg left/right, tilted ~30 deg up/down (~27 poses), plus 3-5 flat poses at 1.5 m. Hold still for 2 frames at each pose (10 s cadence is fine).
- Frame with a FULL-FRAME preview: the pool preview showed only the 1600x900 centre crop, so the frame edges were invisible while posing.
- Keep the production lens position (0.8) for calibration frames: focus changes the focal length slightly, so calibrate at the focus you ship.

