# Phase E — design of experiment (proposed 2026-07-29, pending Nick's go)

Full factorial, 2 message-counts × 2 delays, n = 3 replicates, run on
both units in parallel (unit = blocking factor).

## Held constant

| Parameter | Value |
|---|---|
| Message size | 384 characters (production) |
| Network type | cellular_only (same path as image chunks) |
| Drain between bursts | 300 seconds |
| Units | bmcam003 / SPOT-33507C and bmcam000 / SPOT-31593C, both at once |

## Factors

| Factor | Levels |
|---|---|
| Messages per burst | 200 (production sweet spot), 300 (stress) |
| Delay between messages | 1.0 s, 1.5 s |

## The 4 cells

| Cell | Messages | Delay | Burst duration | 5-min boundaries spanned | Reps per unit | Reps pooled |
|---|---|---|---|---|---|---|
| A | 200 | 1.0 s | 3 min 20 s | 0 or 1 | 3 | 6 |
| B | 300 | 1.0 s | 5 min 00 s | exactly 1 | 3 | 6 |
| C | 200 | 1.5 s | 5 min 00 s | exactly 1 | 3 | 6 |
| D | 300 | 1.5 s | 7 min 30 s | 1 or 2 | 3 | 6 |

Cell A is the only one that can fit between two boundaries — at
production scale the grid is unavoidable.

## Response variables (per burst)

| Response | Source |
|---|---|
| Messages delivered / sent, loss % | backend join, `analyze_queue_drain.py` |
| Gap count, first-gap sequence and time | same |
| Blackout episodes: start, duration, messages cost | Spotter console, `correlate_console.py` |
| 5-min boundaries actually spanned | computed from send log |

## What the result discriminates

Comparing cell **B vs D** (same 300 messages, different delay) and
**A vs C**:

| Model | Prediction for D vs B |
|---|---|
| loss ≈ count × blackout ÷ 300 s (delay cancels out) | **same loss** |
| DESIGN D16: loss ≈ blackout ÷ delay per burst | D loses **⅔** of B |
| loss ∝ boundaries spanned | D loses **more** than B |

Comparing **A vs B** (same 1.0 s delay, 200 vs 300 messages) isolates
whether message count is the lever — which is what would make
`message_cap`, not `image_transmit_delay_seconds`, the value to ship.

## Time budget

| Item | Per unit |
|---|---|
| Bursts | 12 |
| Messages sent | 3 000 |
| Transmit time | 62 min 30 s |
| Drain time (11 × 300 s) | 55 min |
| **Total wall clock** | **≈ 1 h 58 min** |

Both units run concurrently, so total bench time ≈ 2 h for 24 bursts /
6 000 messages. Add 13–30 min backend lag before the final analyzer pass
→ **results ≈ 2 h 30 min after start**.

## Conditional follow-up (not in the 2 h above)

If A vs B shows count is the lever, add **100 messages @ 1.0 s, n = 3**
(1 min 40 s per burst, ≈ 20 min per unit) to confirm the `message_cap`
direction directly before recommending a ship value.
