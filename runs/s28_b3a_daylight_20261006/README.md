# Sprint28 B3a on a DAYLIGHT IMX708 frame (bmcam004, 2026-10-06 14:57Z, ~2170 lux)

**Input:** the TE's sunrise pair 36 from bmcam004, deployed at 1d0ff4f.
- `auto.dng` / `lowgain.dng`: full-sensor IMX708 DNGs, LOSSLESS, 24 MB each. NOT in git; sha256 below. The TE copy is in the vigilant-proskuriakova worktree, `runs/s28_lowgain_sunrise_20261006/day_dng/`.
- Both members ran at the gain floor (1.1228) with a ~8.8 ms shutter. Low gain correctly does nothing in daylight: LG3 holds.

**Method:** `tools/s28_density_sweep.py` IMX708 mode, ROI `[1504, 846, 1600, 900]`, 195-msg cap, fill 0.97.
- Rows: 4-plane Bayer at 1600 (today's nrjxl) and B3a (`rgbwa` = the production v2 coding) at every width.
- Reference: the lossless neutral render (backend v1 render). Today's pjpg runs on the same neutral pixels, upsampled.
- `--equal-quality`: the B3a d that matches the JPEG's SSIMULACRA2.

| frame | today's JPEG (q, msgs, s2 / b3) | Bayer 1600 | B3a 1600 (d, s2 Δ, b3 ×) | B3a 1000 | equal quality: B3a bytes / JPEG |
|---|---|---|---|---|---|
| auto | q50, 172, 48.3 / 2.19 | d 9.43: 16.8 (−31.5), ×1.82 FAIL | d 3.52: **+3.6, ×1.23** FAIL | +6.3, ×1.08 FAIL | **1.01** (173 msgs) |
| low_gain | q60, 195, 55.0 / 1.88 | d 9.18: 18.0 (−37.0), ×2.09 FAIL | d 3.43: **−1.9, ×1.28** FAIL | +0.6, ×1.29 FAIL | **1.03** (201 msgs) |

**Reading (ONE daylight scene, above water):**
- On this daylight frame, B3a at 1600 ≈ today's JPEG. SSIMULACRA2 lands within ±4, butteraugli is 1.2–1.3× worse, and at equal quality it needs the same bytes. Not significantly better, and not a byte saving.
- Lower widths do slightly better on SSIMULACRA2 (+6.3 at 1000 for `auto`), but butteraugli stays worse than the JPEG.
- By eye (`day_auto/cutsheet_rgbwa.png`): B3a 1600 is a little crisper on fine branches than the JPEG; at 1000 the two look alike.
- Compare the low-light TG-7 set (30 frames): +20.6 / +11.8 SSIMULACRA2 at P50 / P90, 0.59× the bytes at equal quality. The B3a gain is strongly scene-dependent: large on dim / noisy underwater frames, roughly neutral on a bright, high-detail daylight frame.
- The 4-plane Bayer is far worse than either in daylight: it needs d ≈ 9.3 to fit 195 msgs.
- Caveats:
  - Branches against a bright sky with clipped highlights. The sky renders magenta in the neutral render (sensor-clipped G after WB), on both sides alike.
  - Two near-identical exposures of one scene. The JPEG ladder's q50 vs q60 step makes their JPEG baselines differ by ~7 s2.
  - Not underwater daylight; that needs a field frame.

sha256: auto.dng 8a5123cb…850fe, lowgain.dng 1d80a44d…604e (MANIFEST.txt in the TE copy).
