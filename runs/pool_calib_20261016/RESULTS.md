# Underwater lens calibration, pool049 re-dive (Fri 2026-10-16): results

> **QE status.** The tool code and these results passed QE: QE 006, bm #184 @ `f023448b`, Sun 10/18 9:30 AM PT. That
> followed QE 006's FAIL @ `150bbe37` (9:13 AM: 2 required, both mechanical, no record verdict change), fixed in
> `3552ea02` + `f023448b`. **The verdicts still FAIL on all three cameras**, so every calibration here is NOT VALIDATED
> and no lens correction is applied to any camera.
>
> The calibration JSONs' `status` field still reads "PROVISIONAL until QE passes the tool code". It was written before
> this PASS and is left as exported so the files stay byte-checkable; this line supersedes it. Their `use` field
> ("NOT VALIDATED") is the one that governs.

Pre-registration: `docs/pool_calib_prereg_20261016.md`.

- Commit `c3356f49`: QE 004 PASS-with-conditions.
- Commit `dda46166`: the conditions C1 + C2, docs only. QE 005 byte-check PASS.
- Both prereg commits predate the first run folder, created Sat 10/17 3:27 PM PT.

Spec: `SPEC_lens_calib_pool049.md` rev 2 (`da245f78…`) for the runs. Rev 3 (`54855f5a…`, PE 002, Sat 5:07 PM) adds the
report and the answer to Nick's question 1; it is committed byte-identical as `docs/specs/SPEC_lens_calib_pool049.md`. Ticket: `c_charuco`. All runs used tool commit `dda46166`
(with `8e98892e` beneath it), `nice -n 10`, 2 workers. The exact commands are in the prereg doc. Full run folders
(images, CSVs, logs) are on Nick's Mac only, in `~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/`:

| run | folder | seconds | exit |
|---|---|---|---|
| 1 IMX708 DNG (primary) | `20261017_152713_pool049_imx708_dng/` | 65 | 3 (bars fail) |
| 2 N6 + AE3 raw Bayer | `20261017_153029_pool049_openmv_raw/` | 19 | 3 |
| 3 IMX708 JPEG (check only) | `20261017_153048_pool049_imx708_jpg_check/` | 50 | 3 |

A first launch of run 1 at 3:26:53 PM was refused at argument parsing: zsh does not split `$COMMON`. No frame was
read and no folder was made. Every run then went through `run_prereg_commands.sh` (bash) in the data folder,
unchanged.

This folder keeps:

- each run's `summary.md`, `run_manifest.json` (the sha256 of every input) and `prereg_bars.json`;
- per camera: `coverage.json`, `models.json`, `residuals.json`, `residuals_vs_radius.csv`, `before_after.json` and the
  intrinsics;
- the DNG-vs-JPEG `crosscheck.*`;
- one `calibration_<camera>_flatport_20261016.json` per camera. Each is marked **NOT VALIDATED**, because the bars
  fail.

## Verdict: all three cameras FAIL, for one shared reason

None of the three cameras had a single board corner in the **bottom ~40 % of its frame**. The rig sits low, so the
lower part of every view is pool floor closer than the board was held. That fails coverage (B3) on every camera.
Without data down there, the frame edges (B4) and the bottom corners of the production crop (B5c) aren't pinned
down either.

The fit is good where the board was: held-out error 0.29–0.50 px, and it is stable.

| camera | model (held-out pick) | fx / fy (px) | cx, cy | held-out RMS | poses | B1 | B2 | B3 | B4 | B5 | B5c | B6 | full / crop |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| IMX708 (DNG) | rational | 2628.3 / 2628.0 | 2313.9, 1294.2 | 0.504 | 57 | PASS | PASS | **FAIL** 50 % of 38, 0/4 | **FAIL** ∞ | PASS | **FAIL** | 1.338 | FAIL / FAIL |
| N6 (raw) | standard | 1628.9 / 1630.0 | 637.4, 490.4 | 0.295 | 25 | PASS | PASS | **FAIL** 67.5 %, 2/4 | **FAIL** | PASS | **FAIL** | **WARN 1.745** | FAIL / FAIL |
| AE3 (raw) | standard | 1394.2 / 1393.3 | 677.4, 375.1 | 0.290 | 25 | PASS | PASS | **FAIL** 60 %, 2/4 | **FAIL** | PASS | **FAIL** | 1.349 | FAIL / FAIL |

