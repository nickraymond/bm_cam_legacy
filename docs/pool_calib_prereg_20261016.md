# Underwater calibration, pool049 re-dive (Fri 2026-10-16): pre-registration amendment

Written and committed **before** any calibration was run on the pool049 frames (commit c3356f49, Sat 2026-10-17
3:21 PM PT; the QE 004 conditions C1 + C2 were added in a docs-only commit, also before any run).
It amends `docs/pool_calib_prereg_20261014.md` (the 10/14 bars, amendment A1 and the A2 note). Everything there
still holds unless a line below changes it. Spec: `~/NereusData/ops/pe_specs/SPEC_lens_calib_pool049.md` rev 2
(sha256 `da245f786fad35b3b465e2d52a7a43a2a337bc945d779a2c26224d810fb01b55`, QE 004 spec PASS Sat 2:57 PM). Board card:
`c_charuco` (proposal `prop_lens_calib_pool049`, approved by Nick Sat 1:59 PM).

Before this commit nothing was calibrated. What was looked at: thumbnails of every frame (to see the port vignette and
where the board is), brightness profiles of a few IMX708 frames, the first cal_a DNG's decode size, and the input
sha256s. No board was detected, no corner counted, and no camera model was fitted on pool049.

## Data

Run `~/NereusPool/20261016/nereus002/pool049_20261016T212527Z/` (read-only). `poses.csv` lists 78 calibration poses,
and every pose has a frame from all three cameras:

| block | poses | board distance (dive plan) |
|---|---|---|
| cal_a | 41 (pose 1–41) | 0.7–0.9 m |
| cal_b | 37 (pose 42–78) | 1.5–2.0 m |

| camera | input (explicit list of the 78 `poses.csv` files) | frame | port |
|---|---|---|---|
| IMX708 (Camera Module 3 Wide), **primary** | `captures/imx708/*.dng` (DNG; never a glob that can reach the `.jpg` siblings) | 4608×2592 | flat, circular vignette |
| IMX708, **cross-check only** | `captures/imx708/*.jpg` (Pi ISP output) | 4608×2592 | same |
| OpenMV N6 (PAG7936) | `captures/openmv_n6/*.bayer` + `<stem>.json` | 1280×800 | flat |
| OpenMV AE3 (PAG7936) | `captures/openmv_ae3/*.bayer` + `<stem>.json` | 1280×800 | flat |

The pipeline reads each camera from a folder of symlinks to exactly those 78 files (plus their sidecars), under
`~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/inputs/`. All 312 input files match the rig's per-experiment
`SHA256SUMS` (78/78 for each of the four inputs, checked before this commit). The first cal_a DNG decodes to
**4608×2592** (rawpy 0.27.1, LibRaw 0.22.1), as rev 2 requires.

Board: calib.io CCT600400C, the single board Nick has (same as 10/14). Detected on 10/14 as 14×9 squares, modern
layout, 40/30 mm, DICT_5X5_100. **Pinned** here (`--layout modern --squares-x 14 --squares-y 9`), because the
auto-layout step skips DNGs and would have no sample on a DNG-only run.

v3-card frame for the before/after (`--card`): stills set 89 (`exp_20261016T215216Z_pool`), the first stills set
in which the card is fully inside all three frames, chosen by eye on thumbnails before this commit. It is not a
calibration frame.

## What is unchanged from 10/14

The stage-1 screen thresholds, the three models, outlier rejection, 5-fold held-out model selection (seed 0), the
bootstrap (B = 100, seed 0), amendment A1 (bootstrap ray spread measured after homography alignment), and every
numeric bar B1–B6.

## Amendments (each is set here, before any run)

### 1. Model disagreement is read literally (metric) — Nick's d33

Nick, Thu 10/15 ~10:19 PM (his board comment on d33): "Go with literal because I'd like to be able to quantify
urchins per square meter." So the **model-disagreement half of B4 and B5c passes or fails on the literal (metric)
value**: the max pairwise distance between the three models' undistorted rays, with nothing aligned away. The
ray-uncertainty half stays on the A1 aligned value, as QE accepted on #176. Both readings are still reported in
every bar row. Tool: `--models-reading literal`.

### 2. IMX708 port mask (Nick: mask the dark corners)

The rule, fixed before any calibration:

