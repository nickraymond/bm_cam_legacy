# Sprint 06 — JPEG (baseline + progressive) compression & partial-transmission DOE

**Status:** Draft for review · **Created:** 2026-07-21 · **Owner:** Nick
**Repo:** `bm_cam_legacy` · **Scope:** Mac-side `tools/` DOE only (no Pi, no SSH this sprint)
**Follows:** the repo-root `CLAUDE.md` (agent manifesto). Read it first. This spec is the source of truth.

---

## 1. Why this sprint exists

The customer buoy `SPOT-33361C` → `BMCAM_001` transmits, but its images never render: the
Bristlemouth/cellular link **cuts transmissions at the tail** (backend bug register **B6** — 5 of
10 captures lost their `<END IMG>` and trailing chunks, including two where every image chunk but
the last arrived). This is a link characteristic, not a backend parse bug.

The camera transmits **HEIC**, which cannot be decoded from a truncated byte stream — so a cut tail
= a blank image. **JPEG degrades gracefully**: a truncated *baseline* JPEG yields the top N% of the
frame; a truncated *progressive* JPEG yields a full but blurry frame even at ~20% received
(verified on real files). Switching the camera to JPEG turns "blank placeholder" into "usable
image" when the tail is lost.

**This sprint finds the JPEG setting to deploy.** It is **Mac-side offline** work on reference
imagery: sweep JPEG quality and mode, measure quality + realistic transmit budget + behavior under
tail-loss, and output a shortlist of `(mode, quality)` values. Those values are then validated on
the Pi in a **separate fast-follow sprint** (P4 below) — on-device, so we catch Mac-specific
encode efficiencies and Pi Zero 2W memory limits before shipping to the customer over Tailscale SSH.

---

## 2. Fixed for the whole sprint (do not sweep these)

- **Source:** native `4608×2592` JPEG (synthetic IMX708-native, via `prepare_reference_images.py`).
- **Geometry:** the existing fixed crop → **downsample to 1600 wide** (lanczos), held constant.
  (Crop/downsample is *not* a variable this sprint — Sprint02 already covered spatial density.)
- **Two inputs, different jobs:**
  | Input | Has AprilTags? | Primary role |
  |---|---|---|
  | **Kitchen reference card** (high-res) | yes | quantitative "quality" via AprilTag detection |
  | **Coral reef synthetic** | **no** | realistic file size → transmit budget, + sharpness/contrast/PSNR |
  The card gives detection ground truth; the coral gives realistic bytes (reef texture compresses
  much larger than a flat card) and, lacking tags, is scored on sharpness / contrast / reference
  similarity — the metrics the analyzer already computes.
- **Chunk model:** 300 **base64 chars** per BM message, 5 s/chunk.

### ⚠️ Message-count correction (required)
The existing HEIC DOE estimates messages as `bytes ÷ 300`. Real chunking is on the **base64-encoded**
stream, and base64 inflates size ~33%, so that undercounts by ~a third. This sprint MUST compute:

```
base64_len   = length of base64-encoded encoded-image bytes
message_count = ceil(base64_len / 300)
est_minutes   = message_count * 5 / 60
```

Report `bytes`, `base64_len`, `message_count`, and `est_minutes` for every result.

---

## 3. Transmission budget (power-bound)

| Band | Messages | ≈ Transmit time @5 s/chunk | Rule |
|---|---|---|---|
| **Ideal** | 50–75 | ~4–6 min | preferred target |
| **Feasible / risky** | 75–125 | ~6–10 min | acceptable |
| **Gated** | 125–200 | ~10–17 min | only with a *clear* quality gain; **hard cap ~15 min (~180 msgs)** |

Budget numbers are anchored on the **coral** image (realistic); the card is a lower bound.

---

## 4. Metrics

Reuse `tools/bm_reference_card_quality_v2.py` (format-agnostic) unchanged.

- **Card (detection):** AprilTag count, min/mean tag side px, tag sharpness (laplacian var,
  tenengrad), tag contrast, rectified-card sharpness, PASS/WARN/FAIL (all 4 tags required to PASS).
- **Coral (no tags):** rectified/full-frame sharpness (laplacian var, tenengrad), contrast
  (p95–p05), reference similarity (`ref_psnr_rgb`, `ref_laplacian_corr`, `ref_mse_rgb`) vs the
  1600-wide source.
- **Both (budget):** `bytes`, `base64_len`, `message_count`, `est_minutes`, duration band.
- **Partial (P2):** every metric above, recomputed on the **partial-decoded** frame at each
  received fraction.