Pixels are each camera's own full frame (IMX708 4608×2592; N6/AE3 1280×800). The bars are the 10/14 numbers. B4/B5c
model disagreement is scored on the **literal** (metric) reading, per Nick's d33. "Poses" means the views used after
outlier rejection (the N6 dropped 1 of its 26 distinct poses).

### Stop-early record

At 3:29 PM the IMX708 hit the pre-registered stop line "B3 still fails". Nothing was refitted, re-masked or
re-screened. PE 002 then approved, at 3:30 PM, the other two pre-registered runs (N6 + AE3; the JPEG check), on these
grounds:

- that stop line stops IMX708 work only;
- a JPEG result never changes a DNG verdict.

No camera fell under the 15-usable-pose stop line (IMX708 57, N6 26, AE3 25). The disk never went under 8 GiB
(93 GiB free). No step touched the rig.

## The missing cells (8×5 grid; rows top → bottom, columns left → right)

Corner observations per cell. A cell counts as covered at ≥ 5 observations.

IMX708 (DNG). The two top corner cells don't count, because they are < 50 % visible:

```
  0   25  198  262  352  242   41    0
  0   36  225  432  420  321  128    2
  0   33   98  175  267  261  115    7
  0    0    0    0    0    0    0    0
  0    0    0    0    0    0    0    0
```

- **Missing** (row, col): all of rows 3–4 (16 cells), plus (1,0), (2,0) and (1,7). None of the 4 extreme visible
  cells is covered: (1,0), (1,7), (4,0) and (4,7).
- No corner lies below y = 1549 of 2592. Radial reach is 0.71 of the half-diagonal.
- The 4 extreme visible cells are the "corner cells" under the mask rule, because the two top corner cells are under
  50 % visible.

N6:

```
 29  45  55  68  54  45  42  29
 24  38  46  87  66  82  78  48
 19  32  42  74  51  56  80  59
  0   0   0   2   2   5   6   5
  0   0   0   0   0   0   0   0
```

- **Missing**: (3,0)–(3,4) and all of row 4 (13 cells), including both bottom corners.
- Bottom gap: 293 of 800 px.

AE3:

```
 10  39  47  54  76  53  29   8
 19  36  49  67 119  93  58  22
  9  25  29  52  76  71  65  21
  0   0   0   0   0   0   0   0
  0   0   0   0   0   0   0   0
```

- **Missing**: all of rows 3–4 (16 cells), including both bottom corners.
- Bottom gap: 322 of 800 px.

**Report-only B3 on the 10/14 definition** (QE 004 C2: all 40 cells, 4 true corners):

| camera | covered | corner cells |
|---|---|---|
| IMX708 DNG | 19/40 (47.5 %) | 0/4 |
| N6 | 27/40 | 2/4 |
| AE3 | 24/40 | 2/4 |
| IMX708 JPEG | 20/40 | 0/4 |

The verdict doesn't depend on the mask: the IMX708 fails either way.

## The IMX708 port mask (Nick: "mask the dark corners")

- **What the port region is.** Grey, detail-free haze at 35–50 % of the centre brightness in the 8-bit decode, about
  −2.2 to −3.3 stops linear. It is not black.
- **The mask.** Pre-registered at 0.5× the central median (about −2.2 stops), from pose 1. It masks 4.40 % of the
  frame (sha256 `599d1ab0…`).
- **Effect on observations: none.** It dropped **0** corner observations in all 78 frames: no board corner was ever
  detected in the masked region.
- **Effect on scoring.** It changes only which cells count for B3 (38 of 40) and where the four corner probes of B4
  sit.

## What the data say

**Focal length behind the flat port.**

- IMX708: fx is 1.338× the in-air datasheet value (2628 vs 1964 px). The flat-port prediction is 1.33×, matching
  10/14.
- AE3: 1.349× (1394 vs 1033 px).
- N6: **1.745×** (1629 vs 933 px). That is far outside a flat-port ratio, which points to a lens that is not the
  stock 2.8 mm: 1629 / 1.33 ≈ 1225 px in air, about 3.7 mm at 3 µm.
- The N6 lens record is **pending** (Nick). If it isn't the stock lens, the N6's B6 reads "N/A: fitted lens differs".
  B6 is WARN-only and changes no verdict.

**Where the board was.** All three cameras' poses sat at almost the same distances, and not at the planned ones
(0.7–0.9 m for cal_a, 1.5–2.0 m for cal_b):

