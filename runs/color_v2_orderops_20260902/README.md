# color_v2_orderops_20260902 — order-of-operations audit of Nereus color v1

Question: how much of v1's red shift (coral reads red-brown instead of
yellow/brown) and halo/haze artifacting is caused by the ORDER of the
pipeline's operations rather than the physics model? All variants use the
merged v1 tool (`tools/bm_reference_card_hybrid_physics.py`, development
@ 2816c5b) with EXISTING flags only — zero code changes. Frames: AOML reef
17:00Z + 18:00Z 2026-09-01, fused site depth map.

## Order-of-operations findings (code review, verified by measurement)

1. **Red gain is applied three times, multiplicatively.** (a) LSAC chroma
   normalization applies up to ~1.5-1.6x local red-vs-green gain exactly in
   the mid-depth band (z 1.6-2.5) where the coral wall sits — measured
   per-pixel on the 17:00 frame (p90 = 1.42-1.55 by depth band). (b) Finish
   WB red gain sits pinned at its 1.2 cap. (c) Per-channel stretch gives red
   another 1.1x slope vs green. Stacked: ~2x red on coral -> red-brown
   instead of yellow/brown. Each stage was capped individually; nothing
   bounded the product.
2. **The per-channel stretch is a second white balance placed AFTER the card
   WB**, re-balancing from scene statistics and overriding the card anchor
   (the very thing the audit called "implicit extra red WB" and capped
   instead of removing).
3. **Halos**: bs_guard leaves >=20% of the veil in far pixels, and the
   default Gaussian (non-edge-aware) LSAC smears that residue across coral
   silhouettes. The edge-aware `guided_luma` filter already existed but was
   not the default.
4. `--stretch-mode luma` as implemented clips 10-13% of highlight pixels
   (vs ~2-3% for perchannel) — not usable as-is.

## Variants

| variant        | flags | verdict |
|----------------|-------|---------|
| v1_baseline    | locked defaults (bit-exact repro of nereus_color_v1_20260901) | red-brown coral |
| nored          | --red-wb-cap 1.0 --red-stretch-cap 1.0 | yellows return, haze remains |
| singlewb       | --stretch-mode luma | REJECT: 10-13% highlight clip |
| nored_singlewb | both | REJECT: same clipping |
| guidedluma     | --lsac-filter guided_luma | cleaner silhouettes, still warm |
| nored_guided   | guided + red caps 1.0 + --sharpen 0.4 | clean but leans cyan (WB blue gain 2.3x) |
| refined        | guided + luma stretch + red caps 1.0 | REJECT: washed out (clipping) |
| **candidate_v2** | **--lsac-filter guided_luma --red-wb-cap 1.1 --red-stretch-cap 1.0 --sharpen 0.4** | **best: yellow-olive coral, blue water, clean edges** |

Card scores (dE2000 17:00 / 18:00): v1 24.5/29.6, candidate_v2 28.0/30.0,
nored_guided 28.3/30.4. Card dE REWARDS the red push (warm card patches), so
v1 "winning" on dE while losing visually is the v6 lesson repeating — do not
tune red by card dE alone. Gray angular error and highlight clipping are the
trustworthy columns here (candidate_v2: clip ~2%, vs 10%+ for luma-stretch).

## Reproduce

    bash runs/color_v2_orderops_20260902/run_matrix.sh          # 5 variants
    # candidate_v2:
    <repo .venv python> tools/bm_reference_card_hybrid_physics.py \
      --images <17:00Z.jpg> <18:00Z.jpg> \
      --depth-npy runs/depth_fusion_20260901/fused_disp_2frames.npy \
      --z-card 1.5 --near-ratio 0.45 --camera-height-m 0.25 --water-depth-m 4.57 \
      --lsac-filter guided_luma --red-wb-cap 1.1 --red-stretch-cap 1.0 --sharpen 0.4

Requires scikit-image + PyWavelets in the repo .venv (installed 2026-09-02).

## Artifacts

- `cutsheet_orderops_-01T17-00-25.jpg`, `-01T18-00-24.jpg` — full 6-variant matrix
- `cutsheet_finalists.jpg` — v1 vs nored_guided vs candidate_v2
- `scores/external_scores.csv` (+ scores_ng, scores_cv2) — card metrics
- `<variant>/` — per-variant PNGs + params.json (reproducible; heavy)
- `matrix.log` — run log

## finish_v2 refactor (2026-09-02, follow-up)

`tools/bm_reference_card_hybrid_physics.py` now has `--finish-style v2`: the
clip-safe single-WB finish (luma-only stretch with per-pixel gamut cap ->
card WB LAST and only once, red capped at red_wb_cap x green ->  TV denoise
-> luminance-only unsharp). v1 default path verified bit-exact after the edit
(`v1_regression/`). Bug found during bring-up: the card white's red channel
is nearly dead (obs 0.047), so the raw red WB gain is ~21x — red must be
capped BEFORE luminance-normalizing the gain vector or it swallows the
exposure.

Run: `finishv2/` — flags `--lsac-filter guided_luma --finish-style v2
--red-wb-cap 1.1 --sharpen 0.4`. Scores: dE2000 26.8/30.1 (best non-v1),
highlight clip 1.7/3.6% (vs 10-13% for the old luma mode), gamut compression
<=2.6%. Cut sheet vs raw + sea-thru: `cutsheet_v2_raw_nereus_seathru.jpg`.
This is the v2 preset candidate.