---

## 5. Reuse vs. add

**Reuse as-is:** `bm_reference_card_quality_v2.py` (all detection/sharpness/contrast/PSNR),
`prepare_reference_images.py` (input prep), and the cut-sheet / heatmap **layout** patterns from
`bm_reference_card_heic_compression_sweep.py` and `bm_heic_sweep_post_analysis.py`.

**Add:** a JPEG encode path (Pillow — `Image.save(..., "JPEG", quality=q, progressive=bool)`, no
subprocess); a **format/mode axis** (`baseline`, `progressive`); the **base64 message-count** fix;
and a **truncation / partial-decode harness** (keep-first-X%-of-chunks → decode with
`ImageFile.LOAD_TRUNCATED_IMAGES` → guard on a minimum recovered fraction → run the analyzer on the
partial frame).

---

## 6. Work Tracker

One or more CC sessions, top-to-bottom. **P2 is compute-heavy** — if a session runs long, split it
by encoder (baseline session / progressive session) or by image (card / coral), each writing into
the *same* timestamped run folder. Update the row's status + findings in the PR.

| # | Phase | Goal | Status | Depends on | Compute | Output |
|---|-------|------|--------|-----------|---------|--------|
| P0 | **Scaffold** | JPEG encode (baseline+progressive), base64 message-count, format axis, truncation harness; smoke-test on 1 image × 1 quality | ✅ DONE | — | light | new sweep script + smoke run |
| P1 | **Coarse quality sweep** (complete images) | JPEG quality ladder (revised with approval to {5,6,7,8,9,10,15,20,25,30,35,40} — bias low, cap 40), baseline, both images; size/base64-msgs/minutes + detection (card) + sharpness/contrast/PSNR (coral) | ✅ DONE | P0 | light–med | quality-ladder cut sheets + `results_*.csv` (run `jpeg_20260722T003214Z`) |
| P2 | **Partial-transmission behavior** *(new)* | {baseline, progressive} × received-fraction {25,50,75,90,100}% × qualities {9,13,15} **on the adopted cell** (ROI 1600×900 → 1000×562, 1.6×; see `Sprint06_experiment_log.md` closing state); judge on card + worst-case corals (alt_03/alt_07) | ☐ TODO | P1 | **heavy** (splittable) | baseline-vs-progressive partial cut sheets + detection/sharpness-vs-% curves |
| P3 | **Budget overlay + verdict** | map every setting to the bands (coral-anchored); heatmap (quality × mode, duration-banded); ranked recommendation | ☐ TODO | P2 | light | heatmap + **recommendation table → JPEG values to try on the Pi** |
| P4 | **Pi validation** *(fast-follow — OUT OF SCOPE here)* | port winning `(mode, quality)` to the Pi encode path; validate on-device encode time / memory / stability over Tailscale SSH | ⛔ DEFERRED | P3 | — | separate sprint spec |

**Legend:** ☐ TODO · 🔄 IN PROGRESS · 🔍 IN REVIEW · ✅ DONE · ⛔ DEFERRED.

**How a session runs:** pick the first row that isn't ✅/⛔ whose dependency is ✅; work only that
one (or one split of P2); follow the repo-root `CLAUDE.md` and the workflow below; update the row.

---

## 7. Decisions for the agent to propose (inspect → options → Nick approves)

### [D1] New script vs. extend the HEIC sweep
| Option | Pros | Cons |
|---|---|---|
| **A. New `tools/bm_reference_card_jpeg_partial_sweep.py` (recommended)** | Keeps Sprint03 HEIC tooling reproducible; houses the new truncation dimension cleanly; imports the analyzer + copies shared cut-sheet helpers | Some helper duplication |
| B. Extend `bm_reference_card_heic_compression_sweep.py` with a format axis | Single sweep, less duplication | Risks destabilizing a known-good script; grows a 45 KB file; muddles HEIC reproducibility |

### [D2] Truncation model for "partial transmission"
Recommend **keep-first-X%-of-chunks** (the real tail-loss failure mode): truncate at the byte offset
for the first M of N 300-base64-char chunks, decode with `LOAD_TRUNCATED_IMAGES`, and **reject a
decode that recovers less than a minimum frame fraction** (so a blank/garbage frame isn't scored as
a real image). Not random-byte loss. The earlier partial-render experiment is the template.

---

## 8. Guardrails

