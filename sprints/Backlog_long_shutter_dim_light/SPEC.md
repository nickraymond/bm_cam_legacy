# Backlog: long shutter in dim light, gain LOCKED at 1.12 (later sprint, not R1)

Status: **BACKLOG SPEC, no code** (Nick 2026-10-06 ~15:30 PDT, via the EM). The R1 RC runs exposure
profile **auto** (the default). The low_gain profile (bm #133) stays in the code as an option, off.

## Goal

In dim light, collect more photons instead of more gain.
- Let the still's shutter run past today's 1/15 s ceiling, to a target of **~1/4 s**.
- Keep the IMX708 analogue gain **pinned at its 1.12 floor**.
- **Accept a darker RAW** when even the cap is not enough.
- **Brighten in the cloud** (backend render / colour correction): the RAW is linear and sqrt-coded, so
  a digital gain applied later costs nothing the sensor did not already lose.

## Evidence (rig, Raw compression test spec session, rig PR #93 `feat/v3-card-truth`)

The source is `docs/card_truth/exposure_test_20261006/score.json`: nereus002 IMX708, ambient ~88 lux, V3 card +
ColorChecker in frame, 3 interleaved repeats. Red SNR = mean / temporal std of the RAW red channel.

| arm | shutter | gain | red SNR, V3 red / CC red (dB) | held-out ΔE00 median | clipped |
|---|---|---|---|---|---|
| auto | 60 ms | 3.35 | 27.6 / 25.7 | 4.14 | none |
| pinned | 58 ms | 1.12 | 27.2 / 24.3 | 4.42 | none |
| pinned | 66.7 ms (1/15 s) | 1.12 | 27.6 / 25.5 | 4.44 | none |
| **pinned** | **250 ms (1/4 s)** | **1.12** | **33.3 / 31.8 (+5.7 / +6.1 vs auto)** | 3.92 | none |
| pinned | 500 ms (1/2 s) | 1.12 | 36.3 / 34.9 | 4.13 | **3 patches** (gray_light, yellow, r4c1) |

What the table shows:
- **Red SNR follows shutter time (photons), not gain.** At the same ~60 ms, gain 3.35 gave the same
  SNR as gain 1.12, so the gain only scaled the signal.
- 1/4 s buys +6 dB.
- 1/2 s clips highlights at this light level.
- Colour accuracy after correction is unchanged across arms (ΔE00 3.9–4.4).

## Mechanism (to verify on the bench first: P0)

- **The ceiling today:** the still's AE tops out at 66.7 ms. That is the frame-duration limit of the
  default still configuration, and rpicam's ExposureTime max read back 66666 µs (the bmcam004 P0
  probe).
- **What the rig did:** it reached 250 / 500 ms with a FIXED `rpicam-still --shutter` (rpicam then
  lengthens the frame).
- **For an AE-driven still** (scene-adaptive, preferred over a fixed shutter that clips in brighter
  light), reuse #133's mechanism. A patched `--tuning-file` copy whose `rpi.agc` `normal` mode is
  `shutter [100, 250000]` / `gain [1.0, 1.0]`, so gain stays at the 1.12 floor. Plus a lower
  frame-rate limit on the still path, so FrameDurationLimits allow ≥ 250 ms (e.g. `--framerate 3`,
  ≤ 333 ms). **ASSUMPTION:** libcamera's AGC honours both. P0 reads back ExposureTime / AnalogueGain
  per still, as R5 did for low_gain.
- **Config sketch:**
  - widen `camera.exposure.max_shutter_us` past 66666 (range to 500000) and allow `max_gain` 1.0;
  - or add a profile value (e.g. `long_dim`) that sets cap 250000 / gain floor / the frame-rate limit
    together.
  - The validate rule `low_gain + fixed shutter/gain → xk` carries over.

## Trades and costs

- **Motion blur** (the main cost). Blur ≈ image speed × shutter.
  - How much the camera itself moves depends on the mounting (reef frame vs mooring line), which
    is site-dependent and not measured here. The scene moves too: kelp sway, surge-borne particles,
    fish.
  - At 1/4 s, a feature crossing the 1600 px ROI in 5 s (320 px/s) smears ~80 px; slow kelp sway at
    20 px/s smears ~5 px. ESTIMATES, not measured.
  - Needs a field check: rig sharpness on a static target vs a moving one, and a dusk field frame
    pair at 66 ms vs 250 ms.
  - Consider capping the shutter where motion is common (a per-site key).
- **Wake time:**
  - The capture itself is fine: 250 ms once.
  - **AE convergence is the risk.** rpicam-still runs AE over a preview for `--timeout 2000` ms. At
    250 ms frames that is only ~8 frames, which may not converge from a cold start.
  - Measure: ExposureTime per preview frame vs time (rpicam `--metadata` per frame, or a short
    rpicam-vid). Then pick the timeout: ESTIMATE +1–2 s per wake (`--timeout 3000–4000`).
  - The `--raw` DNG write is unchanged.
- **Darker RAWs:**
  - Below ~1/4 s worth of light, the RAW is underexposed by design; the cloud brightens it.
  - Check what this does to the nrjxl bytes and quality. B3a codes the WB'd linear values: darker
    codes are quantised harder by VarDCT's perceptual model, as the TG-7 sweep showed. A per-frame
    exposure scale in the coding (like the B3a headroom param) may be needed.
- **Highlights:** at 1/2 s the card's white clipped at 88 lux. The cap must leave headroom.
  AE-driven (not fixed) keeps bright scenes at short shutters automatically.

## Test plan (for the sprint)

1. **P0 (bench):** the patched tuning file + frame-rate limit give AE-driven shutters up to 250 ms at
   gain 1.12 (read back from metadata); measure AE convergence frames and the timeout needed; wake
   time with and without.
2. **Rig repeat** of the exposure test at 2–3 light levels (incl. ~20 lux), adding a moving target
   for blur.
3. **nrjxl / B3a bytes and quality** on the pinned-gain RAWs vs auto (the s28 density tools).
4. **Field:** one dusk wake pair (auto vs long_dim) on a bench unit, reviewed by eye + red SNR.

Not in R1. No code until the sprint starts.
