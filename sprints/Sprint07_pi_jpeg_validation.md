# Sprint 07 — Pi validation of the Sprint06 JPEG settings (fast-follow, P4 of Sprint06)

**Status:** Draft for review · **Created:** 2026-07-22 · **Owner:** Nick
**Repo:** `bm_cam_legacy` · **Scope:** On-device (Pi Zero 2W over Tailscale SSH) validation of the
Sprint06 Mac-side verdict. Read the repo-root `CLAUDE.md` first; this spec is the source of truth.
**Follows:** `Sprint06_jpeg_partial_transmission_sweep.md` (P3 verdict) + `Sprint06_experiment_log.md`.

---

## 1. Why this sprint exists

Sprint06 (Mac-side DOE) produced a deployable JPEG shortlist on the frozen geometry
(ROI 1600×900 native → 1000×562, 1.6× density):

1. **progressive q13 (nominal)** — worst coral 169 msgs / 14.1 min, card 4-tag lock from 50% received
2. **progressive q9 (adaptive floor)** — worst 126 msgs; card-bearing frames floor at q13
3. **progressive q15 (stretch)** — worst 188 msgs, inside the field-tested 195-msg cap
4. **baseline q9 (control)** — on-device A/B reference

All byte sizes and encode behavior are **Mac Pillow emulations**. This sprint replicates the
analysis on the Pi hardware to confirm the settings survive contact with the real encoder,
memory limits, and clock — and to **pare down the upper quality limit** from a Pi-measured
heatmap rather than the Mac one.

### The 18-minute rule (per-image cycle budget)

The total per-image cycle — capture → crop/downsample → JPEG encode → transmit — must not
exceed **18 minutes**. Transmit at the 195-msg cap is 16.25 min (5 s/msg), leaving **≤ ~1.75 min
for capture + processing + encode at the worst-case cell**; at the q13 nominal (worst 169 msgs,
14.1 min) the margin is ~3.9 min. Pi-side encode/processing time is therefore a first-class
measurement here, not an afterthought.

---

## 2. Fixed for the whole sprint (from Sprint06 — do not re-sweep)

- **Geometry frozen:** ROI 1600×900 native (card-centered `1467,1255` / scene-centered
  `1504,846`) → 1000×562 output, lanczos. Native coords are 4608×2592 sensor-equivalent.
- **Chunk model:** 300 base64 chars per BM message, 5 s/msg; `message_count = ceil(base64_len/300)`.
- **Bands (messages):** ideal ≤75 · feasible ≤125 · **hard cap 195** (field-tested).
- **Inputs:** the committed reference set — `reference_images/reference_card_native_imx708.jpg`
  and `reference_images/prepared/*/synthetic_native_4608x2592.jpg` (primary + alt_01…07, committed
  2026-07-22 exactly so the Pi can run the identical DOE).
- **Metrics/analyzer:** `tools/bm_reference_card_quality_v2.py` unchanged; sweep tool
  `tools/bm_reference_card_jpeg_partial_sweep.py`; verdict tool `tools/bm_jpeg_p3_budget_verdict.py`.

**New variables this sprint (the only ones):** encoder platform (Pi vs Mac), encode wall time,
peak memory, and end-to-end cycle time.

---

## 3. Work Tracker