- **Mac-side DOE only.** Add **new** files under `tools/` and this spec under `sprints/`, on a
  branch. **Do NOT touch runtime/transport:** `bm_serial.py`, `process_image_v2.py`,
  `main_pi_camera.py`, `heic_encode_helper.py`, `camera_schedule.yaml`. No Pi, no SSH (that is P4).
- **Preserve known-good.** Don't destructively edit the Sprint03 HEIC sweep unless [D1] picks
  "extend" with Nick's approval.
- **Follow the repo-root `CLAUDE.md`:** one variable at a time; timestamped self-contained run folder with
  `run_manifest.json`, CSVs, cut sheets, logs; all 4 tags required for a card PASS; label coordinate
  systems (native / crop / 1600-output); **trust the artifacts, not exit codes** — open the cut
  sheets and confirm images actually decoded.
- **Report base64 chars + message count**, never just bytes.
- **Workflow:** inspect first → propose a plan (options table for real choices) → wait for Nick's
  approval → build in small commits on a branch → validate artifacts. Nick reviews/merges.

---

## 9. End goal

A shortlist of JPEG `(mode, quality)` settings that, on the **coral** image, land in **50–125
messages / ≤15 min**; on the **card**, keep AprilTag detection (ideally all 4 tags); and under
**tail-loss**, still yield a usable partial (measured detection/sharpness on the partial frame).
Progressive is expected to win for partials — the data decides. Those values feed the Pi
validation fast-follow (P4).

---

## 10. Findings log
_(fill in as phases run — quality↔size↔detection curve, partial-render comparison, and the recommended settings)_

### P0 — Scaffold (2026-07-21, run `smoke_p0`)

- **Decisions:** [D1] new script `tools/bm_reference_card_jpeg_partial_sweep.py` (approved); [D2]
  keep-first-X%-of-chunks tail-loss model (approved). 300 base64 chars = exactly 225 raw bytes per
  message, so the truncation offset is exact.
- **Corner map verified:** all 4 AprilTags (IDs 0–3) detect at analyzer scale 1 on the 1600×900
  fixed-crop card; map is `tl:0,tr:1,bl:2,br:3` (matches the Sprint03 sweep, not the analyzer's
  3-tag default).
- **Smoke (card, q50, baseline+progressive, 50%/100% received)** behaved exactly as the sprint
  hypothesis: baseline@50% decodes the top ~57% of the frame, loses all 4 tags (they sit at
  y≈613–713 of 900) → FAIL; progressive@50% recovers the full frame, keeps 4 tags,
  PSNR 32.5 vs 37.3 at 100%. Coral no-tag branch scores sharpness/contrast/PSNR correctly.
- **Budget reality check:** at q50 the coral is 106 KB → **481 messages (~40 min, over cap)**;
  card 80 KB → 365 msgs. The base64-corrected count is ~⅓ higher than the old bytes÷300 estimate.
  The viable budget window will sit at much lower quality — that's P1's question.
- **Encode settings (assumption to validate in P4):** Pillow, `optimize=True` both modes, default
  4:2:0 chroma subsampling; progressive implies optimized Huffman tables, so optimize is kept on
  for baseline to keep the size comparison fair. Quality capped at 95 (Pillow >95 disables
  useful quantization).
- The "earlier partial-render experiment" referenced in §7-D2 was not found in the repo; the
  harness was implemented from the spec description directly.

### P1 — Coarse quality sweep (2026-07-22, run `jpeg_20260722T003214Z`)

- **Ladder revision (Nick-approved):** spec ladder {10,30,50,70,90} replaced with
  {5,6,7,8,9,10,15,20,25,30,35,40} ("bias low, cap 40") — P0 had shown q50 coral = 481 msgs, so
  q50+ was known-dead. A first pass ({5,10,…,40}, run `jpeg_20260722T003128Z`, superseded) showed
  the **entire coral feasible band falls between q5 and q10**, so q6–q9 were added and the full
  ladder re-run into one clean folder. Baseline mode only, 100% received (P2 owns mode/partials).
- **Budget (coral-anchored, base64-corrected):** q5 = 73 msgs / 6.1 min (**ideal**); q6 = 84,
  q7 = 96, q8 = 107, q9 = 120 (**feasible**); q10 = 133 (**gated**); q15 = 189 and everything
  above is **over the 180-msg cap** (q40 = 405 msgs / 33.8 min). The shippable baseline-JPEG
  window at 1600×900 is **q ≤ 9**, with q10–15 only as a gated stretch.
