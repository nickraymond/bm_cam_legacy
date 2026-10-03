# Sprint28 S0 (camera): distance rungs, and the S1 Mac end-to-end gate

Status: **done 2026-10-02**, desk only (Mac, libjxl **v0.11.1** `cjxl`, effort 5). Written by the
Sprint28 camera build session. Inputs are the rig study DNGs
(`nereus-camera-test-rig/data/s4_20260930`, sha256 in each run manifest, read-only), each with its
own rpicam `--metadata` JSON. **Every number below is a Mac number.** Bytes depend on the libjxl
build (R0.4 records the unit's), and time / RSS on the Pi is R0.3's job.

## 1. Default rungs (`still.raw.distances`, registry v8)

The rule was written before the run (`tools/s28_calibrate_distances.py`). For each byte target,
the rung is the median frame's distance at that size, over 6 frames (cool/warm × stop −1/0/+1,
production `still.crop` [1504, 846, 1600, 900]). It is interpolated on a 0.25 grid and rounded up
to 0.05.

| rung | target | why | distance |
|---|---|---|---|
| 1 | 56 100 B = 195 msgs at 384 chars | today's `still.message_cap` | **3.8** |
| 2 | 50 000 B = 174 msgs | the heal-wake limit (SPEC §3.8) | **4.6** |
| 3 | 43 000 B = 150 msgs | the smaller heal-wake room (SPEC §5.2) | **5.95** |
| 4 | 34 000 B = 118 msgs | complex scenes (reef > indoor card) | **8.25** |

Evidence: `runs/s28_s0_calibration_20261002/{curves.csv, rungs.json, calibration.log}` (558 rows).

Bytes at the grid point nearest each rung, min / median / max over the 6 frames (from curves.csv):

| preset | d 3.75 | d 4.5 | d 6.0 | d 8.25 | d 9.0 |
|---|---|---|---|---|---|
| 1600×900 (default) | 55 093 / 56 457 / 62 235 | 49 253 / 50 486 / 55 994 | 40 934 / 42 573 / 47 209 | 33 080 / 34 990 / 38 712 | 31 279 / 33 081 / 36 692 |
| 2000×1124 (opt-in) | 78 406 / 86 783 / 90 512 | 69 525 / 76 999 / 80 685 | 57 054 / 63 742 / 66 920 | 45 888 / 51 525 / 54 239 | 43 287 / 48 669 / 51 299 |
| 2400×1350 (opt-in) | 110 260 / 129 866 / 132 893 | 97 300 / 114 818 / 117 169 | 79 682 / 93 919 / 95 778 | 63 630 / 75 552 / 77 050 | 60 019 / 71 332 / 72 623 |

**Finding (for the EM):** at e5, **2400×1350 never reaches 56 kB, even at d 9.0** (60–73 kB). It
needs `still.message_cap` > 195 (and more wake time) to fit one wake, or a worse distance than
the grid covers. 2000×1124 fits 56 kB at about d 7.3. This is consistent with the r4 ruling
(those crops are opt-in), but an opt-in 2400×1350 also needs a cap change to be usable. The
study's 2400×1350 at 50 kB (stress ΔE 0.25/0.30) was **e7**, encoded on the Mac.

Mac encode time per rung (4 planes, 1600×900) is ~0.5 s. The Pi Zero 2 W figure from the study
is 6.4 s, so 4 rungs ≈ 26 s, inside the 30 s `still.raw.encode_max_s`. R0.3 measures the real
figure.

## 2. S1 gate: Mac end-to-end against the study's D2 row

`tools/s28_mac_e2e_check.py` takes the study's own field crop (card-centred, 1600×900) and
encodes it with the **production** code (`rc_raw_jxl`, e5) at the study row's byte count. It
decodes the result with the **rig's** study decoder (`raw_planes.decode`) and scores it with the
rig's `metrics.evaluate`. Gate: |Δ stress ΔE00| ≤ 0.02.

| frame set | bytes (study / prod) | d (prod e5 / study e7) | stress ΔE study → prod | Δ | block ΔE med study → prod |
|---|---|---|---|---|---|
| cool air | 48 629 / 48 634 | 5.30 / 4.85 | 0.114 → 0.120 | +0.005 | 0.38 → 0.44 |
| cool uw | 50 073 / 50 076 | 4.21 / 3.84 | 0.182 → 0.178 | −0.004 | 0.51 → 0.56 |
| warm air | 49 933 / 49 930 | 5.07 / 4.58 | 0.139 → 0.126 | −0.013 | 0.38 → 0.40 |
| warm uw | 51 236 / 51 246 | 3.96 / 3.62 | 0.230 → 0.224 | −0.006 | 0.46 → 0.50 |

**PASS** (worst |Δ| = 0.013). Evidence: `runs/s28_s1_mac_e2e_20261002/{e2e.csv, e2e.log,
run_manifest.json}`. Two notes:
- The block ΔE median is 0.02–0.06 worse at e5 than the study's e7. It is not gated, and it is
  still far below today's JPEG (0.94–1.04).
- The study's card-centred crop is easier to code than the production `still.crop` (≈ d 5 vs
  d 3.8–4.6 at 50 kB).

## 3. What S0 did not do

- The crop × bytes grid at 62 / 75 / 112.5 kB (SPEC §8 S0) is in `curves.csv` as bytes per
  distance, but it has no colour scores. The quality question for the larger presets is
  opt-in-only after r4, so it was not scored.
- The decoder candidate in a Debian 12 container is the backend session's part of S0.
- hydrium Pi RSS: not in the rig data. It stays unmeasured.
