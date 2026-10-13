# Full-frame raw → JPEG XL on the Pi Zero 2W (EM quick experiment, Mon 10/12 ~8:13–8:25 PM PDT)

**Question:** is full-frame raw→JXL on the Pi viable for a later product version? This is not for the pool.

**Verdict: NOT viable as a one-shot cjxl encode with today's settings.** Every high-quality encode exceeded the 300 MiB VmPeak stop within 10–28 s. The only encode that finished (lossless with streaming flags) produced corrupt data because of a cjxl streaming-input bug with 12-bit PPMs, reproduced on the Mac.

## Setup

- **Unit:** bmcam004, idle in pool mode, in a work dir outside pool_runs. That dir has been removed; the calibration folder is untouched (600 files).
- **Frames (n = 3):** T-ENDURE calibration DNGs cal_0001, cal_0060, cal_0120, static bench scene, each with its own `.metadata.json`.
- **Prep:** the production B3a path, `rc_raw_jxl.py` @ 9ec4cb7, run at the full frame (0,0,4608,2592) instead of the 1600×900 crop:
  - `read_dng_crop` → `colour_params` → `coding_gains` → `headroom_x10000` → `rgb_codes` (bilinear demosaic, sqrt 12-bit codes) → P6 PPM, maxval 4095, 4608×2592×3, 71.7 MB.
- **Encode:**
  - production argv `cjxl_rgb_command`: `-m 0 -e 5 --num_threads=0`, with `-d 1.0` and `-d 0` (lossless);
  - cjxl v0.11.2;
  - watched every 50 ms; killed if VmPeak > 300 MiB (the EM stop) or after 900 s.
- **Check:** djxl → PPM, compared in strips. Code domain: max/mean |err|, PSNR with peak 4095. Linear domain: PSNR with peak 1.
- **Script:** `fullframe_jxl_exp.py`. Records: `results_production_argv.jsonl`, `results_streaming.jsonl`, `run_*.log`.

## Results

| Run | Frame | Python prep | cjxl | Output / error |
|---|---|---|---|---|
| production d1 | 0001 / 0060 / 0120 | 25.4 / 23.1 / 24.7 s; VmHWM 173 / 232 / 252 MiB* | **killed at VmPeak 322 MiB** after 11.1 / 12.3 / 10.2 s (VmHWM 214 / 235 / 243 MiB, still rising) | none |
| production lossless | same | same | **killed at VmPeak 320 MiB** after 9.6 / 10.4 / 10.8 s (VmHWM 189 / 168 / 159 MiB) | none |
| + `--streaming_input --streaming_output`, d1 (extra, labelled) | same | 15 / 22 / 25 s | **killed at VmPeak 302–304 MiB** after 25–28 s | none |
| + streaming, lossless (extra) | same | same | **finished:** 91.4 / 92.7 / 92.3 s, VmPeak 259 / 262 / 259 MiB, VmHWM 191 / 184 / 171 MiB | 7.77 / 7.84 / 7.86 MB, but **decoded data WRONG**: max \|err\| 3839 codes, mean 708–742, PSNR 12 dB |

\* Python VmHWM is cumulative across the 3 frames in one process. Frame 1 alone peaked at 173 MiB, versus ~126 MiB for the production 1600×900 crop.

**Why the streaming lossless result is invalid:**
- On the Mac (cjxl 0.11.1), a synthetic 12-bit PPM (768×512) round-trips EXACTLY with `-d 0` (0 error).
- The same file with `--streaming_input --streaming_output` decodes to garbage: max error 3838, PSNR 7.6 dB.
- So libjxl 0.11's streaming PPM input mishandles maxval-4095 (12-bit) PPMs. The comparison code was verified by the exact round-trip.

## Meaning, and next options (later, not now)

- The 512 MB Pi (≈ 415 MB visible, ~190 MB available with pool mode idle) can't hold a one-shot 12 MP RGB VarDCT/lossless encode under a 300 MiB cap. The Python demosaic of the full frame also costs ~170 MiB.
- Candidates for a later product version, each to be measured:
  1. **Tile the frame:** e.g. 6 tiles of ~1536×1296. Each tile is close to today's 1.44 MP crop, and we have measured that size at VmPeak ~126 MiB.
  2. **Streaming with a 16-bit PPM** (maxval 65535, codes << 4) to dodge the 12-bit bug. Untested; it still killed at d1 in this run.
  3. **The libjxl API with chunked frames** instead of the cjxl CLI.
- **Not measured:** error vs source for the production argv, since no output was produced before the kill.
