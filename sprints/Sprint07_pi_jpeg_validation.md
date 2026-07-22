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
| P0 | **Deploy + parity smoke** | Get branch + reference images + venv deps onto the Pi (Tailscale SSH; see `pi-deploy`); encode 1 image × q13 × both modes on-device; compare bytes/base64_len vs the Mac run (`p3_verdict_20260722T055437Z`) | ☐ TODO | — | parity delta table (expect near-identical if libjpeg-turbo versions match; investigate if >±2%) |
| P1 | **Pi heatmap re-run** | Sweep on the Pi: both modes × q{7,9,11,13,15,17} × all 9 sources, 100% received, **with per-encode wall time + peak RSS logged**; run `bm_jpeg_p3_budget_verdict.py` on the Pi CSVs → Pi-native heatmaps + ranked table | ☐ TODO | P0 | Pi heatmap + `recommendation_ranked.csv` (Pi bytes, not Mac) |
| P2 | **18-min cycle check** | Time the full cycle on-device: capture (or timed load of a native) → crop/downsample → encode → transmit-time model; verify worst-case cell ≤ 18 min total; measure encode memory headroom (Pi Zero 2W, watch CMA/RSS) | ☐ TODO | P1 | cycle-time table per shortlist cell; PASS/FAIL vs 18 min |
| P3 | **Pare the upper limit + final verdict** | From Pi bytes + cycle times, set the shipping upper quality limit (is q15 stretch still inside cap on Pi? does q17 stay dead?); confirm nominal/floor; final `(mode, q_nominal, q_floor, q_max)` | ☐ TODO | P2 | final settings table → deployment handoff |
| P4 | **Truncated-progressive render check** | Confirm backend/frontend render a tail-cut progressive JPEG (B6 emulation with real truncated files from P1) — the deployment premise | ☐ TODO | P1 | render evidence (screenshots) + backend notes |

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

_(fill in as phases run)_
