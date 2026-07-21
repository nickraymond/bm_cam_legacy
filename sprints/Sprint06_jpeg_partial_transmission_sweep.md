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
| P0 | **Scaffold** | JPEG encode (baseline+progressive), base64 message-count, format axis, truncation harness; smoke-test on 1 image × 1 quality | ☐ TODO | — | light | new sweep script + smoke run |
| P1 | **Coarse quality sweep** (complete images) | JPEG quality {10,30,50,70,90}, baseline, both images; size/base64-msgs/minutes + detection (card) + sharpness/contrast/PSNR (coral) | ☐ TODO | P0 | light–med | quality-ladder cut sheets + `results_*.csv` |
| P2 | **Partial-transmission behavior** *(new)* | {baseline, progressive} × received-fraction {25,50,75,90,100}% × shortlisted qualities × both images; decode partial + analyze | ☐ TODO | P1 | **heavy** (splittable) | baseline-vs-progressive partial cut sheets + detection/sharpness-vs-% curves |
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