- **Frame.** The first cal_a frame by file name: `imx708_raw_20261016T212533Z.dng` (pose 1, a hand in front of the
  camera; DNG sha256 `f05b90b0…6eb4d`, as in the rig's SHA256SUMS).
- **Decode.** The pipeline's own DNG loader: rawpy `postprocess(use_camera_wb=True, output_bps=8)`, then gray.
- **Threshold.** A pixel is port if its 51×51 box mean is below **0.5 × the median of the central 50 % × 50 %**
  of the frame. The rule runs on rawpy's gamma-encoded 8-bit output (BT.709 curve, about 2.2), so 0.5× is about
  **−2.2 stops linear** against the centre (QE 004, C1).
- **Keep.** Only dark regions that touch the frame border (8-connected).
- **Encode.** PNG with 255 = visible and 0 = masked.
- **Script.** `~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/mask/draw_mask.py`. It is the rule above as code,
  run once from the repo root.

**Finding behind the threshold.** The port region in these frames is **not black**. It is a grey, detail-free haze
at about 35–50 % of the centre brightness in the 8-bit decode, which is about −2.2 to −3.3 stops linear. It has a
soft edge. The thumbnails make it look black.
Pose 1 is also brighter and washed out compared with the board frames. So the spec's "thresholded on the black port
region" is applied at 0.5× the central median of the decode (about −2.2 stops linear), and the mask comes out
smaller than the haze looks
in the board frames. The mask was not tuned after this. It was drawn once, with the rule above.

Result: **4.40 % of pixels masked**, in the four corners.

- `imx708_port_mask.png` sha256 `599d1ab04b95f9dc0bf454abe69beb5b385876bca21aa4ae7936fc318859a3dc`
- pixel-array sha256 `6d1b58ce332f7366b66703600c75c4063b4ff61cf034c3be31016327ae2c2b7f`
- Both are also in the run folder's MANIFEST.

How the mask is used (`--mask imx708=PNG`, for both the DNG run and the JPEG cross-check):

- **Corners.** A ChArUco corner on a masked pixel is dropped at detection and counts nowhere: not in the screen's
  corner count, not in the fit, not in coverage.
- **B3.** Scored on the 8×5 cells that are **≥ 50 % visible**. Each cell's visible fraction (tool's own cell
  assignment, rows top to bottom):

  ```
  0.493 1 1 1 1 1 1 0.426
  0.856 1 1 1 1 1 1 0.862
  0.999 1 1 1 1 1 1 1
  0.974 1 1 1 1 1 1 1
  0.768 1 1 1 1 1 1 0.864
  ```

  **38 of 40 cells count.** The two top corner cells (0.49, 0.43 visible) are out.
- **The 4 extreme visible cells** replace the 4 corner cells. For each frame corner, that is the counting cell whose
  centre is nearest to it (ties go to the smaller row, then the smaller column), in (row, column):
  - top-left (1, 0)
  - top-right (1, 7)
  - bottom-left (4, 0)
  - bottom-right (4, 7)
- **B4 probes.** A probe on a masked pixel moves to the nearest visible pixel. Native px:

  | probe | position |
  |---|---|
  | TL | (325, 182) |
  | TR | (4270, 235) |
  | BL | (194, 2507) |
  | BR | (4455, 2529) |
  | top, bottom, left, right edge midpoints | unchanged; they are visible |

- **B5c.** The production-crop corners `[1504, 846, 1600, 900]` are all visible and don't move.
- **Report-only B3 on the 10/14 definition (QE 004, C2).** All 40 cells, with the 4 true corner cells, on the same
  (visible-only) observations. It is computed from the run's `coverage.json` (`covered`, all 40 cells, and cells
  (0,0), (0,7), (4,0), (4,7)). The count of corners the mask dropped is also reported (`masked_corners` in
  `frames.csv`). The **verdict stays on the masked rule** above. This line exists because the two top corner
  cells sit just under 50 % visible (0.493, 0.426), and the threshold was set after viewing thumbnails, so any
  mask effect on B3 has to be visible (spec F2).

N6 and AE3 get no mask. Their frames show no port vignette.

### 3. N6 and AE3 bars (Nick: "Same bars, own pixels")

- **B1–B5.** The 10/14 numbers, applied unchanged in each sensor's own 1280×800 pixels: B3 on an 8×5 grid of the full
  frame, with all 4 true corner cells.
- **B5c.** Uses the full frame, because these boards have no production crop. The "crop" is `[0, 0, 1280, 800]`, so B5c is
  ray uncertainty ≤ 0.5 px (aligned) and model disagreement ≤ 1 px (literal) at the 4 frame corners. Tool:
  `--native-size 1280x800 --crop 0,0,1280,800`. That is a separate run from the IMX708; it labels these bars as
  pre-registered, which they now are.