- **Card detection does not constrain the choice:** all 12 qualities PASS with all 4 AprilTags,
  min tag side flat at 27.4–28.0 px (tag geometry survives even q5). The card is also consistently
  ~10–25% smaller than the coral above q7 — confirming the coral is the right budget anchor
  (at q≤6 heavy quantization flattens reef texture enough that coral ≤ card).
- **Quality curve (coral vs lossless 1600×900 source):** PSNR 25.75 (q5) → 26.57 (q6) → 27.28
  (q7) → 27.97 (q8) → 28.37 (q9) → 28.87 (q10) → 30.48 (q15); ff_laplacian_var rises smoothly
  198 → 224 over q5–q10 (source-matched sharpness ~= q15+ levels never reachable in budget).
  Visual: q5 shows clear posterization/banding in dark water; **q8 full-res is usable** — reef
  structure and individual fish identifiable, mild blocking in shadows. All 24 rows decoded
  (decode_ok, recovered_fraction 1.0); cut sheets confirm no blank/garbage frames.
- **P2 shortlist proposal: qualities {5, 7, 9}** (ideal / mid-feasible / top-of-feasible),
  baseline vs progressive × received fractions — q10 optional as the gated stretch point.
- **Reproduce:**
  `/…/bm_cam_legacy/.venv/bin/python3 tools/bm_reference_card_jpeg_partial_sweep.py --images card coral --modes baseline --qualities 5 6 7 8 9 10 15 20 25 30 35 40 --fractions 100`
  → `~/Downloads/bm_jpeg_partial_sweep/jpeg_20260722T003214Z/` (results CSV, quality-ladder cut
  sheets for both images, decoded frames, analyzer output, `run_manifest.json`, log). No code
  changes were needed — the P0 script covered P1 as-is.

### P1 addendum — q5–q10 across ALL coral scenes (2026-07-22, run `multi_coral_20260722T004839Z`)

Robustness batch pulled forward at Nick's request: q{5,6,7,8,9,10} baseline × card + all 8 coral
scenes (primary + alt_01…07, prepared to synthetic native via `prepare_reference_images.py`).
No code changes — one sweep run per image via `--coral-path`, subfolders under one parent, plus
`combined_results_all_sources.csv` + `combined_manifest.json` at the parent level. All 54 rows
decoded (recovered 1.0); extremes verified visually on cut sheets.

**Message count (band: I ideal / F feasible / G gated / X over-cap):**

| source | q5 | q6 | q7 | q8 | q9 | q10 |
|---|---|---|---|---|---|---|
| card | 81 F | 92 F | 101 F | 110 F | 120 F | 129 G |
| coral_primary | 73 I | 84 F | 96 F | 107 F | 120 F | 133 G |
| alt_01 | 100 F | 124 F | 147 G | 170 G | 191 X | 213 X |
| alt_02 | 62 I | 71 I | 81 F | 92 F | 103 F | 115 F |
| alt_03 | 129 G | 155 G | 178 G | 199 X | 221 X | 242 X |
| alt_04 | 115 F | 139 G | 162 G | 183 X | 203 X | 222 X |
| alt_05 | 78 F | 93 F | 107 F | 122 F | 137 G | 153 G |
| alt_06 | 100 F | 120 F | 139 G | 159 G | 177 G | 195 X |
| alt_07 | 156 G | 187 X | 218 X | 247 X | 276 X | 303 X |

- **Scene content dominates the budget — spread is ~2.5× at fixed quality** (q5: 62–156 msgs).
  The primary coral is one of the *cheaper* scenes; it is not a conservative anchor. alt_07
  (high-texture close-up, ff_sharpness ~1000 vs ~200 primary) is gated even at q5; alt_03/alt_04
  exceed feasible at q5–q6.
- **Feasibility across all 9 sources:** q5 keeps 8/9 within gated-or-better (7/9 feasible-or-
  better); q7 keeps 5/9 feasible-or-better; q9–q10 only 2–3/9. **No fixed quality puts every
  scene in feasible** — worst-case scenes need q5 (or a byte-budget/adaptive-quality encode,
  noted as a P3 discussion point, "Next sprint" if pursued).
- PSNR ordering is stable across scenes (each +1 quality step ≈ +0.4–0.8 dB); alt_07 is lowest
  (22.4 dB @ q5) because fine texture is what quantization destroys first.
- **Implication for P2/P3:** keep shortlist {5, 7, 9} but judge the verdict on worst-case scenes
  (alt_03/alt_07), not just the primary.
