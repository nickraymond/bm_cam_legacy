# T1 — Spotter queue burst test, bmcam003 / SPOT-33507C, 2026-10-06 16:05–18:57Z — RESULTS

Gate: `hil/gates/T1_burst_queue_bmcam003.md` (bars pre-registered); plan as run: `t1_plan.csv` + `PLAN_NOTES.md`
(b-settle on the health-check sync, committed 660bb9f before the first burst). Sender `hil/tools/hil_t1_score.py` /
`hil_t1_burst.py`; 18 bursts × 120 synthetic 384 B messages, 0 send errors. Counts from the nereus000 console:
accepted = `Submitted spotter/transmit-data … cell-only queue`, rejected = `MS_Q_CELLULAR_ONLY is full`.
Per-burst table: `analysis/t1_bursts.csv`.

| arm | n | rejected per burst | median | min–max |
|---|---|---|---|---|
| a-overlap (start :09:30, report sync at :10:09–:10:20 inside) | 3 | 39, 29, 54 | **39** | 29–54 |
| b-settle (start :05:03, 45 s after the health-check sync) | 3 | 1, 3, 1 | **1** | 1–3 |
| clear-steady (:25, :45) | 6 | 5, 5, 5, 5, 5, 5 | **5** | 5–5 |
| clear-pair (:35, :55; 2 msgs 0.1 s apart, then 2.5 s) | 6 | 13, 26, 13, 9, 10, 13 | **13** | 9–26 |

## Verdict against the pre-registered bars

| claim | bar | result |
|---|---|---|
| **T1.a** overlap drives rejects | median(a) ≥ 28 AND median(clear-steady) ≤ 21 AND every a > clear-steady median | **SUPPORTED** (39 ≥ 28; 5 ≤ 21; 29, 39, 54 all > 5) |
| **T1.b** a 45 s settle avoids it | median(b) ≤ 21 | **SUPPORTED** (1) |
| **T1.c** burst shape matters (pair better) | median(pair) ≤ 0.5 × median(steady) = 2.5 | **NOT SUPPORTED** — the pair shape is WORSE (13 vs 5) |

Notes: clear-steady (5) came in below X1's predicted 8–21; the overlap range (29–54) matches X1's 28–49 (one above).
One clear-pair burst (16:55) counts 92 + 26 = 118 of 120 (2 messages unaccounted: console capture gap or a log line
lost); every other burst sums to exactly 120. The Notecard fill rose within each burst and drained at each sync.
Rig: bus held on 15:41:30–19:04Z; no VBAT/charger alerts.

## Restore (19:05Z)
cron re-armed from ~/hil_backup/20261006T154115Z, snapshot after_t1, halted; `hil_restore_schedule.sh SPOT-33507C`
→ 1 / 3600000 / 600000 confirmed; bus_on_watch off; `hil-r1-cmdres.timer` restarted (its start ran one normal action:
set mode.media=still for 20:00Z, cid 1000145; next run 19:20Z).
