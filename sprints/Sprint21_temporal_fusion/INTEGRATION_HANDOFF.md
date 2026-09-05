# Handoff — integrate the daily reef composite into the Nereus backend + gallery

**For:** the agent picking up backend/frontend integration
**From:** Sprint 21 (bm_cam_legacy), complete 2026-09-05
**Read first:** `sprints/Sprint21_temporal_fusion/SPEC.md` (recipe + gates) and
`DECISION.md` (what was decided and why). Evidence: `runs/sprint21_day{0..4}_*/REPORT.md`.

You do not need to re-derive any of the analysis. This document tells you what to build,
where it plugs in, and the three rules you must not break.

---

## 1. What exists today

**Proven, in `bm_cam_legacy`, running on a Mac against downloaded frames:**

| File | Role |
|---|---|
| `tools/bm_temporal_fuse.py` | the fusion itself. Registration, weighting, the fusion ladder, stability/coverage maps |
| `tools/bm_grvi_correct.py` | Nereus v3 colour correction (production, already merged, PR #52) |
| `tools/bm_reef_frame_intake.py` | archives frames with provenance; reference for the delivery join |
| `tools/bm_reef_change_sensitivity.py` | change measurement; **not** for integration yet, see §6 |

**Backend, in `nereus-vision-dev`:**

| File | What it gives you |
|---|---|
| `backend/app/models.py` | `Media` with `r2_key`, `display_key`, `is_complete`, `percent_received`, `expected_chunks`, `received_chunks`, `captured_at_utc`, `device_id` |
| `backend/app/services/image_derivatives.py` | the existing derivative pattern: build bytes, pick a key, upload to R2, best-effort, never fatal to ingest |
| `backend/app/services/poll_once_ingest.py` | where per-image derivatives are created during ingest (~lines 771, 883, 1341) |
| `backend/app/r2.py` | `r2_client()`, `bucket_name()` |
| `backend/app/main.py` | `/media/timeline` (~line 593) is what the gallery consumes; `/gallery.html` serves `backend/dashboard/gallery.html` |

Partial images already store their received bytes in R2 under a `partial/` prefix
(`make_bm_partial_r2_key`), and the backend decodes only the clean prefix before the first
gap — it never splices across a gap. That is the correct input; see rule 2.

## 2. What to build

**A. A composite builder service.** `backend/app/services/composite_builder.py`.

Input: all image `Media` rows for one `device_id` whose `captured_at_utc` falls on one UTC
calendar day. Output: two images and a small stats blob.

Port the fusion from `tools/bm_temporal_fuse.py`. The functions you need are pure numpy and
OpenCV: `srgb_to_linear`, `linear_to_srgb`, `register`, `warped`, `static_mask`, `fuse`
(rung `L6_multiscale`), and `_bands`. Settings that were validated: `--detail-exp 0.25`,
registration threshold 0.5 px, new epoch above ~2 px. **Do not port `normalise_to_reference`
into the production path** — see rule 3.

**B. Storage.** Follow the existing derivative key convention:

```
<device_id>/bm_sofar/composite/<YYYY>/<MM>/<DD>/<system>_<device>_<YYYYMMDD>_composite.jpg          # rendered, for display
<device_id>/bm_sofar/composite/<YYYY>/<MM>/<DD>/<system>_<device>_<YYYYMMDD>_composite.raw.jpg      # fused, pre-colour, for measurement
<device_id>/bm_sofar/composite/<YYYY>/<MM>/<DD>/<system>_<device>_<YYYYMMDD>_stability.png          # optional
```

**C. A row to hang it on.** Recommendation: a **new table**, not a new `MediaType`. `Media`
carries chunk and completeness semantics that are meaningless for a composite
(`expected_chunks`, `is_complete`, `idempotency_key` on a Sofar filename), and overloading
it will confuse every existing query. Suggested `MediaComposite`: `id`, `system_id`,
`device_id`, `composite_date` (date, UTC), `r2_key`, `raw_r2_key`, `display_key`,
`source_media_ids` (JSON), `frame_count`, `mean_percent_received`, `max_drift_px`,
`epoch_id`, `built_at_utc`, unique on `(device_id, composite_date)`.

**D. A trigger.** A scheduled job after the device's transmit window closes is simplest and
safest. Do not rebuild on every ingest: a composite is worth building once per day, and
rebuilding on each chunk would be wasteful and racy. Make it idempotent on
`(device_id, composite_date)` so a re-run replaces cleanly.

**E. API + gallery.** A `/media/composites` endpoint mirroring `/media/timeline`'s shape
(`device_id`, `start_utc`, `end_utc`, list of `{composite_date, image_url, frame_count,
mean_percent_received}`). In `backend/dashboard/gallery.html`, surface it as a day-level
view alongside the frame timeline. Label it plainly, e.g. "Daily composite, 5 frames".

## 3. Three rules you must not break

**Rule 1 — one calendar day, never across days.** Measured: fusing a single day's frames
gives 0.995 of a single frame's detail; fusing the same hour across days gives 0.897; fusing
everything gives 0.67. Across days the reef's fine texture genuinely differs because
turbidity and light change the contrast of the same edge.
*Carve-out:* comparing across days is fine and is how change detection will work later. A
multi-day **statistic** is not a multi-day **fusion**.

**Rule 2 — fuse the raw received bytes, not the display derivative.** The display derivative
is a quality-85 re-encode of an already-truncated stream, so it adds a compression
generation. Read the `partial/` object (or the complete original) and decode once.

**Rule 3 — two outputs, diverging at the last step.** Fuse raw, then run Nereus v3 **once**
on the composite for display. Persist the pre-colour fused image too, because change
measurement runs on that and never on the rendered one. Rendering halves change sensitivity
(10.6% → 25.6%), and any per-composite colour correction is worse still (50–115%): the
colony-versus-substrate ratio already cancels the water cast for free, while a colour fit
does not. Do not "improve" this by colour-correcting before fusing; that was tested and
rejected.

## 4. The dependency that will bite you

The **display** half needs `tools/bm_grvi_correct.py`, which needs
`reference_card_color_utils.py`, `bm_reference_card_quality_v2.py`, the V2 card template
directory, `render_targets_p9.json`, **AprilTag detection via OpenCV's aruco module**, and
scipy. That is a heavier dependency than the backend currently carries, and card detection
takes seconds per image.

Two ways forward, your call with Nick:
- **Vendor it** into the backend as a service, with the template and preset JSON as package
  data. Straightforward, but adds opencv-contrib and scipy to the backend image.
- **Split the job**: build and store the raw fused composite in the backend (numpy plus base
  OpenCV only), and render asynchronously in a worker that has the colour stack.

The fusion half alone needs only numpy and OpenCV. If the colour dependency is a problem,
ship the raw composite first — it is still a better picture than any single frame, just
green.

## 5. Acceptance criteria

Build these as tests before wiring the UI.

1. **Correctness against a known answer.** `tools/bm_temporal_fuse_validate.py` manufactures
   ground truth by simulating the whole capture and delivery chain. Port that fixture. The
   composite must beat the best single input frame on SSIM. Reference numbers: 0.8824
   against 0.8735.
2. **Reproducibility.** Composites built from odd- and even-indexed halves of a day must
   agree more closely than any two single frames. Reference: 2.0–2.5 against 3.4–4.5 RMS.
3. **Epoch safety.** Feed frames with a >2 px shift injected. The builder must refuse and
   flag a new epoch rather than producing a ghosted composite.
4. **Degenerate days.** One frame, zero frames, and frames of differing dimensions must all
   fail cleanly without a partial write.
5. **Idempotency.** Two runs for the same `(device_id, composite_date)` produce one row and
   one set of objects.
6. **Never fatal to ingest.** Follow the existing derivative convention: a composite failure
   logs and returns, and never breaks image ingest.

## 6. Explicitly out of scope

- **Change and bleaching flags.** The sensitivity work is a pilot: three days of baseline,
  an unverified null, and modelled rather than measured bleached-coral spectra. Building the
  flag now would ship a threshold nobody can defend. What to build instead: persist the raw
  composite so a baseline accumulates from today.
- **Cross-day composites**, tile rotation, and any per-frame colour normalisation. All three
  were tested and rejected; see `SPEC.md` §3–4.

## 7. Context you should know

Every frame in the pilot was a **partial delivery**. `bmcam001` has not delivered a complete
image since at least 2026-09-01 — a Spotter timing bug, tracked as `TODO-SPOT-001`, owned by
Nick. Your composite must therefore treat partial frames as the normal case, weight them by
`percent_received`, and never assume `is_complete` is ever true. When that bug is fixed the
composite gets better on its own, with no code change.

## 8. Method warning

Four metric bugs were found during this sprint, all of one family: **a comparison that does
not fix every anchor it shares measures the anchors, not the thing.** Two split-half checks
anchored each half on a different reference frame; one detail metric chose its edge
locations from the reference frame, making its own gate unreachable; one cache was keyed on
a numpy array's memory address, which Python reuses after the array is freed. Each produced
a confident wrong answer. If you add a quality gate, check it against that family first.