## Shadow + yellow tuning pair (2026-09-02, hist_compare/)

Histogram diagnosis vs sea-thru (`hist_compare/hist_v2_vs_seathru.png`,
`stats.txt`): (1) v2's display blacks walled at 0.14 — the sRGB encode of
`--stretch-black 0.02` LINEAR; (2) mean luminance identical, sea-thru just
allocates it to upper-mids + true blacks (contrast, not exposure); (3)
sea-thru's yellow is b* +11..+14 in the L*50-80 band only — our global 1.4x
blue WB gain kills it. New `--blue-wb-cap` flag (finish v2 only; default
None = unchanged, verified bit-exact).

Pair (one variable at a time), cut sheet `cutsheet_sb0_pair.jpg`,
histograms `hist_compare/hist_sb0_pair.png`, scores `scores_sb0/`:
- `v2_sb0/` (+`--stretch-black 0.0`): shadow mass now overlays sea-thru's
  luminance curve; dE2000 26.4/29.7 (slightly better than v2).
- `v2_sb0_bc115/` (+`--blue-wb-cap 1.15`): midtone b* lands +8..+13 in
  L*50-80 — on top of sea-thru's curve (17:00 exact, 18:00 close).
  Tradeoff: card whites drift warm (b* +6..+8 in L*80-100 vs their ~0);
  dE2000 27.1/30.6. Remaining gaps: their extra 0.4-0.7 luminance mass and
  warmer dark-mids on the 18:00 frame.

## Sea-thru replication close-out — Nereus v2.1 (2026-09-02)

Final pair (`cutsheet_seathru_closeout.jpg`,
`hist_compare/hist_seathru_closeout.png`, `scores_sw/`):
- `--stretch-white 0.98` (`v2_sw098/`): bought almost nothing — the
  0.4-0.7 upper-mid luminance gap vs sea-thru is set by the gamut cap and
  p99 anchoring, not the target value. Honest miss; kept because harmless.
- `--warm-blend` (`v2_sw098_warm/`): luma-weighted WB closes the white-point
  tradeoff exactly — L*80-100 b* falls +7.8 -> -2.2 (17:00) / +6 -> -4.3
  (18:00), matching sea-thru's neutral whites, while coral mids hold
  b* +10..+15. dE2000 27.2/30.7, gray angular slightly better than flat cap.

**Nereus v2.1 preset (best sea-thru replication):**

    --lsac-filter guided_luma --finish-style v2 --red-wb-cap 1.1 \
    --sharpen 0.4 --stretch-black 0.0 --blue-wb-cap 1.15 \
    --stretch-white 0.98 --warm-blend

Remaining known gaps vs sea-thru: their extra luminance mass at 0.4-0.7 and
0.9-1.0 (would need a midtone S-curve / letting whites clip, not a p99
target), and their warmer dark-mids (L*20-50) on the 18:00 frame.

## Commercial cleanliness

Nothing new was added: every op is already in the in-house tool (published
Akkaynak-Treibitz model + Ebner LSAC + He guided filter + standard
photography finish). This run only re-orders/re-weights existing stages.
Patent review note in docs/underwater_color_correction_research_202609.md §4b
still stands before productizing.

## MVP vs next

- MVP now: adopt candidate_v2 flags as the v2 default preset (one-line change).
- Next sprint: single-WB refactor — make the card WB the LAST color op and
  demote the per-channel stretch to luma-only WITH proper headroom (fix the
  luma-stretch clipping); bound the LSAC chroma red/green gain ratio.
- Future hardening: beta_B from depth-binned dark pixels (our own
  implementation of the published idea) so bs_guard can rise and the residual
  veil disappears.

## a* retarget at the TG-7 reference — Nereus v2.2 candidate (2026-09-02)

TG-7 fingerprint (runs/olympus_fingerprint_20260902) showed our real gap is
coral a* (-10..-44 vs TG-7 ~0 flat). Two experiments:

1. Red-WB-cap sweep 1.1->1.5 (`v2_redcap*`): nearly inert (a* moved ~4
   units). ROOT CAUSE: card-white red is ~0.05 linear — 8-bit-crushed at
   capture; neutralizing needs ~16x red. No WB gain can recover missing
   information. The TG-7 wins at CAPTURE (in-camera underwater red gain
   before 8-bit encode).
2. `--green-trim` (new finish-v2 knob): luminance-scaled compression of
   NEGATIVE a* only (greens -> neutral, reds untouched, b* verified
   unchanged). Sweep 0.5/0.75/1.0 (`v2_gtrim*`,
   `cutsheet_greentrim_sweep.jpg`): trim 1.0 lands coral a* at -0.1
   (hilites) / -7.6 (L*50-65) vs targets -0.9 / -2.5, and card metrics
   agree for once: dE2000 27.2 -> 17.1, gray angular 30 -> 7.2 deg —
   project-best, better than the TG-7's own card numbers.

**v2.2 candidate = v2.1 preset + --green-trim 1.0** (0.75 the conservative
pick iftruly-green subjects matter). Caveat: perceptual grade, not information
recovery — bright true-green subjects (algae) also neutralize at 1.0.

**The capture-side fix is the real one**: give bmcam a TG-7-style
underwater WB at capture (rpicam --awbgains red-heavy) so red survives
8-bit encoding; ties into TODO-CAM-001 (red-channel HDR bracket). Grade
knobs stop being load-bearing once red arrives intact.
