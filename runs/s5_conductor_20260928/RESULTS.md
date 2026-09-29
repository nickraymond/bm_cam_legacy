# S5 24 h conductor loop — RESULTS (stopped at 19 h, 2026-09-29 15:36Z)

**Gate (0 lost clips): FAIL.** Nick stopped the run at 19 h to start on the heal fixes. The
units did their part: every trigger was acked on the first try, and every clip started at
the backend. The losses come from the Spotter cellular queue (F1) and two heal-tooling
limits (F9, F10). Evidence: `gate.log`, `pulled/events.jsonl`, `pulled/state.json`,
`pulled/summary_stopped.json` (the conductor's `--report`; "lost" there means not complete
at the stop).

## Metrics (20:32Z 09-28 → 15:36Z 09-29)

| | bmcam003 (SPOT-33507C) | bmcam004 (SPOT-31593C) |
|---|---|---|
| triggers / acked (first try) | 38 / 38 | 38 / 38 |
| media row at the backend | 37 (the missing one was triggered 10 min before the stop) | 38 |
| complete on the first send | 5 (13 %) | 24 (63 %) |
| complete after heals | 18 | 12 |
| still partial at the stop | 14 | 1 (+1 still arriving) |
| chunks at the backend | 94.8 % (6161 / 6501) | 96.0 % (6604 / 6883, incl. the arriving clip) |
| trigger → row, p50 / p95 | 13.3 / 16.3 min | 13.3 / 16.3 min |
| trigger → complete, p50 / p95 | 1.7 h / 5.3 h | 15 min / 2.3 h |
| heal commands / chunks asked | 14 / 453 | 16 / 150 |
| Spotter `queue … is full` (09-28 + 09-29) | 497 + 984 | 276 + 387 |

Heal commands: 14 healed, 15 expired (3 cycles without completing).

## Why

- **F1, Spotter cellular queue (not device code):** SPOT-33507C rejects 25–75 messages every
  hour; SPOT-31593C 0–40. Both spiked together at 11–12Z on 09-29 (175–192 per hour), a
  shared cellular or Notecard event. It left the 11:49Z and 11:51Z clips at 58/179 and
  23/177. The losses come in 8-chunk runs (e.g. `100-107,169-176`).
- **F9, backend:** `heal_candidates()` skipped a media missing more than 40 chunks, for
  good. 55202 (55 missing), 55407 (121) and 55409 (154) were never healable. **Fix:**
  nereus-vision-dev PR #68 (heal the first 40 now, the rest on later passes;
  `max_chunks` honoured).
- **F10, conductor:** one heal command per device was held until healed, or for 3 cycles,
  plus still_arriving waits. bmcam003 got about 1 heal per 90 min against a loss every
  cycle, and its backlog grew from 2 to 14 clips. **Fix:** a fresh command every cycle
  (this branch); the backend already leaves out media still arriving, and the unit keeps
  the newest heal per key.
- 55416 lost its START (length unknown): not healable until W9 (S4w) lands on the devices.

## During the run

- nvd PRs #65/#66/#67 merged into staging at 22:52, 23:22 and 23:52Z in quiet windows: no
  no_row, backend_error or cycle_error around them.
- A stale background job from 17:22Z sent one `hld 120` to bmcam003 at its 20:00Z
  scheduled wake (before the loop). It was clamped to 2 min, and the unit halted normally.

## State at the stop

Both units stay_on, trigger-only, on HELD buses (overlay `mode.run: stay_on`), runtime
7d30fea; bm-heal-driver stopped; conductor stopped. Restore steps: README "Stop early / restore".
