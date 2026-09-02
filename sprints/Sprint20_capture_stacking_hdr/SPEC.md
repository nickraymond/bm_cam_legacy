# Sprint 20 — Capture-side frame stacking + red-channel HDR bracketing

**Status:** captured for future work (Nick, 2026-09-01). NOT scheduled; no code yet.
**Motivation:** the Sprint05-revival color-correction work (PR #48/#49) hit a hard
wall: white-patch red on the AOML reef images is 3.5–14% of full scale on 8-bit
JPEGs, below/near the ~5% recoverability floor. Every correction method —
chart fits, physics hybrid, sea-thru, OceanLens — is red-noise-bound. No
shore-side algorithm can recover information the sensor never delivered.
This sprint moves the fix to capture time: more red photons, less noise,
BEFORE compression. Zero added transmit bandwidth (still one image per cycle);
denoised images also compress smaller (noise is incompressible).

## Two experiments, same hardware, tested separately

### Test A — same-exposure frame stacking (MVP)
1. Let AE/AWB converge once, then LOCK exposure, analog gain, and AWB.
2. Capture N identical frames back-to-back (start N=8; also try 16).
3. Accumulate and average (or median-stack) BEFORE JPEG encode.
4. Encode once; transmit as today.

Expected gain: temporal noise / sqrt(N) → ~2.8x SNR at N=8; median stack
also rejects drifting particles/fish; averaging recovers sub-LSB red detail
(dither effect) if done pre-quantization.

### Test B — red-channel HDR bracket
1. Capture one NORMAL frame (locked settings as above).
2. Capture one LONG frame at +2 EV and one at +3 EV (shutter, not gain).
3. Merge channel-wise: G/B from the normal frame; RED from the long frame,
   scaled by the exposure ratio (G/B clipping in the long frame is expected
   and irrelevant).

Expected gain: 4–8x red photons — bigger red win than stacking; combinable
with Test A later.

## Acceptance metric (built into the existing tooling)

Card red-health from `tools/reference_card_color_utils.py::card_red_health`
on transmitted images: white_patch_red_frac and white_patch_red_snr, same
reef, adjacent cycles, stacked vs unstacked. Success = red_frac lifted above
the 0.05 floor on frames that currently fail it (18:00-class light), plus
same-quality JPEG size reduction. Then re-run the Sprint05 method leaderboard
(`tools/bm_reference_card_color_smoke.py`) on stacked vs single frames.

## Known hardware issues / constraints (from the 2026-09-01 session + CLAUDE.md)

- **Pi Zero 2W memory / CMA:** full-res IMX708 frames are large; do NOT hold
  N frames. Use a running accumulator (uint16 sum for N<=8 of 8-bit, or
  uint32) + current frame only (~50–70 MB in YUV420), or stack at transmit
  resolution (~1000 px) since that is what ships anyway.
- **Capture path matters (CLAUDE.md):** Picamera2 production-style captures
  can allocate large BGR888 streams and hit CMA limits at full res —
  bench-test the burst path on bmcam000 before touching field units; check
  `CmaTotal/CmaFree` during the burst. rpicam-still and Picamera2 are NOT
  interchangeable.
- **AE/AWB lock is load-bearing:** frames must be pixel-identical in
  exposure/gain/AWB or the stack blurs colors instead of averaging noise.
  Converge once, freeze controls, then burst.
- **Stack before encode:** capture YUV/RAW via Picamera2 and encode a single
  JPEG at the end. Stacking decoded JPEGs helps less (block artifacts are
  correlated across frames).
- **Bracketing via shutter, not gain** (gain adds the noise we are removing);
  watch motion blur at +3 EV in surge (per-frame shutter still short at
  these light levels — verify on bench).
- **Cycle budget:** a few extra seconds awake per cycle; measure the
  energy-per-cycle delta with the usual SD-bridge A/B comparison
  (tools/sd_bridge_ab_coplot.py) before/after.
- **Field discipline:** prototype on bmcam000 (dev unit) only; production
  process_image_v2.py path untouched until the bench A/B passes; crontab
  backup/restore rules apply as always.

## Relationship to shore-side work

The color-correction stack (root_poly2 measurement layer, hybrid v4
visualization layer) stays as-is; it simply starts receiving images whose
red channel carries signal. Methods currently noise-bound (physics red
recovery, true colorimetric red) become viable if this sprint succeeds.
