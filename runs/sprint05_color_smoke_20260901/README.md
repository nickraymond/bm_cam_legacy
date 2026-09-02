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

## Update 2026-09-01 (later): hybrid rounds v2–v5 (PR #49)

The hybrid pipeline went through five iterations the same day (evidence in
runs/hybrid_physics_20260901*/):

- **v4** (`--illumination lsac --finish --red-wb-cap 1.3 --stretch-mode
  perchannel`): Nick's preferred visualization render. Card dE76 40.5/47.9.
- **v5** adds the now-default 2x-refined DA-V2 depth (internal res was
  silently ~518px before) and the measured ground-plane cap (camera 0.25 m
  off the sand + card at 1.5 m solve pitch -6.1 deg; corrected 43,841
  sand-as-far pixels on the 18:00 frame). Card dE76 unchanged (40.7/48.6) —
  the wins are scene-wide haze correctness, not card-local.
- Moody sweep (black point 0, bs-guard 0.9/0.95): warmer/darker, judged too
  red. Cardless experiment (--no-card-color): render nearly identical to v4
  (the per-channel stretch does most of the WB); card's real jobs are
  measurement, frame-to-frame stability, and geometry.
- Benchmarks: OceanLens (MIT) ties v4 on-card (46/48), greener cast;
  sea-thru stays the visual reference but never colorimetric (54-63);
  depth-binned dark-pixel backscatter (patent-flagged, quarantined) LOSES
  to the card anchor on this scene type.
- Measurement layer unchanged: root_poly2 (17.5-18.2), with cheung2004_t7
  (10.5/14.8, colour-science BSD) pending cross-frame stability testing.
- Capture-side red fix captured as Sprint 20 / TODO-CAM-001 (frame stacking
  + red-channel HDR bracket).

## LOCKED: Nereus color correction v1 (2026-09-01, Nick sign-off)

Visualization layer frozen as the DEFAULTS of
tools/bm_reference_card_hybrid_physics.py: fused site depth
(runs/depth_fusion_20260901) + ground plane + chroma LSAC (spatial-cast
only, lift 0.45) + card-anchored finish (red WB cap 1.2, red stretch cap
1.1, per-channel stretch, TV denoise, unsharp). Reference render + final
cut sheet: runs/nereus_color_v1_20260901/. Card dE76 41.8/50.8 — the
locked look deliberately under-corrects red vs the card (Nick's aesthetic
call); the measurement layer (root_poly2) remains the colorimetric truth.