- **Reproduce:** prepare alts (`tools/prepare_reference_images.py --input
  reference_images/reference_reef_coral_alt_NN.jpg --output-root reference_images/prepared`),
  then per image: `tools/bm_reference_card_jpeg_partial_sweep.py --images coral --coral-path
  reference_images/prepared/<name>/synthetic_native_4608x2592.jpg --modes baseline --qualities
  5 6 7 8 9 10 --fractions 100 --output <parent>/<name>` (card: `--images card`). Artifacts:
  `~/Downloads/bm_jpeg_partial_sweep/multi_coral_20260722T004839Z/`.

### P1 color verification + source-vs-compressed cut sheets (2026-07-22, run `multi_coral_srccmp_20260722T010924Z`)

Nick flagged "colors look incorrect" on the ladder cut sheets. Investigated whether it's a
pipeline bug or a compression effect:

- **Pipeline is color-correct.** All sources are sRGB (OM TG-7 EXIF Interop `R98`; no embedded
  ICC profiles anywhere, so nothing is dropped/misinterpreted). Mean RGB is preserved through
  prep → 1600×900 source → encode → decode (primary: 85.96/81.08/81.61 → 85.62/81.72/81.15 at
  q5); luminance percentiles are essentially identical (shadows slightly *lifted*, not crushed);
  and tile pixels in the rendered sheet measure equal (lum 83.4 source vs 83.3 q5).
- **The "wrong" look is chroma-variation loss from low-quality JPEG** (heavy chroma quantization
  + 4:2:0): local saturation retained vs lossless source at q5 is 48–88% depending on scene
  (primary 55%, alt_01 48%, alt_04 53%; recovering to ~90%+ by q9–q10). Equal-luminance but
  desaturated reef reads as darker/muddier to the eye. This is what the customer would receive —
  a real cost of the q5–q10 budget window, now measured.
- **Script additions (additive only, CLI unchanged):** `ff_chroma_sat` metric (mean |R−G|+|G−B|)
  in the CSV, and a new per-(image,mode) cut sheet `*_source_vs_compressed.jpg` — rows per
  quality: [lossless source | compressed] full frames (display-normalized) + [source | compressed]
  **1:1 center-crop detail panels**, with chroma-retention % in the labels.
- Rerun of card + all 8 corals at q{5–10} with the new sheets:
  `~/Downloads/bm_jpeg_partial_sweep/multi_coral_srccmp_20260722T010924Z/` (per-source subruns,
  `combined_results_all_sources.csv`, `combined_manifest.json`). All 54 rows decoded; budget
  numbers identical to the previous multi-coral run (encode path untouched).
- **Chroma retention (% of source) at q5 / q8 / q10 per scene:** card 68/97/104 · primary
  55/83/92 · alt_01 48/63/73 · alt_02 80/89/95 · alt_03 59/69/85 · alt_04 53/84/96 ·
  alt_05 88/88/92 · alt_06 87/91/97 · alt_07 78/83/92. Takeaway: q5 color cost is scene-
  dependent and can be severe (≈half the color variation gone); q8–q9 keeps ~85–95% on most
  scenes. Color fidelity now argues for the upper feasible band (q8–q9) where budget allows.

### Crop-vs-size probe (2026-07-22, scratch measurement — answers Nick's HEIC-era trend question)

Question: with JPEG, does a tighter crop reduce file size, and could it buy q9–q10 within budget?
Scratch probe (no changes to the sweep tool; identical Pillow encode settings — optimize=True,
4:2:0, baseline) on coral_primary + coral_alt_07 at q9/q10, 16:9 crops centered at native center:

- **E1 — tighter crop, output fixed 1600×900** (sampling density 1.92×→1.0×): file size is
  **flat** — primary q9: 26.2→26.5→26.4→26.1→24.7 KB (crop 3072→1600 wide); alt_07 q9:
  60.5→…→57.7 KB. The HEIC-era trend **holds for JPEG**: at constant output resolution, crop
  tightness barely moves bytes (≤~6%, only at full 1:1 density).
- **E2 — tighter crop, density fixed 1.92×** (output shrinks with crop): messages scale
  ~linearly with output pixel area — primary q9: 120→96→74→55→36 msgs for outputs
  1600×900→1400×788→1200×675→1000×562→800×450; alt_07 q9: 276→220→169→124→83.
