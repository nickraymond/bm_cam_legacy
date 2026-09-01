# Sea-thru internal benchmark — RESEARCH ONLY, NOT FOR THE COMMERCIAL PATH

## Why this directory exists

`docs/underwater_color_correction_research_202609.md` §4b: the Sea-thru
*algorithm* is patent-claimed by Carmel Ltd / SeaErra for commercial use, and
the best free depth models above "Small" are CC-BY-NC. We still want to know
how good a physics-based, range-aware method is on our imagery, as an
**internal upper-bound benchmark** for the commercial-clean card methods in
`tools/bm_reference_card_color_smoke.py`.

## Quarantine rules

- **No third-party Sea-thru code is vendored into this repo.** The runner
  clones [hainh/sea-thru](https://github.com/hainh/sea-thru) (MIT code,
  patent-caveat method) into a scratch directory at run time.
- Output images from this benchmark are for internal comparison only — do not
  ship them, and do not port this pipeline into product code without the
  patent opinion the research brief calls for.
- The glue code here (`run_seathru_mono.py`) is ours; it only orchestrates.

## How to run

```bash
# one-time: create the bench env (~2 GB: torch + Depth Anything V2 Small)
python3 research/seathru_benchmark/run_seathru_mono.py --setup \
  --bench-dir ~/nereus_seathru_bench

# per image
python3 research/seathru_benchmark/run_seathru_mono.py \
  --bench-dir ~/nereus_seathru_bench \
  --image ~/Downloads/SPOT-33361C_BMCAM_001_2026-09-01T18-00-24Z.jpg \
  --out-dir runs/seathru_bench_<date>
```

Then score the outputs with the same card metrics as every other method:

```bash
python3 tools/bm_reference_card_score_external.py \
  --pair <original.jpg> <seathru_output.png> seathru_mono
```

## Depth caveats (read before trusting results)

- Depth comes from Depth Anything V2 **Small** (Apache-2.0), a *relative*
  monocular model trained on land scenes; it is mapped to meters by a crude
  linear ramp `--z-near`/`--z-far` (defaults 0.5/4.0 m, eyeballed for the
  AOML card-in-frame geometry). No in-housing calibration has been done, so
  the meters are NOT real — good enough for a method-class comparison, not
  for science.
- Sea-thru's backscatter fit needs depth *variation*; a flat scene or a bad
  depth map degrades it silently. Inspect the saved `*_depth.png`.