| camera | board distances | median | views beyond 1.1 m |
|---|---|---|---|
| IMX708 | 0.85–1.87 m | 0.98 m | only 6 |
| N6 | 0.84–1.06 m | — | — |
| AE3 | 0.85–1.01 m | — | — |

- On the N6 and AE3, the far cal_b poses either failed the screen or had the board out of view.
- Screen FAILs were almost all frames with no board, or under 12 corners because the board was mostly out of
  view: IMX708 2 + 9 (cal_a + cal_b), N6 22 + 22, AE3 20 + 25 (one AE3 frame was blurred). The narrower N6 and AE3
  views lost the board in many poses.
- **Validated distance range** (the range of each calibration set): IMX708 0.85–1.87 m (mostly ~1 m), N6 0.84–1.06 m,
  AE3 0.85–1.01 m.

**Model choice.**

- IMX708: held-out RMS standard 0.548 / rational 0.504 / fisheye 0.609 px, so rational is chosen.
- N6 and AE3: the three models are within 2 %, so standard is chosen by the tie rule.
- In the coverage gap the IMX708's rational fit **folds** before the side and bottom edges. That is why its B4 is ∞:
  the model can't be inverted there.

**Literal model disagreement: a finding Nick needs for the d33 bar.** Even where the board was dense, the three model
families disagree by about 10–25 px on the literal reading, while the aligned (shape-only) values stay small:

| camera | probe | literal | aligned |
|---|---|---|---|
| N6 | top edge | 20.8 px | 0.08 px |
| IMX708 | top edge | 20.2 px | 0.08 px |
| AE3 | top edge | 9.1 px | 0.04 px |

- Nearly all of it is the **fisheye** model placing the image centre elsewhere: 12–16 px in cx and cy on the IMX708 and
  N6, 4–8 px on the AE3. A shift of the image centre largely turns every ray by the same small angle.
- So coverage is **necessary but may not be enough** to pass the literal ≤ 1 px (B5c) and ≤ 2 px (B4) bars between
  three model families. PE 002 has stated this in decision d58. Nothing here changes the bar.

**Residuals vs radius** (held-out per corner; r = distance from the frame centre over the half-diagonal):

| camera | r < 0.2 | 0.2–0.4 | 0.4–0.5 | 0.5–0.6 | 0.6–0.7 | 0.7–0.8 | 0.8–0.9 |
|---|---|---|---|---|---|---|---|
| IMX708 DNG | 0.40 | 0.44 | 0.56 | 0.67 | 0.81 | 1.07 (n 3) | — |
| N6 | 0.29 | 0.25 | 0.26 | 0.37 | 0.28 | 0.35 | 0.31 (n 33) |
| AE3 | 0.38 | 0.22 | 0.25 | 0.26 | 0.41 | 0.35 | 0.45 (n 4) |

- **IMX708.** The residuals roughly double from the centre to r 0.7. That is what refraction at a flat port, or a pinhole
  model straining at a wide field, looks like. The pre-registered flag (outer r > 0.8 vs inner) is **not testable**: no
  corner reached r > 0.8.
- **N6.** Flat with radius. Outer / inner = 1.22, under the 2× flag.
- **AE3.** Only 4 corners beyond r 0.8, so not testable.

**Tilt / decentre flags** (report-only; thresholds in the prereg §7):

| camera | principal point offset | standard-fit p1 / p2 | outer / inner |
|---|---|---|---|
| IMX708 | +10 / −2 px, no flag | 0.0043 / 0.0053, **flag** | not testable |
| N6 | −3 / **+90 px (11 % of H)**, **flag** | 0.0027 / 0.0012, **flag** | 1.22, no flag |
| AE3 | **+37 / −25 px**, **flag** | 0.0043 / −0.0023, **flag** | not testable |

- With only the top 60 % of every frame observed, the vertical centre (cy) and p1/p2 trade off against each other. So
  these flags are **not yet evidence** of a tilted or off-centre port.
- Re-read them after the lower-half poses. If they persist, a follow-up card for an explicit flat-port model is the
  next step (spec rev 1).

**Cross-distance check** (R5, report-only; refit on one block, scored on the other):

| camera | cal_a → cal_b | cal_b → cal_a |
|---|---|---|
| IMX708 | 0.678 vs 0.560 px own (1.21×) | 0.578 vs 0.454 (1.27×) |
| N6 | 1.03× | 1.04× |
| AE3 | 1.01× | 1.24× |