- **Conclusion: output pixel count is the budget lever, not crop tightness.** A tighter crop
  helps q9/q10 *only* by shrinking the output; equivalently the same message budget can be spent
  on FOV (wide crop, more downsample) or per-pixel detail (tight crop, less downsample). To hold
  q9 feasible on the worst-case scene (alt_07) needs ≈1000×562 output (124 msgs) — i.e. a
  1920×1080 native crop at 1.92× density (39% of current FOV area), or the full FOV downsampled
  to ~1000 wide. Geometry is a Sprint02-style axis; if pursued, smallest change = add
  `--crop-native` / `--output-width` flags to the sweep tool (defaults = current values).
  Probe CSV: session scratchpad `crop_size_probe.csv` (regenerable from this description).

### Output-width sweep at q9 (2026-07-22, run `width_sweep_q9_20260722T013001Z`)

Geometry flags added to the sweep tool (`--crop-native`, `--output-width`; defaults byte-identical
to sprint-fixed behavior, regression-checked). Sweep: output width {1600,1400,1200,1000,800} at
**q9 baseline, FOV constant** (sprint crop 768,432,3072,1728 → downsample 1.92×–3.84×), all 9
sources, one self-contained subrun per (width, source) + `combined_results_width_sweep.csv` +
per-source width-ladder sheets (`cut_sheets_width_ladder/`).

**Messages at q9 (I/F/G/X bands):**

| source | w1600 | w1400 | w1200 | w1000 | w800 |
|---|---|---|---|---|---|
| card | 120 F | 98 F | 77 F | 59 I | 42 I |
| coral_primary | 120 F | 95 F | 73 I | 55 I | 38 I |
| alt_01 | 191 X | 148 G | 109 F | 75 I | 47 I |
| alt_02 | 103 F | 80 F | 59 I | 43 I | 30 I |
| alt_03 | 221 X | 177 G | 135 G | 98 F | 66 I |
| alt_04 | 203 X | 160 G | 120 F | 86 F | 57 I |
| alt_05 | 137 G | 107 F | 80 F | 58 I | 40 I |
| alt_06 | 177 G | 143 G | 108 F | 79 F | 54 I |
| alt_07 | 276 X | 217 X | 162 G | 115 F | 74 I |

**Card AprilTag detection vs width (q9):** w1600 PASS (27.4 px) · w1400 PASS (24.5) · w1200 PASS
(21.0) · w1000 WARN (17.3, all 4 detected) · **w800 FAIL (tags 1,3 lost, 13.6 px)**. The
detection knee sits between 1000 and 800 output width at this FOV.

- **w1200 is the standout compromise at q9:** every scene ≤ gated (worst 162), 7/9 feasible-or-
  better, card still PASS with margin. w1000 puts even alt_07 in feasible (115) but the card
  drops to WARN. w800 fixes every budget (all ideal) but breaks detection — not shippable where
  the card matters.
- Full-frame legibility holds surprisingly well down to w1000 (see width-ladder sheets; same-ROI
  rows show the per-pixel softening honestly — lower widths upsampled for display).
- Candidate P2/P3 cells now include geometry: (q9, w1200) and (q9, w1000) vs (q5–q7, w1600) —
  similar budgets, different failure modes; partial-transmission behavior (P2) should decide.

### Card clipping check + centered-crop control, q7–q13 (2026-07-22, run `card_center_grid_20260722T020707Z`)

Nick asked whether the w800 detection FAIL was a false fail from the crop clipping the card.

- **No clipping — measured, not assumed.** Tag bounding box in the sprint crop (output coords,
  1600×900): x 600–962, y 598–728 → margins left 600 / right 638 / top 598 / bottom 172 px; the
  card is already near-centered horizontally (center x=781). The annotated w800 analyzer frame
  shows the full card in-frame with all margins intact — tags 1/3 visible but too small to lock.
- **Centered-crop control run** (same-size 3072×1728 crop re-centered on the card at native
  731,841 via the existing `--crop-native` flag) × width {1600…800} × **q7–q13**, vs the standard
  crop. Result matrices (P/W/F + tags detected): the two crops are statistically identical —
  min tag px matches to ±0.4 (e.g. w800: 13.56 standard vs 13.95 centered). w1600–w1200 PASS-4
  at every quality in both; w1000 is a flaky WARN-4 boundary in both (occasional 3-tag drop at
  random qualities); w800 FAILs in both (0–3 tags), with only isolated marginal 4-tag WARN locks
  at q12–q13 centered — boundary noise, not a robust rescue.
- **Quality does not rescue small tags:** detection is flat across q7–q13 at every width
  (consistent with the P1 q5–q40 finding). Tag *pixel size* is the detection variable:
  ≥21 px (w1200) robust PASS · ~17 px (w1000) marginal WARN · ~14 px (w800) below the reliable
  floor.
