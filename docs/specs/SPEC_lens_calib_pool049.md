# Spec: lens correction from Friday's ChArUco re-dive, nereus002 pool049 (IMX708 + N6 + AE3) (PE rev 0, tier (b))

Tier (b): desk study on pulled pool data, no hardware. But it amends pre-registered bars, so its FIRST commit is the
pre-registration amendment, and QE checks that commit before any calibration runs. Board: prop_lens_calib_pool049 →
c_charuco. Author: Principal Engineer, Fri 10/16 4:41 PM PT. Nick settled the 4 open details in the PE chat (~4:15 PM).

- **Why:** E6. Nick wants urchins per m² (d33: literal / metric reading). True scale starts with a correct lens model;
  the gen2 shoot-out needs "pixels on target". The 10/14 run failed only on corner coverage (22 %, 0 of 4 corners, board
  ~1.55 m, central); Friday's re-dive (pool049) was built to fix that.
- **Inputs:** `~/NereusPool/20261016/nereus002/pool049_20261016T212527Z/` (poses.csv: 78 poses). IMX708: cal_a, 41
  poses at 0.7–0.9 m, 4608×2592 **JPEGs** (Nick: JPEGs now; DNGs only if a bar comes out borderline). N6 + AE3: cal_b,
  37 poses at 1.5–2.0 m, `.bayer` + `.json` (the tool's preferred input). Board: calib.io CCT600400C, 9×14, 40/30 mm,
  DICT_5X5_100 (the tool default; the same single board as 10/14).
- **Must do (numbered, testable):**
  1. **Pre-registration amendment first** (`docs/pool_calib_prereg_20261016.md`, committed before any calibration;
     QE confirms it before the run):
     a. **N6 and AE3 bars** (Nick: "Same bars, own pixels"): B1–B5 of the 10/14 prereg in each sensor's own pixels.
        B5c uses the full frame (they have no production crop).
     b. **IMX708 vignette mask** (Nick: mask the dark corners): one fixed mask from ONE frame chosen by rule (the
        first cal_a frame by name), thresholded on the black port region, drawn before any calibration. Corner
        observations inside the mask don't count. B3 coverage is measured on the 8×5 grid cells that are ≥ 50 %
        visible, and "the 4 corners" become the 4 extreme visible cells. B4 probes move to the visible area's corners
        and edge midpoints.
     c. **B6 nominal in-air focal length from datasheets** (Nick: "all details are known from data sheet"). IMX708 =
        Raspberry Pi Camera Module 3 Wide, 2.75 mm / 1.4 µm = 1964 px (already in the tool). N6 and AE3: look up the
        sensor pixel pitch and the lens focal length on the OpenMV website; cite each URL; convert to px at the frame
        size used. Record which lens is actually fitted (rig profiles / Nick); if it isn't the documented stock lens,
        B6 for that camera reads "N/A: fitted lens differs from the datasheet", not a fail.
     d. **Model disagreement read literally (metric)**, Nick's d33.
  2. Run `tools/pool_calib_pipeline.py` (bm #176–#179) per camera with `--nominal-fx` for N6/AE3. Add the mask as one
     small tool option (`--mask NAME=PNG`) with a unit test; no other tool change.
  3. Per camera: the chosen model and coefficients, held-out RMS, bootstrap spread, coverage map, B1–B6 verdicts; a
     comparison with 10/14 (fx, cx, cy) per camera. AE3's lens was refocused 10/15, so its 10/14 fit is context only.
  4. Calibration JSON per camera + port + date, and undistort maps.
  5. A one-page cut sheet for Nick (before-after-report style): per camera, a corner-heavy pose and a v3-card frame,
     before vs after undistort; coverage heat map; one-line verdict per camera.
- **Failure modes (what goes wrong, how it's caught, what happens):**
  - F1 N6/AE3 poses at 1.5–2.0 m don't reach the corners again (the 10/14 cause). Caught by B3. Then report the missing
    cells; Nick can add close poses at pool test 2 (Wed 10/21).
  - F2 Mask chosen after seeing results biases coverage. The mask is fixed by rule in commit 1 and its sha256 is in the
    MANIFEST before the run.
  - F3 Wrong datasheet focal length (lens not stock, or the AE3 refocus). B6 shows N/A or a ratio far from 1.33; the
    lens record decides.
  - F4 JPEG processing (ISP sharpening and shading) shifts corner positions slightly. Only matters if a bar is within
    10 % of its limit; then re-run that camera on the DNGs (same commit, labelled). The DNGs for all 78 poses are on the Mac
    since Fri 9:37 PM (rig session, sha256-verified), so no rig pull is needed.
  - F5 A pinhole + distortion model fits a flat port poorly at the edges. B4 model disagreement (literal) exposes it;
    report, don't hide.
  - F6 Calibration gives intrinsics, not seafloor scale. Per-m² (d33) also needs range or a known-size reference; out
    of scope here (the E6 detection spec). Report the board-pose range per frame as a by-product.
- **Pass / fail (per camera, set in commit 1 before any run):** PASS = B1–B6 as amended. Minimum useful result:
  IMX708 B5c (production crop) PASS. Stop early, say so on the card and tell the PE + PjM if: a camera keeps < 15 usable
  poses after the stage-1 screen (stop that camera); IMX708 B3 still fails (report the missing cells; no refit to rescue
  it); Mac free disk < 8 GiB; any step would need a rig command (never).
- **Cost:** ~3–4 h of session time after the PjM's heavy-compute all-clear; ~1–2 GB outputs under
  `~/NereusData/bm_cam_legacy/<date>_pool049_calib/` with a MANIFEST; Nick ~10 min on the cut sheet.
- **Exit:** PASS → calibration JSON committed (bm PR: amendment + tool option + RESULTS + JSON; QE → PjM merges);
  consumers are the E6 density spec and the shoot-out. FAIL → the failing bar and missing cells; Nick chooses close
  re-poses at pool test 2 (Wed 10/21) or a crop-only calibration (B5c).
- **Out of scope:** bmcam003/004 (not in this run); refraction-aware port models; the urchin density computation.

## Amendment rev 1 (PE, Sat 10/17 2:38 PM; Nick approved 1:59 PM, questions + deliverable verbatim on prop_lens_calib_pool049)
- **IMX708 input = DNG** (Nick, PE chat ~2:37 PM: "DNG, JPEG as a check"). Calibrate on the DNGs for all 41 cal_a poses
  (on the Mac, sha256-verified Fri 9:37 PM). Run the JPEGs once as a cross-check against 10/14 and report the fx / cx / cy /
  distortion differences. The verdict uses the DNG run.
- **Flat port, tilt and decentre (Nick's question).** A flat port bends rays more toward the frame edges, adds radial
  distortion and makes focal length ~1.33× longer. A pinhole + distortion model fitted underwater absorbs most of that
  within the calibrated distance range. A tilted or off-centre port adds decentering that the model only approximates.
  Detection, added to RESULTS per camera:
  - principal point offset (cx, cy vs the image centre);
  - tangential coefficients p1 and p2;
  - held-out residuals vs image radius and vs board distance (residuals that grow with radius or vary with distance point
    to refraction or tilt the model can't absorb);
  - B4 model disagreement at the edges (literal).

  Limit: N6 and AE3 poses are 1.5–2.0 m only, so distance dependence can't be tested for them; say so. If decentre or
  tilt shows, recommend an explicit refractive (flat-port) model as a follow-up card; don't build it here.
- **Explainer side card (Nick's deliverable)** in the summary report, one page: what was done, why (true scale for urchins
  per m²), how (ChArUco poses → model fit → held-out check), and how industry does underwater lens correction:
  1. in-situ pinhole + distortion calibration underwater (common, valid near the calibrated distances);
  2. explicit refractive flat-port models (e.g. Treibitz et al. 2012; Jordt-Sedlazeck & Koch 2012);
  3. dome ports centred on the lens's entrance pupil to remove most refraction.

## Amendment rev 2 (PE, Sat 10/17 2:55 PM; QE 004 R1–R6 on rev 1). Overrides earlier lines where they conflict.
- **Stamps:** Nick's "DNG, JPEG as a check" was answered in the PE chat at ~2:40 PM (rev 1's "~2:37 PM" is corrected).
- **Overrides of line 11 and F4:** the IMX708 input is **DNG (primary)**. JPEG is a cross-check only; a JPEG result never
  changes a DNG verdict.
- **DNG path (R1):**
  - Install `rawpy` at a pinned version in the session's venv and record the version in the MANIFEST. Don't change bm's
    shared .venv outside the PR.
  - The IMX708 input is explicitly `captures/imx708/*.dng`; never a folder glob that could match the .jpg siblings.
  - Before the run, decode one DNG and check it is 4608×2592; if not, stop.
- **JPEG cross-check bar (R3)**, same mask for both, set now:
  - |Δfx|/fx ≤ 0.5 %; |Δcx| and |Δcy| ≤ 5 px; ray difference at the B4 probes ≤ 1 px.
  - If they disagree beyond that, the DNG calibration JSON ships and the disagreement is reported as a finding (an ISP
    effect).
- **Tilt / decentre flags (R4)**, reported as flags, not pass/fail, with thresholds set now. A flag is raised if any of:
  - |cx − W/2| > 2 % of W, or |cy − H/2| > 2 % of H;
  - |p1| or |p2| > 1e-3, always taken from the standard 5-coefficient fit, whichever model wins;
  - held-out RMS in the outer 20 % of radius > 2× that in the inner 50 %.

  The residual-vs-radius report is new tool code: one function in `tools/pool_calib_pipeline.py` with a unit test, in the
  same PR. So the tool changes are the mask option and this report (rev 0.1's "no other tool change" is amended).
- **Distances (R5, corrects rev 1):** all 78 poses have frames from all three cameras, so every camera has 41 poses at
  0.7–0.9 m and 37 at 1.5–2.0 m.
  - Calibration set per camera = all usable poses from both blocks.
  - Pre-registered cross-distance check (report-only): fit on one block and measure held-out RMS on the other, both ways.
    Residual growth across distance points to refraction or tilt.
  - Each camera's validated range = the distances in its calibration set.
- **Commit 1 (R6):** the pre-registration amendment carries rev 1's and rev 2's thresholds and rules before any run.

## Amendment rev 3 (PE 002, Sat 10/17 5:07 PM; Nick's d58 answer, 3:39 PM, verbatim on decision d58). Adds deliverables; no bar moves.
- **Nick (board comment on d58):** "I'd like to see a report and learn how this was done, once this is ready it should go to
  the "ready for you" lane so I can see what was done with visuals and comparisons of before and after the correction.
  1. I need help to understand how bad the lens errors are relative to being able to detect the urchin - is this something
  we need to do before running any field test to count urchin in the wild? 2. I can rerun the test, but first I need a
  better mechanical mount and a way to get data from the rig to the surface to check the alignment. So this is no an
  option for Wednesday. We should add to the backlock and check back in Nov".
- **No re-poses on Wed.** The re-dive is backlog card c_calib_redive_nov (Nov, after a better mount and a surface link to
  check alignment). This run ends with its FAIL verdicts as they stand: no refit, no re-screen.
- **Summary report → the board's "Ready for you" lane** (PjM 003 says how to post that card), after QE passes the tool
  code (8e98892e). It holds: the cut sheet (must do 5) with before/after undistort comparisons, valid only inside the
  observed area and labelled so; the coverage maps; the explainer side card (rev 1); and the answer to his question 1,
  below.
- **Question 1, answered with numbers per camera, from the provisional fits, inside the observed area only:**
  1. Distortion size: the radial displacement (px, and % of radius) vs image radius; mark where the observed area ends
     (beyond it is extrapolation, not measurement).
  2. What it does to area: the local area scale (pixel footprint relative to the centre) vs radius, and the radius
     where it is off by > 5 % and > 10 %.
  3. Urchin size in pixels at 0.8 m and 1.5–2.0 m (take ~5–8 cm test diameters and say so), and how much distortion
     changes that size at the centre vs the edge of the observed area.
  4. The answer in plain words, kept separate:
     - **Detection** (is there an urchin): does distortion matter at these sizes?
     - **Counting per m²** (d33): needs the area per image. State the error from skipping the correction (whole frame
       vs a central crop where the area error is < 5 %), and that scale also needs range or a known-size reference in
       frame (F6, out of scope here).
  5. A recommendation for "calibrate before any field urchin count?": yes / no / only for per-m², with what a field
     test could do without it (e.g. count only inside the < 5 % central crop, with a scale reference).
- Cost: ~1–2 h desk, no hardware. Stop if a number would need data outside the observed area; say so instead.
