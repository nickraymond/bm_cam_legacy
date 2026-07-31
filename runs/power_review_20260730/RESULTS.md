# Power review — bmcam000 vs bmcam003, last 10 h (2026-07-30)

**Window:** 2026-07-30 16:19 – 2026-07-31 02:19 UTC (09:19–19:19 PDT), the
last 10 h of common SD-card coverage. **Source:** each Spotter's bridge-node
`addr:65` trace (10 s cadence) from the SD dumps `bmcam000.zip` /
`bmcam003.zip` — same sensor and method as the Sprint10 overnight A/B
(`runs/sprint10_overnight_20260729/`).

## Headline

| unit | complete cycles | on / period (min) | Wh / cycle | Wh / h | Wh over 10 h |
|---|---|---|---|---|---|
| **bmcam003** (SPOT-33507C) | 20 | 14.8 / 30 | **0.1568** | 0.3172 | 3.172 |
| **bmcam000** (SPOT-31593C) | 19 (+2 partial dropped) | 19.8 / 30 | **0.2275** | 0.4571 | 4.571 |

**bmcam003 used 0.0707 Wh less per cycle — 31.1% less (ratio 1.45×), and
30.6% less over the full 10 h.**

## Why the gap

The two units are on the same 30-min period but different schedules
(measured from the trace, not assumed):

- **bmcam003 runs 15 min on / 15 min off** (the 15/15 soak schedule from the
  rebuild) — bus powered 14.8 min per cycle.
- **bmcam000 runs 20 min on / 10 min off** — bus powered 19.8 min per cycle.

Since the Pi halts early in the window on both units and the halted-Pi
baseline (~0.42–0.44 W on the bridge trace) dominates the on-window energy,
the 5-min-shorter bus window accounts for most of the difference:
5 min × 0.43 W ≈ 0.036 Wh, i.e. roughly half of the 0.0707 Wh/cycle gap; the
rest is bmcam000's longer active period visible in the trace (red stays near
0.6–0.8 W for most of its window).

**Caveat:** this is NOT a controlled A/B of firmware or pacing — the two
units run different bus schedules, so per-cycle energy mixes schedule and
behavior. For a like-for-like comparison, compare Wh/h (0.317 vs 0.457,
bmcam003 30.6% lower) or re-run with matched schedules.

Per-cycle spread is tight (bmcam003 sd 0.0033 Wh, bmcam000 sd 0.0058 Wh;
on-window durations exactly consistent cycle to cycle), so the means are
representative.

## Consistency with prior published numbers

bmcam000 at 0.2275 Wh/cycle matches the Sprint10 overnight SD-derived figure
(0.2256 Wh/cycle, arm A) within 0.8%. bmcam003's 0.1568 is lower than its
Sprint10 figure (0.1797) because it now runs the shorter 15-min bus window.

## Artifacts

- [ab_coplot.html](ab_coplot.html) — interactive coplot (zoom/pan/tooltip) + summary table
- [ab_cycles.csv](ab_cycles.csv) — per-cycle start/end/duration/Wh (39 complete cycles)
- [ab_summary.json](ab_summary.json), [ab_coplot_data.json](ab_coplot_data.json)
- [run_manifest.json](run_manifest.json) — source zips (sha256), window, method notes

Regenerate: see `command` in run_manifest.json (tool: `tools/sd_bridge_ab_coplot.py`).
