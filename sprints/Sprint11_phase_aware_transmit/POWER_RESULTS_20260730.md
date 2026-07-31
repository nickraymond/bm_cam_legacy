# Sprint11 power results

**Date:** 2026-07-30. **Question:** how much less energy per cycle does
bmcam003 consume vs bmcam000, over the last 10 hours?
**Data:** Spotter SD dumps `bmcam000.zip` / `bmcam003.zip` (sha256 in
[run_manifest.json](../../runs/power_review_20260730/run_manifest.json)),
bridge `addr:65` traces, window 2026-07-30 16:19 – 2026-07-31 02:19 UTC
(09:19–19:19 PDT).

## Headline

**bmcam003 used 0.0707 Wh less per cycle than bmcam000 — 0.1568 vs
0.2275 Wh/cycle, a 31.1% saving (ratio 1.45×).** Over the full 10 h:
3.17 Wh vs 4.57 Wh (30.6% less).

| unit | complete cycles | on / period (min) | Wh / cycle | Wh / h | Wh over 10 h |
|---|---|---|---|---|---|
| bmcam003 (SPOT-33507C) | 20 | 14.8 / 30 | **0.1568** | 0.3172 | 3.172 |
| bmcam000 (SPOT-31593C) | 19 (+2 partial dropped) | 19.8 / 30 | **0.2275** | 0.4571 | 4.571 |

## Important nuance — not a controlled A/B

The two units are **not on the same schedule**, so this is not a pure
firmware/pacing A/B like the Sprint10 overnight run. Both run a 30-min
period, but bmcam003 is on the 15/15 soak schedule from its rebuild (bus on
14.8 min/cycle) while bmcam000 runs the usual 20/10 (bus on 19.8 min/cycle).
About half the gap is just the 5-min-shorter bus window paying the ~0.43 W
halted-Pi baseline for less time (5 min × 0.43 W ≈ 0.036 Wh); the rest is
bmcam000's longer active period, visible in the trace at ~0.6–0.8 W. The
schedule-neutral comparison is Wh/h: 0.317 vs 0.457, bmcam003 30.6% lower.
For a like-for-like energy A/B, the units need matched bus schedules first.

## Method and validation

- Same sensor and method as the Sprint10 overnight A/B: bridge `addr:65`
  (dense 10 s trace on both units, reads the downstream camera load, ~0 W in
  bus-off windows — design D9 in `tools/bridge_energy_per_cycle.py`).
- Cycles detected from the trace (power > 0.05 W, gaps ≤ 90 s tolerated);
  partial windows at the span edges dropped, not averaged in. Halted-Pi
  baseline charged to the cycle deliberately — it is what the battery pays.
- Per-cycle spread is tight (sd 0.0033 / 0.0058 Wh; on-window durations
  exactly consistent), so the means are representative.
- Cross-check: bmcam000's 0.2275 Wh/cycle matches the published Sprint10
  SD-derived figure (0.2256, arm A) within 0.8%. bmcam003's 0.1568 is below
  its Sprint10 figure (0.1797) because it now runs the shorter 15-min bus
  window.

## Artifacts

Run folder: [runs/power_review_20260730/](../../runs/power_review_20260730/RESULTS.md)
— interactive coplot ([ab_coplot.html](../../runs/power_review_20260730/ab_coplot.html)),
per-cycle CSV (39 cycles), summary JSON, manifest.

Tool (new, reusable): `tools/sd_bridge_ab_coplot.py` — SD-side companion to
`bridge_energy_per_cycle.py`; also documented as the fast path in the
`nereus-spotter-sd-analysis` skill (§9).
