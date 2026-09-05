# Sprint 21 (proposed) — Cross-cycle temporal fusion + change-detection feasibility for fixed-mount reef cameras

**Status:** DRAFT plan for Nick's review, 2026-09-04. No code. Branch `development` (8b7cf97); this file is uncommitted.
**Data:** `~/Downloads/Day_03/` — 16 frames, bmcam001 / SPOT-33361C (Cheeca/AOML reef): 2026-09-01T18Z, then 5/day on 09-02..09-04 at 14-18Z (10:00-14:00 EDT).
Nick said "Day_02"; that folder does not exist. `SPOT-33361C_BMCAM_001_Day02` is a 7-frame subset of Day_03. **Assumption: Day_03 is the intended folder.**
**Adversarial review:** two independent reviews of draft v0 (imaging science; field-ops/product). Their contradicting claims were re-measured before acceptance; what changed is in §8.

---

## 1. Nick's four asks — short answers (evidence in §2)

| # | Ask | Answer |
|---|-----|--------|
| A | Combine the 4-5 daily frames (and days) into a richer, more complete reef image | Yes, but not by iPhone-style averaging: plain mean/median stacking makes this image **softer**. A robust, reference-anchored fusion gives a **cleaner, more complete** image at the same resolution (transients, particles, the soft bottom band of partially delivered frames removed). Genuinely sharper or redder frames need capture-side work (Sprint20). |
| B | Nereus v3 per frame then stack, or stack then correct? | Neither as stated. **Fuse in a per-frame linear layer anchored on the static reef, then render V3 once.** Card-anchored per-frame gain must *not* be applied frame-wide before fusing: it injects the card's own lighting variance into the coral (measured, §2). Efficiency is settled by architecture; quality is measured on day 3. |
| C | The repeatable process exploiting a fixed camera shooting the same reef 4-5x/day | The "reef epoch" pipeline in §3: register on the static reef, normalize each frame on static reef anchors, weight by delivery state and transients, fuse into daily and rolling composites, and keep the per-pixel stability map that falls out of it. |
| D | Detect coral change / bleaching given bad light, dead red, unevenly soft partial frames | Feasible **as a flag, not a diagnosis**, on the stable massive corals only, using the B/G ratio in the measurement layer against the colony's own multi-week baseline. This sprint yields a *pilot sensitivity estimate* with pre-registered thresholds (§5). Not "bleaching detection". |

## 2. What we measured (bring-up + scratchpad pilots, 2026-09-04; pilot-grade numbers)