- Both blocks sat at about 0.85–1.1 m on every camera, so this checks **pose sets, not distance**. Distance dependence
  stays **untested** for all three cameras (rev 1 already expected this for N6/AE3).
- The IMX708's ~1.2× is a mild generalisation gap between the two pose sets.

**Straightness** on a calibration frame the model never saw (the median over board rows; it shows the correction works
where there are data):

| camera | before | after |
|---|---|---|
| IMX708 | 2.41 px | 0.17 px |
| N6 | 0.56 px | 0.22 px |
| AE3 | 0.50 px | 0.09 px |

**Compared with 10/14** (both central-only):

| camera | fx | cx | cy |
|---|---|---|---|
| IMX708 (vs `nereus002` on 10/14, JPEG, standard) | 2621 → 2628 (+0.3 %) | 2337 → 2314 | 1272 → 1294 |
| N6 (vs `n6_bayer`, 9 views) | 1613 → 1629 (+1.0 %) | 629 → 637 | 475 → 490 |

- The fx agreement is within the ±1 % 10/14 quoted. The cx/cy shifts (8–23 px) are as large as the coverage confound
  above.
- The AE3 has no 10/14 fit: it was refocused on 10/15, so there is nothing to compare.

## DNG vs JPEG cross-check (IMX708; R3; it can't change the DNG verdict)

| item | DNG → JPEG | bar | within |
|---|---|---|---|
| Δfx/fx | −0.03 % | ±0.5 % | yes |
| Δcx | −1.0 px | ±5 px | yes |
| Δcy | −1.0 px | ±5 px | yes |
| ray difference at the B4 probes | n/a at the 4 corner and 2 side probes (both rational fits fold there); 1.95 px top, 1.35 px bottom | ≤ 1 px | **no** |

- **Reading.** The two decodes give the **same camera** where there are data. They differ only where both fits are
  extrapolating: no probe that fails the bar was observed. That makes it a coverage effect, not evidence of an ISP
  effect. (The tool printed "ISP effect" for any out-of-bar result; this PR makes that wording neutral. The copied `crosscheck.md` keeps the original output.)
- **Other JPEG differences:**
  - 54 views (3 outliers);
  - one more covered cell, the top-right extreme cell (1,7): 6 corners vs 2 in the DNG. Likely cause, not checked:
    the ISP's lens-shading correction brightens the dark corners, so detection reaches a little further;
  - a worse held-out RMS (0.551 vs 0.504 px).
- **Caveat.** The JPEG run's **fisheye fit failed** in OpenCV (`InitExtrinsics: fabs(norm_u1) > 0`). The tool it ran
  on compared only standard and rational: 3.6 px literal at the crop corners, against the DNG's 23.9 px with fisheye.
  - **The tool now fails closed** (QE 006 R1, `3552ea02`). The pre-registered value is over all three models, so with
    one missing it is unknown: inf at every probe, with `models_missing` recorded.
  - Under that rule the JPEG run's B4 and B5c read inf. They were FAIL anyway, and the JPEG run never decides a
    verdict.
  - All three record runs (IMX708 DNG, N6, AE3) fit all three models, so no record number or verdict changes.

## Re-dive: declined for Wednesday (d58), backlog for November

PE 002 posted the lower-half recipe as decision **d58**. Nick declined it at 3:39 PM, in a board comment: "I can rerun
the test, but first I need a better mechanical mount and a way to get data from the rig to the surface to check the
alignment. So this is no an option for Wednesday. We should add to the backlock and check back in Nov".

So this run ends with its FAIL verdicts as they stand: no refit, no re-screen. The re-dive is backlog card
`c_calib_redive_nov`. The recipe, kept for that card, is **one lower-half block** covering all three cameras:

1. **Rig.** Raise it ~40 cm or tilt it nose-up ~25°.
2. **Board placements.** At 0.8 m, lower-left, lower-centre and lower-right, each flat, ±30° left/right and ~30° back:
   12 poses.
3. **Bottom corners.** 4 poses.
4. **IMX708 side edges.** 4 poses.
5. **Preview.** Full-frame on all three cameras.

That is ~20 poses, about 15 min of pool time. A crop-only calibration saves nothing, because the IMX708 crop's bottom
corners (y = 1745) are below every Friday observation.

## Nick's question 1: lens error vs urchin detection and counting per m² (spec rev 3; observed area only)

Nick asked (d58): "I need help to understand how bad the lens errors are relative to being able to detect the urchin -
is this something we need to do before running any field test to count urchin in the wild?"