| # | Phase | Goal | Status | Depends on | Output |
|---|-------|------|--------|-----------|--------|
| P0 | **Deploy + parity smoke** | Get branch + reference images + venv deps onto the Pi (Tailscale SSH; see `pi-deploy`); encode 1 image × q13 × both modes on-device; compare bytes/base64_len vs the Mac run (`p3_verdict_20260722T055437Z`) | ✅ DONE | — | **PASS — byte-identical (0.0% delta, sha256 match, card + coral_primary × both modes).** Run `p0_parity_20260724T164924Z` + `parity_report.md` in `~/Downloads/bm_jpeg_partial_sweep/`; new tool `tools/bm_pi_jpeg_encode.py` |
| P1 | **Pi heatmap re-run** | Sweep on the Pi: both modes × q{7,9,11,13,15,17} × all 9 sources, 100% received, **with per-encode wall time + peak RSS logged**; run `bm_jpeg_p3_budget_verdict.py` on the Pi CSVs → Pi-native heatmaps + ranked table | ✅ DONE | P0 | **108/108 cells sha256-identical to Mac; ranked table identical; shortlist confirmed (q13→169, q9→126, q15→188, q17 dead at 206). Encode ≤0.08 s, RSS ~122 MB.** Runs `p1_grid_20260724T165653Z` (Pi) + `p1_pi_analysis_20260725T063719Z` (Mac, heatmaps + `p1_report.md`); new tool `tools/bm_mac_analyze_pi_run.py` |
| P2 | **18-min cycle check** | Time the full cycle on-device: capture (or timed load of a native) → crop/downsample → encode → transmit-time model; verify worst-case cell ≤ 18 min total; measure encode memory headroom (Pi Zero 2W, watch CMA/RSS) | ✅ DONE | P1 | **PASS all cells; even at the 195-msg cap total = 16.4 min (1.6 min margin). Pipeline ~7.8 s (capture 5.3 + prep 2.4 + encode ≤0.06). ⚠ CmaFree bottoms at 1.9 MB during native capture — cma=128M is a hard floor.** Run `p2_cycle_20260724T170930Z` (`p2_report.md`, `p2_cycle_table.csv`); new script `tools/bm_pi_cycle_time_p2.sh` |
| P3 | **Pare the upper limit + final verdict** | From Pi bytes + cycle times, set the shipping upper quality limit (is q15 stretch still inside cap on Pi? does q17 stay dead?); confirm nominal/floor; final `(mode, q_nominal, q_floor, q_max)` | ✅ DONE | P2 | **Final: (progressive, q_nominal=13, q_floor=9 [card frames floor at q13], q_max=15 gated by a pre-transmit size check; q17 dead at 206 msgs).** See §7 Final verdict + deployment handoff |
| P4 | **Truncated-progressive render check** | Confirm backend/frontend render a tail-cut progressive JPEG (B6 emulation with real truncated files from P1) — the deployment premise | ✅ DONE | P1 | **HOLDS: 29/30 progressive tail-cuts render via the real backend derivative code (full frame from 25% received); ~0.5–4% of cut points decode black and are correctly rejected to placeholder.** Run `p4_render_20260725T070626Z` (`p4_report.md`, evidence cut sheets); new tool `tools/bm_p4_partial_render_check.py` |

**Legend:** ☐ TODO · 🔄 IN PROGRESS · 🔍 IN REVIEW · ✅ DONE · ⛔ DEFERRED.
Session rule as in Sprint06: first non-✅/⛔ row whose dependency is ✅.

---

## 4. Guardrails (field hardware — CLAUDE.md §15/16 apply in full)

- **Protect field operations:** before any camera/encode work — check for running camera
  processes (`main_pi_camera.py --transmit` can own `/dev/video*`), back up crontab before
  disabling, restore after, record every change. No reboot loops; document any
  `/boot/cmdline.txt` / CMA change with a restore command.
- **Do NOT modify runtime/transport:** `bm_serial.py`, `process_image_v2.py`,
  `main_pi_camera.py`, `heic_encode_helper.py`, `camera_schedule.yaml`. This sprint validates;
  deployment edits are a separate, reviewed change.
- **Reversible only:** copy production scripts before patching anything; new files under
  `tools/`; timestamped self-contained run folders (manifest, CSV, cut sheets, logs) pulled back
  to the Mac for review.
- **Trust the artifacts:** compare Pi CSV row counts / byte sizes against the Mac run before
  drawing conclusions; a sweep that "finishes" with empty folders is a failure.

---

## 5. End goal

A Pi-measured heatmap and cycle-time table that either confirms the Sprint06 shortlist
(progressive q13 nominal / q9 floor / q15 stretch) or pares it down, an explicit shipping
**upper quality limit**, proof the 18-min per-image budget holds worst-case, and evidence the
backend renders truncated progressive JPEGs. Output feeds the actual deployment change
(separate, reviewed PR against the runtime scripts).

---

## 6. Findings log

