# Underwater camera calibration: pre-registered quality bars (pool 2026-10-14)

Written and committed **before** any calibration was run on the 10/14 pool frames.
The pipeline (`tools/pool_calib_pipeline.py`) carries these exact numbers as defaults and
prints them at the top of every run; the verdict is computed from them, not chosen afterwards.

All pixel numbers are **native sensor pixels of the full 4608×2592 frame** unless labelled
otherwise. The production still crop is `[1504, 846, 1600, 900]` (x, y, w, h, native px,
pure pixel crop, no scaling).

## Data (from TE2 + the nereus002 session, 2026-10-14)

| camera | frames | port | notes |
|---|---|---|---|
| bmcam003 | 120 JPEG + sidecar, `~/NereusPool/20261014/bmcam003_20261014_175926/calibration/` | flat acrylic | 1:18–1:38 PM PDT, 10 s cadence, LensPosition 0.8 |
| bmcam004 | 120 JPEG + sidecar, `~/NereusPool/20261014/bmcam004_20261014_190821/calibration/` | flat acrylic | same window |
| nereus002 | 18 sets × 2 JPEG (raw-pair + auto still), `…/nereus002/results/pool/pool007_20261014T194116Z/` | **unknown** (ask Nick) | strong circular port vignette: black corners in every frame |

No DNGs on the Mac (still on the units). JPEG = Pi ISP output, same pixel grid as the DNG.
Board: calib.io CCT600400C, ChArUco 9×14, 40 mm checker, 30 mm marker, DICT_5X5.
Board distance was not logged per pose; TE2 says ~1–1.5 m, and the live preview Nick framed
with showed **only the 1600×900 centre crop**.

**Stated prediction (before running):** Nick suspects he was too far away for edge distortion
to show; the centre-crop preview points the same way. If true, bars B3 and B4 FAIL (no corner
cells, unconstrained edges) while the production crop (B5c) can still PASS.

## Per-frame screen (stage 1)

| check | FAIL | WARN |
|---|---|---|
| decode | unreadable | – |
| ChArUco corners | < 12, or collinear | < 30 |
| board blur: Laplacian variance on the board ROI ÷ ROI variance (contrast-normalised) | < 0.25 × set median | < 0.5 × set median |
| exposure: board-ROI pixels at ≥ 250 (8-bit gray) | > 10 % | > 1 % |
| exposure: board-ROI mean | – | < 40 or > 220 |
| motion: blur anisotropy along the two board axes (p95 gradient ratio) | > 2.5 | > 1.5 |
| near-duplicate pose (< 5° rotation **and** < 10 % distance from a kept pose) | – | WARN, excluded from the set (keeps the frame with more corners) |
| board coverage of the frame (hull area) | – | reported only |

Thresholds marked "× set median" are relative because absolute Laplacian values depend on
scene and scale; they are heuristics, labelled as such.

## Calibration (stage 3)

- Models: OpenCV standard 5-coeff (k1 k2 p1 p2 k3), rational 8-coeff (k1..k6 p1 p2),
  fisheye (k1..k4).
- Outlier rejection: drop views whose per-view RMS > max(3 × median, median + 5 × 1.4826 × MAD),
  refit, up to 3 rounds (chosen per model on the training data).
- **Selection by held-out error**: 5-fold, folds by view (fixed seed 0). Train on 4/5 of the
  views; on each held-out view fit only the board pose (solvePnP with the trained intrinsics)
  and measure the reprojection RMS. Lowest held-out RMS wins; within 2 % of the best, prefer
  standard > fisheye > rational (standard is what `cv2.undistort` / our tools use).
- Uncertainty: bootstrap, B = 100 resamples of views with replacement (seed 0), chosen model;
  plus OpenCV's `stdDeviationsIntrinsics`.
- "Ray uncertainty at pixel p" = spread of the undistorted ray for pixel p across bootstrap
  replicates, expressed in pixels at the median focal length (95th percentile distance from
  the median ray). "Model disagreement at p" = the same distance between the three models'
  rays (max pairwise).

## Pass bars (per camera)

| bar | rule |
|---|---|
| **B1 poses** | ≥ 15 distinct usable poses in the calibration set |
| **B2 held-out RMS** | chosen model's 5-fold held-out RMS < 1.0 px |
| **B3 coverage** | ≥ 60 % of an 8×5 grid over the full frame covered (cell = ≥ 5 used corner observations) **and** all 4 corner cells covered |
| **B4 edges constrained** (Nick's hypothesis) | at all 8 probes (4 frame corners + 4 edge midpoints): bootstrap ray uncertainty ≤ 2 px **and** model disagreement ≤ 2 px |
| **B5 stable** | bootstrap std(fx)/fx and std(fy)/fy ≤ 0.5 %, std(cx), std(cy) ≤ 10 px |
| **B5c production crop** | ray uncertainty ≤ 0.5 px and model disagreement ≤ 1 px at the 4 corners of the 1600×900 crop |
| B6 sanity (WARN only) | underwater fx ÷ in-air nominal fx in [1.25, 1.42]; nominal fx = 2.75 mm / 1.4 µm = 1964 px (Raspberry Pi Camera Module 3 Wide datasheet values; **assumption**, no in-air ChArUco set exists: the 10/12 "calibration" frames contain no board) |

Verdicts:
- **Full-frame intrinsics: PASS** only if B1–B5 all pass.
- **Production-crop intrinsics: PASS** if B1, B2, B5, B5c pass (the crop does not need the
  frame corners).
- Any FAIL → the report says which bar, and gives the capture recipe for the next dive instead
  of a number to trust.