All numbers come from the fitted models (NOT VALIDATED: the bars FAIL), evaluated **only inside the observed area**: the convex hull of the board
corners each fit used. That is 37 % of the IMX708 frame, 49 % of the N6 and 43 % of the AE3, all in the upper part.
Nothing was refitted.

- **Radius** r is the distance from the principal point over the half-diagonal (frame corner ≈ 1).
- **Radial displacement** is |pixel − pinhole(ray(pixel))|.
- **Local area scale** is det J · fx · fy (seafloor area per pixel relative to the centre).

Code: `tools/pool_calib_post/q1_observed_area.py` and `tools/pool_calib_post/lens_error_budget.py`, at commit
`3552ea02` (QE 006 R2; they first ran from the data folder, unchanged maths). Their outputs here are
`q1_observed_area.json` (reproduced byte for byte by the in-repo script) and `lens_error_budget.json`. In the budget, the
whole-frame region is now named `full_EXTRAPOLATED_not_measured` (context only, never a result), and its partly
extrapolated radius profile is dropped. The calibration JSONs are made by `tools/pool_calib_post/export_calibration.py`
at the same commit. Their `limits` now say the individual distortion coefficients are not identified by this coverage:
the DNG and JPEG rational coefficients differ wildly while K and the rays agree. The commands:

```bash
D=~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib; O=runs/pool_calib_20261016
CAMS="imx708=$D/20261017_152713_pool049_imx708_dng/imx708 n6=$D/20261017_153029_pool049_openmv_raw/n6 ae3=$D/20261017_153029_pool049_openmv_raw/ae3"
.venv-dev/bin/python tools/pool_calib_post/q1_observed_area.py $CAMS --crop imx708=1504,846,1600,900 --out $O/q1_observed_area.json
.venv-dev/bin/python tools/pool_calib_post/lens_error_budget.py $CAMS --nominal-fx imx708=1964.3 --nominal-fx n6=933.3 \
  --nominal-fx ae3=1033.3 --crop imx708=1504,846,1600,900 --out $O/lens_error_budget.json
.venv-dev/bin/python tools/pool_calib_post/export_calibration.py $O $D/undistort_maps $CAMS --prereg-sha c3356f49+dda46166 --git-sha 3552ea02
```

**1. Distortion size** (median radial displacement; % of radius):

| r | IMX708 | N6 | AE3 |
|---|---|---|---|
| 0.20–0.25 | 7.8 px (1.3 %) | 0.9 px (0.5 %) | 0.9 px (0.5 %) |
| 0.40–0.45 | 59.5 px (5.3 %) | 5.7 px (1.8 %) | 5.7 px (1.8 %) |
| 0.60–0.65 | 192 px (11.8 %) | 18.3 px (3.9 %) | 18.0 px (3.8 %) |
| edge of the observed area | 231 px (13.3 %) at 0.65–0.70 | 60.9 px (8.8 %) at 0.90–0.95 | 39.7 px (6.5 %) at 0.80–0.85 |

**2. What it does to area** (ring medians):

| | IMX708 | N6 | AE3 |
|---|---|---|---|
| area scale off by > 5 % from r = | 0.20 | 0.35 | 0.35 |
| area scale off by > 10 % from r = | 0.30 | 0.50 | 0.50 |
| area scale at the observed edge | −44 % | +40 % | −23 % |

**3. Urchin size**, as pixels across the test at the frame centre (5–8 cm test diameter; assumption, spec rev 3):

| range | IMX708 | N6 | AE3 |
|---|---|---|---|
| 0.8 m | 164–263 px | 102–163 px | 87–139 px |
| 1.5 m | 88–140 px | 54–87 px | 46–74 px |
| 2.0 m | 66–105 px | 41–65 px | 35–56 px |
| size change, centre → observed edge | ×1.33 | ×0.85 | ×1.14 |

**4. Detection vs counting:**

- **Detection: distortion doesn't matter at these sizes.**
  - An urchin is 35–263 px across.
  - Distortion changes that by 14–33 % at the edge of the observed area, less than the 2.5× size change between 0.8 m
    and 2 m.
  - Assumption: detectors need roughly 20–30 px; E6 has not set this number.