**Delivery and coding (this overturns the draft's noise model).**
- Frames are backend display derivatives (baseline JPEG, re-encoded ~q85), not the Pi's progressive originals. The originals are **q70** (q80 on two frames): a q70 progressive encode of this scene is 43.5 KB = 194 messages, exactly at the 195 cap; q80 does not fit. At q70, quantization error is ~1 DN, negligible against the ~9 DN frame-to-frame illumination residual.
- The dominant coding defect is **tail truncation of the final luma refinement scan** (10 scans from the Pi's exact Pillow call; that last scan is the final ~32% of a q70 file). A frame received at fraction f ≥ 0.68 has DC and chroma complete everywhere and full luma precision only above a cut row ≈ (f−0.68)/0.32 of the height. The reviewer's pixel-parity forensic puts typical frames at ~86-99% received (cut rows ~320-500), two frames near 90%, and 09-03T15Z below 65% (chroma refinement lost too). **This is a per-frame bias below a known row, not noise; averaging cannot remove it, weighting by delivery state can.** The card (rows ~390-530) sits below the typical cut, so card sharpness tracks delivery, not optics.
- Completeness is knowable exactly: backend media rows carry `expected_chunks / received_chunks / percent_received / is_complete`; the first-gap index comes from Sofar records via `tools/count_complete_images.py` (it reads `SOFAR_API_TOKEN_BM_REEF`; bmcam001 needs the AOML token aliased into that name). The backend decodes the clean prefix before the first gap and never splices (verified in `poll_once_ingest.py` / `bm_image_parser.py`). Loss is mostly the 5-minute UTC blackout grid grazing the burst tail (Sprint11 lane logic already tries to miss it; whether these 16 bursts were phased correctly is a day-0 check).

**Geometry.**
- Mount is stable: 0.6-2 px translation, ≤0.08° rotation over 3 days; AprilTag centres move by the same ~0.5-0.85 px, so the card moves *with* the mount, not on its own. A 0.3 px bicubic resample costs ~20% edge energy, so sub-pixel warps are not free. Rule: no warp below 0.5 px, integer shifts up to ~2 px, above that flag a new epoch and stop.
- Per-pixel temporal std map: **low on the massive boulder corals**; high on gorgonian/soft-coral polyps (sway), sand (ripples, particles, caustics), transiting fish. The stability map is a product by itself (where change detection is even possible).

**Illumination (this overturns the draft's normalization).**
- bmcam001 runs AE/AWB **auto**; "linear" frames are ISP output with per-frame gamma/CCM. Card white spans 0.42-0.60 linear across frames; GRVI veil and gain vary 1.5-2x; white-patch red 0.4-13.7% (same hour, different days: 0.4/2.0/5.1%).
- **Card-anchored gain does not stabilize the coral wall.** Robust temporal CV of static coral tiles (linear G): raw 0.30; scene-median gain 0.064; card-white gain 0.30. Correlation of card-white level with coral level: 0.24. The card lies on bright near-field sand in direct downwelling light; the wall is a shaded, more distant vertical surface. The card black patch is brighter than ~90% of coral-wall pixels, so a frame-wide card veil subtraction pushes coral negative (GRVI survives only via softplus and the partial-veil clarity pick, a display choice). Consequence: the fusion/measurement layer is anchored on **static reef patches**; the card is used for chroma (B/G) calibration and its own health.
- Day-to-day differences at the same hour (4.0 gray levels on static reef; reviewer: RMS 9.5-19.6) are *larger* than hour-to-hour within a day (3.1; 7.4-14.3). There is a real within-day B/G drift of −7..−10% from 14Z to 18Z, but day conditions (turbidity, cloud, sea state, delivery) dominate. Stratify by measured covariates, not by clock; "day" is the unit of independence.

**Fusion.**
- Plain stacking of the 16 registered frames: edge energy roughly halves (Laplacian variance 86 → 27/32), mostly because half the single-frame high-frequency energy is non-static (caustics, particles, sway) and because the frames' refinement cuts differ. Blockiness: unchanged by one metric, 1.9 → 1.64 by the reviewer's on 12 good frames; split-half noise drops ~3x (8.7 → 2.8 DN). A robust reference-anchored merge keeps ~75% of the reference's edge energy (28 vs 37; plain mean 18) at ~10 effective frames per pixel, and drops fish and particles.
- Chroma is 4:2:0 and red is Cr noise (R ≤ 8 in 27-65% of pixels): any colour index is G/B-only on ≥16x16-pixel aggregates.

**Ground truth.** Every cycle persists `<stem>_native_full.jpg` (4608x2592) and the transmitted JPEG on the unit's SD; nothing deletes them (retention unverified on-unit). bmcam001 is an armed customer unit (8-min cycles, ~3 min awake, hotspot-only): **retrieval is a site-visit task and gates nothing in this sprint.**

## 3. The process (ask C): "cross-cycle temporal fusion", shore-side, one tool

Named to stay distinct from Sprint20's *intra-cycle* capture stacking:

| Layer | Owner | Attacks | Status |
|---|---|---|---|
| Capture-side, intra-cycle, pre-encode (Sprint20: locked AE/AWB burst average, red HDR bracket) | Pi | sensor noise, dead red | captured, unscheduled |
| Shore-side, cross-cycle/day, post-decode (**this sprint**) | Mac now, backend later | tail-truncation softness, transients, illumination drift; builds the temporal baseline and stability map | proposed |

Interface: per-frame `q`, `complete`, chunk counts already flow in START/END and onto media rows. Nothing new on the Pi. Notes for the Sprint20 spec, not this sprint: AE/AWB lock, exposure metadata (ExposureTime/AnalogueGain/ColourGains/Lux from the sidecar) into END or SD, sub-pixel crop jitter.

`tools/bm_temporal_fuse.py` — same conventions as the GRVI runner; imports `reference_card_color_utils` (`GRVIModel.apply_core` / `apply_render`) and never modifies the runner:

1. **Ingest + provenance.** Frames (display derivatives now; R2 originals/partial bytes when available), timestamps, `q` from the START message, completeness, first gap. Timestamped run folder: manifest, CSV, logs, cut sheets.
2. **Register** to the epoch reference grid with a similarity fit on 3-5 hand-picked rock anchors plus the static mask (card, water, sand, gorgonians excluded). No warp below 0.5 px; integer shift otherwise; residual > ~1-2 px ⇒ new epoch, frame excluded and flagged. Never ghost silently.
3. **Card once per epoch.** Detect the AprilTag quad on the reference; re-sample the same rectified coordinates in every registered frame. Card outputs per frame: chroma (B/G) calibration, health (ramp-linearity residual, white/black ratio), red-health.
4. **Per-frame linear layer on reef anchors.** OETF per frame from the card's 5-step gray ramp (the ISP is not sRGB); gain from static reef anchors at the colonies' distance and orientation; veil from the scene's darkest stable crevice, not the card black. Acceptance: the temporal CV of static coral tiles must drop below the scene-global baseline (~0.06); otherwise the normalization is rejected for that frame.
5. **Weights**, one term at a time on the cut sheet: delivery-state weight (scan reached / cut row: exact from the byte stream, else the parity forensic on derivatives; local sharpness is *not* used as a truncation proxy, it is confounded by turbidity, distance, caustics, blocking) → transient mask (fish, particles, sway: robust deviation vs running median) → optional per-region agreement weight vs the reference (HDR+-style).
6. **Fuse** in the linear layer, reference = sharpest, most-complete frame. Outputs: per day, same-hour across days, rolling N-day, plus the **stability map** and effective-frame-count map.
7. **Render once** with `apply_render` (V3 preset) on the composite.
8. **Outputs:** 16-bit linear composite, 8-bit V3 render, stability/coverage maps, per-frame QC CSV (q, completeness, cut row, shift, residual, accepted/rejected + reason, card health, covariates: card white level and B/G, diffuse/direct proxy from a fixed boulder shadow edge, turbidity proxy from a distant static feature's contrast, port-fouling proxy = corner-vs-centre contrast), cut sheets: reference vs composite with hero-coral and card crops, the fusion ladder, the variance split.

## 4. Sequence: decision in 5 working days, all Mac-side, nothing touches the Pi

| Day | Work | Output / gate |
|---|---|---|
| 0 (2-3 h) | Archive the frames + manifest (provenance already leaking: Day_02 vs Day_03). Sofar pull for the 16 frames (q, complete, first gap, burst phase vs the 300 s grid) → CSV. Byte-identity check of the duplicated frames; re-download the odd "(2)" copy of 09-01T18Z. Median of the existing `after_grvi.jpg` per day and per hour → first cut sheet. | Nick sees a first composite the same day; delivery state per frame known. |
| 1 | Fusion tool MVP (steps 1-4, 6-8) with the **fusion ladder**: median (control) → mean → +reef-anchor normalization → +delivery weight → +transient mask, one cut-sheet column each. Variance-split panel (day vs hour vs transient). Per-frame runtime logged. | Gate (split-half, not "sharper than the best frame"): odd-frame vs even-frame composites agree on static coral ROIs better than any two single frames; hero-coral edge energy ≥ 0.9× the reference; blockiness not worse; each ladder step attributable. |
| 2 | Truth on the Mac: full-resolution reference frames through the Sprint06 partial-transmission sweep (`tools/bm_reference_card_jpeg_partial_sweep.py`) at the real q70 and received fractions → known-truth composites; PSNR/SSIM per ROI. Nick's hero-crop review. | Answers A and C with a reference, not a proxy. |
| 3 | Ask B as flags on the same tool. Arms: (1) V3 per frame then stack; (2) stack raw then V3 once; (3) per-frame **card**-anchored linear then fuse then render; (4) per-frame **reef**-anchored linear then fuse then render. Identical frame sets and weights across arms. Metric: **split-half test-retest of hero-colony B/G and lightness**; card ΔE2000 only as a sanity check (it ties by construction). Efficiency stated architecturally: per-frame GRVI runs anyway for the gallery; a composite adds fusion plus one render; per-epoch card detection is the real saving. | Answer B. Hypothesis to test: (4) ≥ (2) > (3) on reproducibility, (1) ≈ (4) on appearance, (2) cheapest. |
| 4 | Change-detection pilot (§5): colony polygons, B/G differential index, null variance components (day × hour, split-half), minimum detectable change vs colony area and baseline length. | Answer D as a pilot sensitivity estimate. |
| 5 | Decision memo + cut sheets; `SPEC.md` with the gates written down; one TODO line. | Decisions: ship the composite as a backend derivative? start baseline accumulation? schedule Sprint20? |
| Week 2 (not critical path) | Hero-crop iteration; optional bmcam000 bench A/B (static printed reef + card through the identical RC pipeline with forced tail truncation) for AE/AWB-inclusive truth; SD/native retrieval at the next site visit; Sprint20 coordination note. | — |

## 5. Change detection (ask D): how, honestly

- **Where:** only on the stable structures the stability map identifies (massive boulder corals). Soft corals move; their observable is extent/presence, out of scope.
- **Observable:** **B/G ratio up** is the physically specific G/B-only bleaching signal (loss of blue absorption by symbiont pigments); lightness up is secondary because turbidity, sediment, diffuse light and port fouling also raise it. Measured veil B/G (0.45-0.64) ≈ coral B/G (~0.5), so turbidity here is mainly a lightness confounder, which helps. Direction and magnitude must be calibrated on **real labeled bleached/healthy pixels in comparable water** (AOML Cheeca survey imagery, diver photos), not on literature. Require colony-shaped change, not distance-shaped.
- **Index:** differential — colony B/G and lightness relative to adjacent non-coral static substrate at the same distance, on ≥16x16-px aggregates, per hand-drawn colony polygon (5-10 colonies; AOML knows these colonies). Sediment proxy (sand-like chromaticity + texture loss) as a covariate.
- **Baseline and rule:** the colony's own rolling baseline, per condition (covariates in a regression, day as the random effect). With n = 3 days, sigma has ~50% relative error and the daily frames are not independent, so **no thresholds until ≥3-4 weeks of baseline**; then a persistence rule (≥3 consecutive days) and a zero-false-alarm budget on the baseline period, all pre-registered. Report *minimum detectable change vs colony area and baseline length*, not k·σ.
- **The null is unverified.** Early September is peak thermal-stress season in the Florida Keys; "3 no-change days" is an assumption. Ask AOML whether bleaching is occurring at Cheeca now: if yes, this dataset may already contain a positive case worth labeling.
- **Reviewer's honest sensitivity from today's data:** whole-wall B/G day-to-day scatter ~3% ⇒ a persistent ≥10% shift is detectable in days; at 40x40-px colony scale scatter is 4-9% per frame ⇒ ≥15-25% needed. Larger colonies and longer baselines improve both.
- **Synthetic injection** only pre-encode (spectral model: B/G shift + lightness, injected into full-resolution frames before the RC encode via the Sprint06 sweep), with the false-positive axis from the real day-to-day null; report vs colony size and scan reached. Injecting into derivatives is meaningless (triple-compressed, ignores AE/AWB).
- **Ground truth to start collecting:** CoralWatch Coral Health Chart readings per colony with IDs matched to the camera polygons on every visit; change-standard targets at **two distances** (card position and on/near the wall — a tile beside the card lives in the card's light field, which does not track the wall).
- **Defensible customer wording after this sprint** (reviewer's draft, adopted): the composite is "a cleaner daily view of the reef, built from the day's frames and the same hour on previous days; haze, particles and passing fish are averaged out; it is not sharper than the camera's transmitted resolution." The flag "marks a sustained brightening and loss of colour of a marked colony relative to its own multi-week baseline; it means worth a diver check, not a bleaching diagnosis; not evaluated against ground-truth bleaching." Do not say "bleaching detection".

## 6. Product path (after the day-5 decision; not in this sprint)

- **Backend:** composite derivative per device (daily + rolling) beside the frames, built from the raw bytes in R2 rather than the display derivative; stability map and per-colony time series in the gallery. Hooks exist (`image_derivatives.py`, `partial/` keys, completeness fields). Cost from the day-1 runtime log.
- **Delivery, cheap and evidence-driven:** if day 0 shows the bursts' tails being clipped at the blackout boundary, the fix is lane/cap configuration (e.g. message_cap ~185) — a YAML-only change at the next field update, not this sprint.
- **Capture-side:** Sprint20 is the route to sharper, redder frames. The draft's "scene-static delivery / tile rotation" idea is **dropped to the icebox**: tail loss is deterministic (whatever is sent last is lost), the Pi has no downlink to know what arrived, each tile would be its own first-gap-limited stream, tile bytes come out of the base frame's q, and cross-day tiles seam and cannot serve change detection. "Send the cut point" is unnecessary (the backend can read scan boundaries from the bytes it holds). Pillow exposes no scan script, so scan reordering would need another encoder — future.
- **Observation for the gallery, separate from this sprint:** V3 anchors exposure on the card white, so coral brightness in corrected single frames varies ~±30% frame to frame for the same reason card gain fails as a normalizer. A reef-anchored exposure stage would make the daily frames consistent.

## 7. Start today, zero risk

- One archive folder per day of derivatives with a manifest (source, download time, backend media id, completeness).
- Daily Sofar pull (q, complete, first gap, burst phase) appended to a CSV.
- Ask the backend owner whether R2 `partial/` bytes and originals are retained indefinitely, and whether the Mac can get read credentials.
- Ask AOML: colony IDs and condition records for the colonies in frame; is bleaching occurring now; CoralWatch readings and two change-standard targets at the next dive.
- Pick tide and weather sources for the site; Spotter wave height is already logged.
- SD retention check and native retrieval on the next site visit.

## 8. Adversarial review: what changed from draft v0

**Re-measured and accepted (imaging-science review):** originals are q70/q80, not q~20 (F2; verified by encode size vs the 195-message cap); tail truncation is a per-frame bias, weight by delivery state and drop local sharpness as its proxy (F2); card-anchored gain does not stabilize the coral wall, measurement layer moved to static reef anchors with the card demoted to chroma calibration and health (F1; verified: coral-tile CV 0.30 card vs 0.064 scene); success gates rewritten as split-half and reference metrics with Mac-side truth from the Sprint06 sweep, bench A/B optional (F3); covariate stratification instead of hour bins (F4; matches our measurement); Phase 4 rewritten as a sensitivity study with ≥3-4-week baseline, differential B/G index, persistence rule, unverified null (F5, F6, F7); ground truth via CoralWatch readings and two-distance targets, nothing gated on natives (F8); registration simplified to anchors with no sub-pixel warps below 0.5 px (F9); tile rotation and cut-point telemetry dropped, blackout-tail check added (F10); per-frame OETF from the gray ramp (F11); G/B-only indices on ≥16-px aggregates (F12); card-health and port-fouling QC metrics (F14). **Checked and not accepted:** F13 (backend splicing across a gap) — the backend decodes the clean prefix only; the odd "(2)" file gets re-downloaded on day 0.
**Accepted (field-ops/product review):** Pi-side pull off the critical path (F1); 5-day sequence, one tool, fusion ladder (F2, F11); grouping question settled by the first cut sheet (F3); tile idea to the icebox (F4); pilot-sensitivity framing with pre-registered thresholds (F5); "cross-cycle temporal fusion" naming and the division-of-labour table with Sprint20 (F6); dependency corrections — token alias, backend completeness fields, R2 optional (F7); efficiency answered architecturally, per-epoch card detection (F8); day-0 data collection (F9); unsourced values fixed, "min tag side" dropped (F10); runner imported not modified (F12); Nick's decisions listed (F13); SPEC + TODO home (F14).

## 9. Decisions only Nick can make

1. Deliverable of this sprint: internal feasibility memo, or a gallery feature (sets §6 scope)?
2. Composite cadence: end-of-day, rolling, or both?
3. Who draws the colony polygons: Nick, or AOML?
4. Is Sprint20 getting scheduled this month? (Changes what shore-side must compensate for.)
5. Alert policy once a baseline exists: persistence, who receives the flag, wording.
6. Permission to ask AOML for colony records, CoralWatch readings, and change-standard targets; permission to ask the backend owner for R2 access.
7. Confirm: Day_03 is the folder; no Pi changes in this sprint; SD retrieval waits for the next site visit.
