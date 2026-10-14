# cjxl -p on the B3a encode: cost check (desk, 2026-10-09)

Purpose: back the `still.raw.progressive` key (bm PR, Nick 2026-10-09 "progressive preview, the
format's own mode only"). The quality / first-preview numbers are the EM desk study
(`runs/jxl_progressive_20261009` in the EM worktree, `docs/wire/NOTE_progressive_jxl_preview_2026-10-09.md`):
-p = +1.6 % bytes at equal d (max +2.7 %), -0.1 SSIMULACRA2 at equal bytes, 75 %-prefix 40.7 dB vs 34.7 dB.
This folder adds what that study did not measure: memory under the unit's guard, and the search's
encode count.

Input: `rgb.ppm` = djxl of `tests/fixtures/s28/blob_v2_bmcam004_57521.nrjxl` (real bmcam004 frame,
1600x900 12-bit codes; a re-encode of a lossy decode, so its bytes(d) curve is flatter than a fresh
frame's: do not read slopes from it).

| check | without -p | with -p | source |
|---|---|---|---|
| bytes at d 3.42 (cjxl 0.11.2 arm64) | 52,292 | 53,436 (+2.2 %) | results_vmpeak.txt |
| VmPeak under the 250 MiB guard (3 reps) | 120.7-124.0 MiB | 137.5-180.6 MiB, 0 kills | results_vmpeak.txt |
| VmHWM (RSS) | 97.9-98.5 MiB | 119.3-120.2 MiB (+22 MiB) | results_vmpeak.txt |
| wall (container, not Pi) | 0.77-0.79 s | 0.78-0.80 s | results_vmpeak.txt |
| search replay, 31 curves x1.016 (production choose_rate) | 1/2/3 encodes 6/23/2 | 5/23/3, fill P50 0.969, 0 rfb | replay.txt |
| real search on the fixture (Mac cjxl 0.11.1) | d 2.727, 55,549 B, 193 msgs, 2 encodes | d 3.264, 54,193 B, 189 msgs, 3 encodes | tests/test_s28_progressive.py |

Reading: -p is safe under the guard (worst VmPeak 181 MiB < 250 MiB, headroom 1.38x vs 2.0x) but
costs ~22 MiB more real RAM during the encode. NOT measured: VmPeak / RSS / time on a real Pi Zero 2 W
(bench item before the key is relied on). Scaling the search prior by 1.016 changes 1 of 31 frames
(3 -> 2 encodes): not done.

Re-run: `.venv-dev/bin/python runs/s28_progressive_p_20261009/replay_p.py` (from the repo root);
the docker line is in results_vmpeak.txt.