- **Production-crop verdict.** B1, B2, B5 and B5c (full frame).
- **Recipe lines.** Because the run uses `--native-size 1280x800`, its capture-recipe lines may mention the bmcam
  preview and LensPosition. That is wording only; RESULTS will say so.

### 4. B6 in-air nominal focal length from the datasheets (WARN only, as before)

| camera | lens (datasheet) | pixel | nominal fx at the frame used | source |
|---|---|---|---|---|
| IMX708 | 2.75 mm | 1.4 µm | **1964 px** at 4608×2592 | Raspberry Pi Camera Module 3 Wide datasheet (already in the tool) |
| N6 | 2.8 mm, M12, F2.0, TV distortion < −24 % | 3 µm | **933.3 px** at 1280×800 (full sensor) | https://openmv.io/products/openmv-n6 |
| AE3 | 3.1 mm ± 5 %, M8, F2.3, TV distortion < −2.5 % | 3 µm | **1033.3 px** at 1280×800 (full sensor) | https://openmv.io/products/openmv-ae3 |

Both OpenMV pages were read Sat 10/17 ~3:00 PM PT. Tool: `--nominal-fx n6=933.3 --nominal-fx ae3=1033.3`.

**Lens record.**

- **Profiles.** The rig profiles (`profiles_cal/*.yaml`) don't name the lens.
- **Rig notes.** nereus-camera-test-rig `docs/open_questions.md` OQ-43 (2026-09-28) lists the stock lenses as fitted,
  and nothing since records a lens change.
- **AE3.** Its lens was refocused on 10/15. That is a focus change, not a lens change, so B6 applies to it.

If Nick says either lens is not the stock lens, that camera's B6 reads "N/A: fitted lens differs from the datasheet".
That is not a fail.

**Known risk, stated before the run.** The 10/14 N6 fit (`20261014_205741_pool1014_n6_bayer`, 9 views, central
only) gave fx ≈ 1613 px underwater. That is 1.73× the 933 px datasheet value, not ~1.33×, so the N6's B6 may WARN.
Per the spec (F3), the lens record decides; a B6 WARN changes no verdict.

### 5. Usable poses and stop-early lines (spec rev 2)

- **Usable poses.** A camera's usable poses are the distinct poses in its calibration set after the stage-1 screen
  and near-duplicate removal (`screen.distinct_set` in summary.json).
- **< 15 usable poses.** If a camera has fewer, that camera is **stopped**. Its numbers are reported as context, no
  calibration JSON ships for it, and the session says so on its card and to the Principal Engineer and the PjM.
- **IMX708 B3 fails.** The missing cells are reported, and nothing is refitted, re-masked or re-screened to rescue
  it.
- **Other stops.** The session also stops if the Mac has less than 8 GiB free, or if any step would need a rig
  command (none does).
- **One run per input.** No input gets a second calibration run. A crash re-runs the same command unchanged, and the
  log says so.

### 6. JPEG cross-check bar (R3)

- **What is compared.** The IMX708 JPEG run (same mask, same flags) against the DNG run: chosen model vs chosen model.
- **Bar.**
  - |Δfx|/fx ≤ 0.5 %
  - |Δcx| and |Δcy| ≤ 5 px
  - the literal ray difference at the 8 B4 probes of the DNG run ≤ 1 px, in px at the DNG run's f_ref
- **Tool.** `tools/pool_calib_crosscheck.py <dng run>/imx708 <jpg run>/imx708`.
- **Outcome.** A JPEG result never changes a DNG verdict. A disagreement is reported as a finding (an ISP effect),
  and the DNG calibration JSON ships.

### 7. Tilt and decentre flags (R4): reported as flags, not bars

A flag is raised for a camera if any of these holds:

1. |cx − W/2| > 2 % of W, or |cy − H/2| > 2 % of H, using the chosen model's cx, cy;
2. |p1| or |p2| > 1e-3, always from the **standard 5-coefficient fit**, whichever model wins;
3. held-out RMS in the outer zone > 2× the held-out RMS in the inner zone.

The zones for flag 3:

- r = a corner's distance from the frame centre ((W−1)/2, (H−1)/2), divided by the half-diagonal, so a frame corner is 1.0.
- The inner zone is r ≤ 0.5; the outer zone is r > 0.8.
- Held-out residuals are per corner, each view scored by the fold model that did not see it: the same scoring as B2.
- The ratio is "not testable" if either zone has fewer than 30 residuals.

A raised flag means a follow-up card for an explicit flat-port (refractive) model. Nothing like that gets built in
this ticket.

### 8. Residuals vs radius and vs distance, and the cross-distance check (R5): report-only

