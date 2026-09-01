# Color-correction benchmark runs — 2026-09-01 (first deployed reef images)

Baseline benchmark for all future color-correction work. Inputs: the first two
images from bmcam001 @ AOML (17:00Z and 18:00Z, Reef Reference Card V2 in
frame, 8-bit transmitted JPEGs, ~1000x562).

## Run folders (committed past .gitignore on purpose)

- `sprint05_color_smoke_20260901/` — all 7 registry methods solved per image.
  Headline: **root_poly2 is the best commercial-clean method** (dE76 59 ->
  17.5-18.2, psi 37deg -> ~7deg). root_poly3 overfits (great on-patch,
  posterized scene). The 18:00 frame is below the 5% white-patch red floor
  (3.5%) — red is unrecoverable there by any chart method.
- `sprint05_color_smoke_20260901_xapply/` — 17:00 models applied frozen to the
  18:00 frame (`--apply-model-from`). root_poly2/ccm3x3 transfer (~4 dE
  penalty); root_poly3 collapses (35.1).
- `seathru_bench_20260901/` — RESEARCH-ONLY sea-thru benchmark (see
  research/seathru_benchmark/README.md for quarantine rules). Dehazes the
  scene but leaves the card cyan (dE unchanged; no absolute anchor, red never
  amplified). `hybrid/` = naive chain sea-thru -> card methods: worse on-card,
  posterized. `*_depth.png` are the (uncalibrated) Depth Anything V2 ramps.

## What was committed vs not

Committed: cut sheets, before/after renders, detection overlays, all
JSON/CSV/manifests, sea-thru outputs + depth maps. NOT committed: the
`rectified_card*.png` intermediates (~60 MB) — they are embedded in the cut
sheets and regenerate exactly by rerunning the commands in each
`run_manifest.json`.

Metric definitions: see tools/reference_card_color_utils.py docstrings.
Targets are nominal design sRGB (not measured print values).
