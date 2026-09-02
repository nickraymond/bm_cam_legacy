# Hybrid physics correction — first attempt, 2026-09-01

Tool: `tools/bm_reference_card_hybrid_physics.py` (card-anchored
Akkaynak-Treibitz model + Depth Anything V2 Small depth; commercial-clean
stack, patent review still noted for productization).

## Runs

- `hybrid_physics_20260901/` — defaults (root_poly2 polish). Card dE76 11.1 /
  16.9 — best on-card numbers of ANY method to date. Scene: flooded deep red.
- `hybrid_physics_20260901_noredv/` — physics-stage red gain capped at 2x
  (`--red-boost-cap 2`), red delegated to the cross-channel polish. Same
  on-card scores; scene still red -> the flood comes from the POLISH, not the
  physics red boost.
- `hybrid_physics_20260901_gbpolish/` — diagonal gray_balance polish instead.
  Still red (an 18x diagonal red gain amplifies scene red residue).
- `seathru_variants_20260901/` — sea-thru parameter sweep (f=1.5/2/3, two
  depth ramps): the natural look is stable (card dE flat at 54-62 across all;
  it never targets absolute color). See `variants_montage.jpg`.

## Finding (the headline)

**On 8-bit transmitted JPEGs with white-patch red at 3.5-14% of full scale,
colorimetric card accuracy and a natural-looking scene are mutually
exclusive.** Any correction that brings the card's red to target must apply
~20x red gain somewhere, and outside the averaged card patches that gain
amplifies sensor/JPEG noise into a red glow. This is information loss at
capture, not an algorithm defect — it matches the research brief's <5%
recoverability floor.

Key evidence: `comparison_physics_only_vs_polish.jpg` — the physics-only
output (backscatter subtracted, G/B attenuation compensated, red left alone)
is dark but NATURAL; every red-forcing polish on top of it floods the scene.

## Where this leaves the method ranking

- Colorimetric-at-card (measurement use): **root_poly2 chart-only** on the
  original image (dE76 17.5, cross-channel red reconstruction degrades most
  gracefully).
- Natural visualization: physics-only hybrid (needs an exposure lift) or the
  research-only sea-thru output.
- Best on-card number ever recorded here (hybrid + root_poly2 polish, 11.1)
  is a patch-metric artifact — never ship it as imagery.

## Next steps that actually move the needle

1. Capture-side red signal: card closer / artificial light / RAW or
   higher-bit capture / longer exposure. Biggest single win available.
2. Dual-output product framing: accurate patch-anchored MEASUREMENTS +
   natural-looking visualization image, instead of one image doing both jobs.
3. beta_D(z) needs the card at 2+ distances to be measurable; single-distance
   extrapolation is what made red explode with range.