- **Counting per m² needs the area per image.** Density error if the correction is skipped:
  - **Over the observed area:** IMX708 −19.2 %, N6 +13.0 %, AE3 −8.3 %.
  - **Over the whole frame: not computed.** It needs the model outside the observed area, so per spec rev 3 this run
    stops and says so.
  - **Central crop with area error under 5 %**, frame-centred, the largest that stays wholly inside the observed area:

    | camera | crop | share of frame | density error if skipped |
    |---|---|---|---|
    | IMX708 | 553×311 px | 1.4 % | −0.5 % |
    | N6 | 294×184 px | 5.3 % | +1.3 % |
    | AE3 | 179×112 px | 2.0 % | −0.6 % |

    All three are limited by the observed area, not by the 5 % threshold.
  - **The IMX708 1600×900 field crop** is 70.4 % inside the observed area. Over that part, skipping the correction
    costs −4.2 %.
  - **No underwater calibration at all** (datasheet focal length): IMX708 −44 %, N6 −67 % (if the stock lens), AE3 −45 %.
  - **Left over after the correction, observed area:**

    | camera | bootstrap spread | spread across the 3 model families |
    |---|---|---|
    | IMX708 | 0.5 % | 3.2 % |
    | N6 | 0.2 % | 0.5 % |
    | AE3 | 0.3 % | 0.2 % |

  - Scale also needs the range to the seafloor or a known-size reference in frame (spec F6, out of scope here). Area grows
    with range squared, so a 5 % range error is a 10 % density error.

**5. Recommendation (calibrate before any field urchin count?): only for per-m².**

- Detection and per-frame counts don't need it.
- Per-m² does, for each camera unit, before densities are reported. That means the underwater focal length plus the
  distortion over the area being counted.
- Without it, a field test can detect and count per frame, and can give per-m² only inside a central crop like those
  above, with a known-size reference on the seafloor in view (about ±1–2 % from the lens, plus the reference's own
  error).
- Every calibration here is NOT VALIDATED (the full-frame bars fail). Using one goes beyond these results; that is
  Nick's call.

**Report:** https://claude.ai/artifact/XVVphV1DVXLfA5Do1d5LGp (v3), on the board's "Ready for you" lane (card
`ready_lens_report`).

- **Provisional posting.** It was posted PROVISIONAL before the QE pass, per PE 002's ruling (Sat ~5:10 PM): Nick asked to
  see it, and a clear provisional label keeps unreviewed numbers from reading as final. The label came off after QE
  006's PASS (Sun 9:30 AM), in report v4 at the same URL.
- **v2 correction.** v2 showed two items extrapolated beyond the measured area: a whole-frame density row and the dashed
  parts of a size chart. v3 removes them and says so at the top. The same note is on the card.
- **IMX708 images.** Taken from a figure re-render with the `d69c0cf0` drawing fix.
  - **This is a deviation from prereg §5** ("one run per input"). It was a second full pipeline run on the IMX708 DNG
    input, used only for the report's IMX708 images.
  - Every bar status and verdict is identical to the record run, which stays the result.
  - The numbers are not bit-exact. fx moves by +8×10⁻⁷ px and held-out RMS by +4×10⁻¹⁰ px. The B5c aligned model
    disagreement moved 1.1877 → 1.1860 px, more than floating-point noise; QE 006 flagged it.
  - The likely cause is OpenCV threading. Filed: set `cv2.setNumThreads(1)` so reruns are bit-exact.

## Not tested / limits

- **QE.** The tool code passed QE 006 @ `f023448b` (Sun 9:30 AM PT).
- **Filed, not fixed** (QE 006 nits):
  - B6's fallback to the IMX708 1964 px applies to any `--native-size` camera given no `--nominal-fx`. The help now
    says to always pass `--nominal-fx` for another sensor; changing it would change the 10/14 synthetic tests.
  - Bit-exact reruns (`cv2.setNumThreads(1)`).
- **DNG decode.** Grey-level only, through rawpy `postprocess` (camera WB, 8-bit, gamma), the pipeline's existing
  loader. A linear demosaic was not tried. The JPEG cross-check says it makes no difference to the intrinsics.
- **Verdict sheets.** The tool's `verdict.png` and `coverage.png` were not changed for the mask. Their red boxes show
  the 4 true corner cells, not the 4 extreme visible cells. The bar rows and `coverage.json` carry the masked rule.
  All 8 of those cells are empty, so the picture doesn't mislead about this verdict.
- **Fresh water.** In seawater fx is about +0.5 %.
- **No scale.** A calibration gives no seafloor scale (spec F6); urchins per m² still need range or a reference in
  frame (E6 spec).