### P0 — Deploy + parity smoke (2026-07-24, run `p0_parity_20260724T164924Z`) — PASS

- **bmcam000 confirmed as the real target:** Raspberry Pi Zero 2 W Rev 1.0, Debian 11
  Bullseye aarch64 (64-bit), MemAvailable ~230 MB, CmaTotal 131072 kB / CmaFree ~109 MB.
- **Design decision (Nick):** the Pi does image altering only (crop → downsample → encode);
  ALL quality analysis (metrics + AprilTag) stays on the Mac with the unchanged Sprint06
  tooling. New encode-only tool `tools/bm_pi_jpeg_encode.py` (Pillow + stdlib, no cv2) —
  relevant because the Pi's system OpenCV is 4.5.1, which predates the
  `cv2.aruco.ArucoDetector` API the frozen analyzer uses.
- **Perfect encoder parity:** all 4 cells (card + coral_primary × baseline/progressive, q13,
  frozen geometry → 1000×562) are **byte-identical** to the Mac P3 run — same jpeg_bytes /
  base64_len / message_count, sha256 match on the JPEG files; working sources pixel-identical
  (Pi Pillow 11.3.0 / libjpeg-turbo 3.1.1 vs Mac Pillow 12.3.0 / turbo 3.1.4.1). Every
  Sprint06 Mac byte number transfers to the Pi unchanged → P1's Pi heatmap should replicate
  the Mac heatmap exactly on bytes; its real payload is wall time + RSS.
- **First timing datapoints (for P2):** Pi encode ~0.03 s baseline / ~0.06 s progressive;
  native load + crop + lanczos ~2.2–2.8 s; peak RSS ~123 MB. Trivial vs the 18-min budget.
- **Field ops:** no packages installed, camera + crontab untouched (verified only
  `@reboot run_capture_cycle.sh` present; no camera processes). Pi repo
  `~/repos/bm_cam_legacy` now on branch `claude/sprint-07-pi-jpeg-28a4c9`
  (restore: `git checkout main`).
- Artifacts: `~/Downloads/bm_jpeg_partial_sweep/p0_parity_20260724T164924Z/`
  (`parity_report.md`, `parity_delta.csv`, per-source manifests/CSVs/JPEGs); Pi-side copy in
  `~/bm_sprint07_runs/`.

### P1 — Pi heatmap re-run (2026-07-24, runs `p1_grid_20260724T165653Z` Pi / `p1_pi_analysis_20260725T063719Z` Mac) — CONFIRMED

- **Full-grid byte parity:** all **108/108** Pi JPEGs (9 sources × both modes × q{7,9,11,13,15,17})
  sha256-identical to the Mac P3 run → the Sprint06 Mac heatmap IS the Pi heatmap. Ranked
  recommendation table identical on every cell/column.
- **Shortlist confirmed on Pi bytes:** progressive q13 worst coral 169 msgs · q9 → 126 ·
  q15 → 188 (inside 195 cap) · baseline q9 → 124 · **q17 stays dead** (206 > 195). Card 4-tag
  PASS everywhere.
- **New Pi-only numbers:** encode wall mean 0.031 s baseline / 0.066 s progressive, max
  0.078 s (alt_07 progressive q17); peak RSS ~122 MB flat (MemAvailable ~230 MB, CMA
  untouched); native load + crop + lanczos ~2.2–2.8 s/source. Encode is a non-factor for the
  18-min budget — P2 hinges on capture + transmit.
- **Pipeline:** Pi = `tools/bm_pi_jpeg_encode.py` (encode only); Mac = new
  `tools/bm_mac_analyze_pi_run.py` (imports the sweep's metric/decode/analyzer/status
  functions unchanged) + `bm_jpeg_p3_budget_verdict.py` unchanged (with the P2 robustness CSV).
- Field ops: nothing installed, camera/crontab untouched. Artifacts:
  `~/Downloads/bm_jpeg_partial_sweep/p1_pi_analysis_20260725T063719Z/` (`p1_report.md`,
  `verdict/heatmaps/`, `recommendation_ranked.csv`, `parity_grid.csv`, per-source CSVs with
  `pi_encode_wall_s_*`/`pi_peak_rss_kb_after`, 108 decoded PNGs, 18 cut sheets).

