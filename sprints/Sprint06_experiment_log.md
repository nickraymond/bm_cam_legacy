# Sprint 06 — Experiment Log & Lessons Learned

**Date:** 2026-07-21/22 · **Branch:** `claude/sprint-06-jpeg-sweep-09e791` · **Detail:** findings
log in `Sprint06_jpeg_partial_transmission_sweep.md` (reproduce commands + artifact paths per run).
All Mac-side DOE; tool = `tools/bm_reference_card_jpeg_partial_sweep.py`. Message = 300 base64
chars, 5 s. Bands: ideal ≤75 msgs · feasible ≤125 · hard cap ~180.

## Experiments

| # | Run (artifacts in `~/Downloads/bm_jpeg_partial_sweep/`) | Question | Result |
|---|---|---|---|
| 1 | `jpeg_20260722T003214Z` — quality ladder q5–40, baseline, card+primary, sprint geometry (3072×1728→1600×900) | Where is the budget window on the quality axis? | Only q≤9 fits feasible at 1600-wide; q15+ over cap. Card detects 4/4 tags at *every* quality down to q5 — budget, not detection, binds. |
| 2 | `multi_coral_20260722T004839Z` — q5–10 × all 8 coral scenes | Does the primary coral represent the fleet? | No. ~2.5× byte spread by scene texture (q5: 62–156 msgs); primary is one of the *cheapest*. No fixed q lands every scene in feasible → adaptive encode needed. |
| 3 | `multi_coral_srccmp_20260722T010924Z` — color investigation + new source-vs-compressed sheets, `ff_chroma_sat` metric | Are the "wrong" colors a pipeline bug? | No — pipeline verified color-correct (all sRGB, no ICC, mean RGB/luminance preserved end-to-end). Muddy look = chroma-variation loss from low-q JPEG (q5 keeps 48–88% by scene; ~90%+ by q9). Color sets a quality floor ≈ q8. |
| 4 | scratch probe (`crop_size_probe.csv`, spec §crop-vs-size) | Does a tighter crop shrink files (HEIC-era trend)? | Trend holds for JPEG: at fixed output size, crop tightness ≈ no size change (≤6%). Messages ∝ output pixel area. **Output pixel count is the budget lever; crop decides how pixels are spent (FOV vs detail).** |
| 5 | `width_sweep_q9_20260722T013001Z` — output width 1600→800 @ q9, FOV fixed, all 9 sources (+ geometry CLI flags added, regression-checked) | Can downsampling buy the budget? | Yes for bytes (w1200: all scenes ≤ gated) but detection knees: card PASS ≥w1200 (21 px tags), WARN w1000 (17 px), FAIL w800 (14 px, 2 tags lost). |
| 6 | `card_center_grid_20260722T020707Z` — clipping check + card-centered crop × width × q7–13 | Is the w800 FAIL a crop-clipping false fail? | No — measured margins 600/638/598/172 px, card fully in frame. Centered crop identical (±0.4 px tag size). Quality can't rescue small tags (flat q7–13). **Tag pixel size is the only detection variable.** |
| 7 | scratch demo (spec §order-of-operations) | Does crop-order matter? Would a tight ROI at constant density keep detection? | Crop and downsample commute. Card-centered 1920×1080 ROI @ 1.92× → 1000×562: PASS with 27.8 px tags at **56 msgs** (vs 120 full-FOV) — ROI cuts bytes with zero per-pixel quality loss. |
| 8 | `proposal_vs_baseline_q9_20260722T022941Z` — minted proposal (1920×1080 ROI @1.92×→1000×562) vs sprint baseline, all 9 sources | What does the ROI trade buy fleet-wide? | Every scene ideal (4) or feasible (5) vs baseline's 4 over-cap; card PASS unchanged. Cost = 61% of FOV area. |
| 9 | `roi1600_density_grid_20260722T024040Z` — ROI 1600×900, density 1.0–2.0× × q{9,11,13,15}, common-reference PSNR | Should we ship max density (1.0×) and let JPEG do all reduction? | No — under-budget optimum is never 1.0×: ideal band → 1.6–2.0× @ q13–15; feasible → 1.14–1.6×. Quality beats pixel count at our budgets. Card PASSes every cell in this ROI. |
| 10 | `heic_vs_jpeg_closeout_20260722` — production HEIC (2688×1512 q20, from `camera_schedule.yaml`) vs minted JPEG, 3 sources | Apples-to-apples vs the starting pipeline? | HEIC: 136/205/490 msgs (card/primary/alt_07) — over cap on both reefs, blank on tail-cut. JPEG: 71/73/124–169, partial-renders on tail-cut. HEIC pixels smoother but at 2.8–4.5× message cost and undeliverable. |
| 11 | `p2_partial_20260722T045306Z` — P2: baseline vs progressive × q{9,13,15} × received {25–100}% on the adopted cell, card+alt_03+alt_07 (+ new `bm_jpeg_partial_post_analysis.py`) | Which JPEG mode survives B6 tail-loss? | Progressive, decisively: full-frame partials at +7–10 dB PSNR over baseline at every fraction; card keeps 4-tag PASS from 50% received at q13/q15 (baseline needs 75%; progressive q9 needs 90% — q9 first scans too coarse to lock all tags). Overhead at 100%: corals ≤0.6%, card ~5%. |

## Lessons (one line each)

1. Message count must use base64 length (`ceil(b64/300)`) — raw-bytes÷300 undercounts ~33%.
2. Budget ∝ output pixel area × scene texture × quality; FOV/crop by itself is nearly free.
3. Scene texture varies ~2.5× across real reef imagery → fixed settings can't hold the band; encoder must adapt per image.
4. Color dies first: chroma variation collapses below ~q7–8 (4:2:0 + chroma quantization); q13–15 is where reef color/texture looks right.
5. AprilTag detection depends only on tag pixel size (≥21 px robust, ≤14 px dead) — not on JPEG quality, not on crop position.
6. Crop and downsample commute; think in two knobs: ROI (how much scene) and density (detail per pixel).
7. At our budgets, spending bytes on quality at moderate density beats native density at low quality.
8. The pipeline is color-correct (sRGB throughout) — verified, not assumed.
9. Production HEIC is over the cap on realistic reef scenes and yields a blank on tail-cut; JPEG degrades gracefully. This is the deployment argument.
10. Orchestration lesson: zsh doesn't word-split unquoted vars; never filter subprocess stderr through grep — count artifacts, not exit banners.

## Closing state → P3

- **Adopted geometry (frozen):** ROI 1600×900 native (card-centered `1467,1255` / scene-centered `1504,846`) → output 1000×562 (1.6× density).
- **Mode (new, from P2):** **progressive** — under B6 tail-loss it delivers full-frame partials (+7–10 dB over baseline at every received fraction) for negligible byte overhead (corals ≤0.6%, card ~5%).
- **Quality:** dynamic JPEG quality, nominal q13–15; P2 caveat on the q9 floor: progressive q9 partials lose the 4-tag card lock until 90% received (q13 holds it from 50%) — P3 should weigh a q13 floor for card-bearing frames.
- **P3 (next session):** budget overlay + verdict — map every setting to the bands (coral-anchored), heatmap (quality × mode, duration-banded), ranked recommendation → JPEG values for Pi validation.
- **P4 (later):** Pi encode parity/memory/time — all sizes here are Mac-side Pillow/pillow_heif emulations; progressive decode of partials also needs a backend check (frontend must render truncated progressive JPEGs).
