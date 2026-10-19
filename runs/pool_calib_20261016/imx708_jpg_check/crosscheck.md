# Cross-check: primary vs check input (same camera)

Primary `/Users/nickbuemond/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/20261017_152713_pool049_imx708_dng/imx708` (rational) vs check `/Users/nickbuemond/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/20261017_153048_pool049_imx708_jpg_check/imx708` (rational). Bar: docs/pool_calib_prereg_20261016.md (R3).

| item | value | limit | within |
|---|---|---|---|
| Δfx / fx | -0.026 % | ±0.5 % | True |
| Δcx | -1.03 px | ±5 px | True |
| Δcy | -0.98 px | ±5 px | True |
| ray difference at the B4 probes (max) | n/a px | 1 px | False |

Per probe (px): TL n/a, TR n/a, BL n/a, BR n/a, top 1.95, bottom 1.35, left n/a, right n/a

Distortion primary [11.33861, -41.08505, 0.00472, 0.00512, 27.27714, 11.08144, -44.97969, 36.96861] vs check [-5.22883, 8.89939, 0.00436, 0.00491, -4.87695, -5.51938, 10.13402, -6.19657]

**OUTSIDE the bar: reported as a finding (ISP effect); the primary calibration still ships.**