### P2 — 18-min cycle check (2026-07-24, run `p2_cycle_20260724T170930Z`) — PASS

- **No Spotter/ebox needed:** transmit is the Sprint06 model (msgs × 5 s); only capture +
  processing + encode were measured for real, with a live camera capture on bmcam000.
- **All shortlist cells PASS with room:** totals (worst pipeline + worst-coral transmit from
  the Pi-validated P1 grid) — baseline q9 10.46 min · prog q9 10.63 · prog q13 14.21 (3.8 min
  margin) · prog q15 15.80 (2.2 min) · **any cell at the 195-msg cap 16.38 min (1.6 min
  margin)**. The spec's ≤ ~1.75 min pipeline budget is met ~13× over: capture 4.8–5.3 s
  (production `libcamera-still` command incl. 2 s AE settle; `rpicam-still` not installed on
  this Bullseye image — production falls back identically), prep 2.42 s, encode ≤ 0.063 s.
- **⚠ CMA is the binding constraint:** CmaFree bottomed at **1,872 kB** during native
  4608×2592 capture (idle ~109 MB, full recovery after). `cma=128M` is a hard floor with
  ~1.9 MB headroom — never lower it, never run concurrent CMA users during capture.
  MemAvailable never dropped below 180 MB; encode peak RSS ~123 MB, CMA untouched by encode.
- Field ops: preflight enforced (no camera procs, capture lock free), crontab backed up,
  nothing disabled/changed, no reboot. Artifacts:
  `~/Downloads/bm_jpeg_partial_sweep/p2_cycle_20260724T170930Z/` (`p2_report.md`,
  `p2_cycle_table.csv`, `capture_times.csv`, `cma_samples.csv`, meminfo before/after, native
  captures + metadata, encode subrun); Pi copy in `~/bm_sprint07_runs/`.

### P3 — Pare the upper limit + final verdict (2026-07-24) — settings finalized, see §7

- Analysis-only phase on the Pi-validated P1/P2 data (no new hardware runs).
- **Upper limit pared to q15, gated:** worst coral (alt_07) at prog q15 is 188 msgs = **96% of
  the 195 cap (7 msgs spare)** — fine for the reference fleet, but a busier field scene can
  blow the cap, so q15 ships only behind a pre-transmit size check. q13 nominal sits at 87%
  (26 msgs spare). **q17 confirmed dead** (206 msgs = 106%). Message counts per source/quality:
  see §7 table + `p1_pi_analysis_20260725T063719Z/verdict/`.
- Quality ladder (mean coral PSNR, progressive): q9 26.48 dB · q11 27.29 · q13 27.99 · q15 28.51
  — ~0.5 dB per 2-quality step; q13→q15 buys ~0.5 dB for ~19 extra worst-case messages.
- **Final: (progressive, q_nominal=13, q_floor=9 with card frames floored at q13, q_max=15
  gated).** Adaptive rule + deployment handoff in §7. Remaining sprint work: P4 render check
  (deployment precondition).

## 7. Final verdict (P3) — shipping JPEG settings, Pi-validated

All numbers below are **Pi-measured** (bmcam000, Pi Zero 2 W; runs `p1_grid_20260724T165653Z`
/ `p1_pi_analysis_20260725T063719Z` / `p2_cycle_20260724T170930Z`) and byte-identical to the
Sprint06 Mac DOE.

| Setting | Value | Evidence |
|---|---|---|
| **mode** | **progressive** | ≤ ~4% coral / ~5–8% card byte overhead vs baseline; under B6 tail-loss delivers full-frame partials (+7–10 dB, card 4-tag lock from 50% received — Sprint06 P2) |
| **q_nominal** | **13** | worst coral (alt_07) 169 msgs = 87% of the 195 cap; cycle 14.21 min (3.8 min margin); mean coral PSNR 27.99 dB |
| **q_floor** | **9** (adaptive floor; **card-bearing frames floor at q13**) | worst coral 126 msgs; q9 partial card lock needs 90% received → card frames must not drop below q13 |
| **q_max** | **15**, gated by a pre-transmit size check | worst coral 188 msgs = **96% of cap (7 msgs spare on the busiest reference scene)**; cycle 15.80 min. A field scene busier than alt_07 can exceed the cap → q15 only when the encoded size check passes, else step down to q13/q11/q9 |
| q17+ | **dead** | 206 msgs = 106% of cap on Pi bytes; confirmed, do not ship |