- **Recommendation: no "force crop centered on card" flag** — centering measurably changes
  nothing here, and `--crop-native` already covers deliberate re-framing. To keep detection at
  low output widths the options are physical/geometric: hold output ≥1200 wide, print larger
  tags, or frame the card tighter (FOV trade — Sprint02-type ROI decision, field-side).
- Artifacts: `~/Downloads/bm_jpeg_partial_sweep/card_center_grid_20260722T020707Z/`
  (`standard|centered/w<width>/` subruns, `combined_card_center_grid.csv`). Reproduce (centered):
  `--images card --qualities 7 8 9 10 11 12 13 --crop-native 731 841 3072 1728 --output-width <W>`.

### Order-of-operations clarification + tight-ROI demo (2026-07-22)

Nick's mental model: native → downsample (density) → crop (ROI) → compress. The script does
native → **crop** → **downsample** → compress — but the two orders **commute** (same pixels at
the same density), so the model's prediction holds: *at fixed density, the crop only changes how
much data is compressed, not the per-pixel quality of what remains*. The width sweep changed
**density** (crop fixed, output shrunk), which is why tags degraded — it swept the other knob.

Demo run confirming the model (card, q9, card-centered **1920×1080 native crop** at unchanged
**1.92× density** → 1000×562 output): **PASS, 4 tags, min tag 27.8 px, 56 msgs (ideal)** —
tag size identical to full-FOV w1600 (27.4 px) at less than half the messages, and PASS where
full-FOV w1000 (3.07× density, 59 msgs) only WARNs. FOV is the only cost (39% of sprint-crop
area). Reproduce: `--images card --qualities 9 --crop-native 1307 1165 1920 1080 --output-width
1000`. **Two-knob summary:** `--crop-native` = ROI/FOV = how much data; density (crop_w ÷
output_w) = per-pixel detail; `--output-width` sets density for a given crop. A field decision
to tighten the camera's ROI on the card region would preserve detection at ideal-band budgets.

### Minted proposal vs baseline, all sources at q9 (2026-07-22, run `proposal_vs_baseline_q9_20260722T022941Z`)

**Proposal (minted):** q9 baseline-mode JPEG · density 1.92× (unchanged) · **ROI 1920×1080
native → 1000×562 output** (card-centered `1307,1165` for the card; scene-centered `1344,756`
for corals) — trade 61% of FOV area for budget, keep per-pixel quality identical.

| source | baseline msgs (band) | proposal msgs (band) | quality carry-over |
|---|---|---|---|
| card | 120 F | **56 I** | PASS→PASS, min tag 27.4→27.8 px |
| coral_primary | 120 F | **55 I** | PSNR 28.4→27.8 |
| alt_01 | 191 X | **80 F** | 26.1→25.8 |
| alt_02 | 103 F | **43 I** | 28.4→28.0 |
| alt_03 | 221 X | **97 F** | 25.8→25.2 |
| alt_04 | 203 X | **100 F** | 26.1→25.1 |
| alt_05 | 137 G | **64 I** | 27.1→26.0 |
| alt_06 | 177 G | **77 F** | 27.1→26.5 |
| alt_07 | 276 X | **124 F** | 24.6→24.0 |

**Every scene lands ideal (4/9) or feasible (5/9)** — nothing gated, nothing over cap — vs the
baseline's 4/9 over-cap. Card detection unchanged at full margin. PSNR deltas are small and are
per-arm (each vs its own lossless source; ROIs differ in content, so treat as per-pixel fidelity,
not identical-content comparison). Chroma_sat differences track ROI content.
Cut sheets (one per source): `cut_sheets_proposal_vs_baseline/` — baseline frame with proposal
ROI outlined, proposal frame, and 1:1 details at the same scene point (visually identical per
pixel, as designed). Combined CSV: `combined_proposal_vs_baseline.csv`.
Reproduce (coral): `--images coral --coral-path <prepared native> --qualities 9 --crop-native
1344 756 1920 1080 --output-width 1000`; card: `--images card --qualities 9 --crop-native 1307
1165 1920 1080 --output-width 1000`. Baseline arm: same minus the two geometry flags.
**Open items before deployment:** P2 partial-transmission on this cell (baseline vs progressive),
Pi encode parity/memory (P4), and the ROI is a science/deployment decision (39% of current FOV).

### ROI 1600×900 density × quality grid (2026-07-22, run `roi1600_density_grid_20260722T024040Z`)