- **Report.** Held-out RMS per radius bin (r = 0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0) and per board-distance bin
  (0, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 5.0 m). Each view's distance is the board centre's distance under its
  held-out pose.
- **Cross-distance check.** The chosen model is refitted on one block's calibration views (cal_a or cal_b, from
  `poses.csv`, with outlier rejection on that block only) and scored pose-only on the other block, both ways. Each
  result is set next to the other block's own 5-fold held-out RMS. A block with fewer than 8 calibration views is
  "not testable". Tool: `--groups NAME=CSV`.
- **Validated range.** Each camera's validated range is the board-distance range of its calibration set.
- **Limit.** If one block contributes few usable poses for a camera (for example, the board is outside the N6 or AE3
  view in many cal_b poses), distance dependence can't be tested for that camera, and RESULTS says so.

### 9. Verdict per camera

- **PASS** = B1–B5 pass, as amended above (full frame). B6 is a WARN-only sanity check.
- **Production-crop verdict** = B1, B2, B5 and B5c.
- **Minimum useful result** = IMX708 B5c PASS.

## Code that computes each number (one PR with this doc)

| number | where |
|---|---|
| screen, fit, B1–B6 | `tools/pool_calib_pipeline.py` (#176–#179) |
| mask: drop corners, B3 cells, B4 probes | `--mask` (new; `load_mask`, `visible_at`, `cell_visible_fraction`, `extreme_visible_cells`, `nearest_visible`) |
| literal reading of B4/B5c | `--models-reading literal` (new; `evaluate_bars(models_reading=…)`) |
| residuals vs radius / distance, flags | new: `heldout_residuals`, `residual_report`, `tilt_decentre_flags` → `residuals.json`, `residuals_vs_radius.csv` |
| cross-distance check | `--groups` (new; `cross_group_check`) |
| JPEG cross-check | `tools/pool_calib_crosscheck.py` (new) |

Unit tests: `tests/test_pool_calib_pipeline.py` (`TestPrereg20261016Units`, `TestMaskRun`) and
`tests/test_pool_calib_crosscheck.py`.

## Commands (run only after QE confirms this commit and the PjM's heavy-compute all-clear)

`nice -n 10`, `--workers 2` (PjM 003's limits). Outputs go under `~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/`,
and the run stops if the Mac has less than 8 GiB free.

```bash
D=~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib; IN=$D/inputs
P=~/NereusPool/20261016/nereus002/pool049_20261016T212527Z/2026-10-16/exp_20261016T215216Z_pool/captures
COMMON="--layout modern --squares-x 14 --squares-y 9 --models-reading literal --workers 2 --out-root $D"
# 1. IMX708, DNG (primary)
nice -n 10 .venv-dev/bin/python tools/pool_calib_pipeline.py imx708=$IN/imx708_dng $COMMON \
  --mask imx708=$D/mask/imx708_port_mask.png --groups imx708=$IN/groups_imx708_dng.csv \
  --card imx708=$P/imx708/imx708_raw_20261016T215216Z.dng --tag pool049_imx708_dng
# 2. N6 + AE3, raw Bayer
nice -n 10 .venv-dev/bin/python tools/pool_calib_pipeline.py n6=$IN/n6 ae3=$IN/ae3 $COMMON \
  --native-size 1280x800 --crop 0,0,1280,800 --nominal-fx n6=933.3 --nominal-fx ae3=1033.3 \
  --groups n6=$IN/groups_n6.csv --groups ae3=$IN/groups_ae3.csv \
  --card n6=$P/openmv_n6/openmv_n6_raw_20261016T215216Z.bayer \
  --card ae3=$P/openmv_ae3/openmv_ae3_raw_20261016T215216Z.bayer --tag pool049_openmv_raw
# 3. IMX708, JPEG (cross-check only)
nice -n 10 .venv-dev/bin/python tools/pool_calib_pipeline.py imx708=$IN/imx708_jpg $COMMON \
  --mask imx708=$D/mask/imx708_port_mask.png --groups imx708=$IN/groups_imx708_jpg.csv \
  --card imx708=$P/imx708/imx708_raw_20261016T215216Z.jpg --tag pool049_imx708_jpg_check
.venv-dev/bin/python tools/pool_calib_crosscheck.py $D/<run 1>/imx708 $D/<run 3>/imx708
```

Software: Python 3.13.5, opencv-contrib-python 5.0.0.93, numpy 2.5.1, scipy 1.18.1, matplotlib 3.11.1 (the same
pins as the repo's main `.venv`), plus **rawpy 0.27.1** (LibRaw 0.22.1), installed only in this worktree's
`.venv-dev`, not in the shared `.venv`.