**Adaptive rule (deployment behavior):** encode at q_nominal=13; if `ceil(base64/300) > 195`,
step down q13→q11→q9 until within cap (q9 worst case 126 always fits the reference fleet);
q15 permitted only when explicitly requested (stretch/still mode) **and** the size check
passes. Never exceed q15.

### Deployment handoff (separate, reviewed PR — do NOT fold into this sprint)

- Runtime targets: `image_pipeline` in `camera_schedule.yaml` + `process_image_v2.py`
  (currently 3072×1728 crop → 2688×1512 → HEIC Q20). New path: crop **1600×900** native
  (scene-centered `1504,846`; card-centered `1467,1255` for card frames) → **1000×562**
  lanczos → Pillow JPEG `quality=q, progressive=True, optimize=True`. Chunking unchanged
  (300 b64 chars/msg, `image_buffer_size` 300).
- Capture side unchanged: `libcamera-still`/`rpicam-still` native 4608×2592 q95 (P2: 4.8–5.3 s,
  works with `cma=128M` — **CmaFree bottoms at 1.9 MB during capture; never lower CMA, never
  run concurrent CMA users**).
- Encoder parity is exact (P0/P1: sha256-identical Pi vs Mac across the whole grid), so the
  Sprint06 Mac DOE remains the authoritative quality/budget reference for these settings.
- Cycle budget: even at the 195-msg cap the full cycle is 16.38 min (1.6 min margin vs 18).
- **P4 precondition: satisfied** (run `p4_render_20260725T070626Z`) — the backend's partial
  derivative path renders tail-cut progressive JPEGs unmodified (full-frame preview from 25%
  received). Known residual: ~0.5–4% of cut positions land in refinement-scan headers and
  decode black; the backend's ≥5%-recovered guard rejects those to the placeholder tile
  (missing preview, never a wrong one). Optional future hardening (backend, separate work):
  retry the derivative with a shorter prefix to recover the last completed scan.

### P4 — Truncated-progressive render check (2026-07-25, run `p4_render_20260725T070626Z`) — PREMISE HOLDS

- Ran the REAL backend code (`nereus-vision-dev/backend/app/services/image_derivatives.py::
  convert_partial_image_bytes_to_jpeg`, the function partial ingest calls at
  `poll_once_ingest.py:879`), unmodified, in the backend's venv, on Pi-encoded P1 JPEGs
  tail-cut with the chunk model. Backend repo untouched; nothing pushed anywhere.
- **Progressive 29/30 cells renderable:** full blurry frame from 25% received (recovered
  ~100% rows); 10% gives the DC scan (~half frame). Card locatable at 25%, readable at 50%.
  Baseline contrast: renders only the top slice (25% → top ~31%), confirming why progressive
  ships. Derivatives 9–71 KB, `display/*.partial.jpg`, `render_state="renderable"`.
- **New finding — pathological cut points:** cuts landing inside clusters of tiny
  successive-approximation refinement scans make libjpeg discard the whole progressive state →
  black frame (found at coral_primary q13 @ 50%: cut byte 8325 vs SOS at 8126/8202/8330).
  Exhaustive per-message-cut sweep over 5 files: 0.5–4.0% of cut positions affected. The
  backend's `MIN_RECOVERED_FRACTION=0.05` guard rejects every one → placeholder + %-badge
  (correct: missing preview, never a wrong one). Not in Sprint06's P2 (coral_primary wasn't
  in that partial sweep). No backend change needed to deploy; optional hardening noted in §7.
- Artifacts: `~/Downloads/bm_jpeg_partial_sweep/p4_render_20260725T070626Z/` (`p4_report.md`,
  2 evidence cut sheets, `p4_render_check.csv` 42 rows, 41 derivatives, manifest, log).