Nick shrank the ROI again (**1600×900 native**, card-centered `1467,1255` / scene-centered
`1504,846`) to buy quality headroom, and asked whether to run **max starting density (1.0×, no
downsample) and let JPEG do all the reduction**. Grid: density 1.0×–2.0× (output width
1600→800) × q{9,11,13,15} × all 9 sources; every cell also scored with **common-reference PSNR**
(decoded frame upscaled to the 1.0× lossless source of the same ROI — cross-density comparable,
unlike per-run PSNR). Note: in this pipeline downsampling is a resample, not a compression
generation — each cell is native → one resample → one encode, so the question is purely rate
allocation (many soft pixels at low q vs fewer sharp pixels at high q).

- **Answer: max density loses at these budgets.** The best under-budget cell is *never* 1.0×:
  ideal-band winners sit at 1.6–2.0× density with q13–q15 (e.g. primary: w1000/1.6× q13, 73
  msgs, cPSNR 29.45 — vs 1.0× q9 at 113 msgs scoring 29.41); feasible-band winners at
  1.14–1.6× (primary: w1200 q15, 110 msgs, 30.72; card: w1400 q15, 123 msgs, 33.07 PASS).
  Bytes spent on quality (q13–15) at moderate density consistently beat bytes spent on native
  density at q9–11. Pattern: as budget loosens, the optimum shifts toward higher density —
  but within our bands it never reaches 1.0×.
- **Card:** every grid cell PASSes (tags ≥ ~26 px even at 2.0× thanks to the tight ROI) —
  detection is no longer a constraint inside this ROI.
- **Worst case alt_07:** still no ideal-band cell (best feasible: w1000 q9, 124 msgs, 24.26) —
  texture-heavy scenes remain the argument for the adaptive-q encoder.
- **Emerging operating point: ROI 1600×900 · w1000 (1.6× density) · q13 nominal, adaptive down
  to q9** → ideal band for 6/9 scenes at q13, all 9 within feasible at the q9 floor.
- Artifacts: 45 subruns (`w<width>/<source>/`), `combined_roi1600_density_grid.csv`
  (includes `common_ref_psnr`), per-source 5×4 grid sheets in `cut_sheets_density_grid/`
  (band-colored tiles). Reproduce (coral): `--images coral --coral-path <prepared> --qualities
  9 11 13 15 --crop-native 1504 846 1600 900 --output-width <W>`; card uses
  `--crop-native 1467 1255 1600 900`.

### CLOSEOUT — production HEIC vs proposed JPEG (2026-07-22, `heic_vs_jpeg_closeout_20260722`)

Apples-to-apples end-to-end: the **production HEIC pipeline** (from `camera_schedule.yaml`
"validated bmcam000 production candidate": crop 768,432,3072,1728 → 2688×1512 lanczos → HEIC q20
via pillow_heif, `heic_encode_helper.py` flow — Mac emulation, Pi parity is P4) vs the **minted
JPEG proposal** (ROI 1600×900 native → 1000×562, 1.6×, q13 baseline, adaptive floor q9). Chunk
model per `bm_serial`: 300 b64 chars / 5 s.

| source | production HEIC q20 @2688 | proposed JPEG q13 @1000 |
|---|---|---|
| card | 29.7 KB → **136 msgs (11.3 min)** | 15.4 KB → **71 msgs (5.9 min, ideal)** PASS, 33.3 px tags |
| coral_primary | 45.0 KB → **205 msgs (17.1 min, over cap)** | 16.0 KB → **73 msgs (6.1 min, ideal)** |
| coral_alt_07 | 107.5 KB → **490 msgs (40.8 min)** | 37.1 KB → 169 gated; **q9 floor → 124 (feasible)** |

- **Budget:** proposal costs ~⅓–¼ of production per image; production is over the hard cap on
  both reef scenes (the field symptom that started this sprint, quantified).
- **Honest visual note (see sheets):** in the same-scene zooms the HEIC arm's *pixels* look
  smoother — it spends 2.8–4× the messages and runs at 1.14× density. Per message delivered,
  the JPEG arm wins decisively, and the HEIC numbers are not deliverable inside the cap anyway.
- **Failure mode:** HEIC tail-cut → blank (B6); JPEG tail-cut → partial image renders. This
  asymmetry is the sprint's founding premise and stands regardless of visual comparison.
- Sheets (`cut_sheets/`): per source — full frames (proposal ROI red-boxed on the HEIC frame),
  card region at TRUE 1:1 both arms, reef same-scene-region zooms (display-normalized, labeled).
  Stats: `closeout_stats.json`; manifest in run folder.
