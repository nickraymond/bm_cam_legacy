# T2 — message size × pace, bmcam003 / SPOT-33507C, 2026-10-06 21:30–23:46Z — RESULTS

Gate `hil/gates/T2_message_size_bmcam003.md` (bars registered ff106b1 before the first burst); plan `t2_plan.csv`;
27 bursts, ~35 kB per arm, synthetic non-ingestable payloads, 0 send errors; scorer `hil/tools/hil_t1_score.py`
→ `analysis/t2_bursts.csv`. accept % = Submitted / sent; drain = Spotter `Added message(id)` → `Queuing message id`
(to the Notecard); B/s = accepted bytes / burst send span (what the Spotter took per second of sending).
20/27 bursts sum to exactly sent; 7 are off by 1–4 (a Spotter HDR message rejected in the same window, or a log gap).

| size B | pace s | n | accept % | drain p50 / p90 s | accepted B/s |
|---|---|---|---|---|---|
| 96 | 1.3 | 2 | 98.6 / 97.0 | 0.03–0.04 / 0.04 | 73 / 72 |
| 160 | 1.3 | 2 | 96.8 / 94.5 | 0.04 / 0.05–0.07 | 120 / 117 |
| 224 | 1.3 | 2 | 96.8 / 96.8 | 0.05 / 0.06 | 168 / 168 |
| 288 | 1.3 | 2 | 95.9 / 95.9 | 0.06 / 0.06 | 214 / 214 |
| 384 (today) | 1.3 | 2 | **78.0 / 100.0** | 0.07 / 0.08 | 233 / 299 |
| 450 | 1.3 | 2 | 85.9 / 88.5 | 0.09 / 0.10 | 301 / 310 |
| 600 | 1.3 | 2 | 63.8 / 55.2 | 1.2–1.4 / 10.6–10.8 | 300 / 259 |
| 900 | 1.3 | 2 | 51.3 / 41.0 | 2.3–2.6 / 10.9–14.5 | 364 / 292 |
| 1000 | 1.3 | 2 | 42.9 / 42.9 | 5.3–5.4 / 6.1–6.4 | 339 / 339 |
| 96 | 0.6 | 1 | 96.4 | 0.03 / 0.04 | 155 |
| 160 | 0.6 | 1 | 97.3 | 0.04 / 0.58 | 261 |
| 224 | 0.6 | 1 | **69.9** | 0.33 / 1.63 | 263 |
| 288 | 0.6 | 1 | **95.9** | 0.71 / 0.96 | **464** |
| 384 | 0.6 | 1 | 73.6 | 1.31 / 1.58 | 476 |
| 450 | 6.0 | 1 | 100 | 0.09 / 0.09 | 76 |
| 600 | 6.0 | 1 | 100 | 5.9 / 10.8 | 102 |
| 900 | 6.0 | 1 | **64.1** | 14.6 / 19.2 | 99 |
| 1000 | 6.0 | 1 | 97.1 | 8.0 / 10.8 | 167 |

## Verdict against the pre-registered bars

| id | result |
|---|---|
| T2.1 small drains fast (≤ 384 B @ 1.3 s: drain p50 < 1 s AND accept ≥ 93 %) | **NOT SUPPORTED as written** — drain 0.03–0.08 s everywhere ✓, but one 384 B run accepted 78 % (the other 100 %) |
| T2.2 cliff above ~400 B (≥ 450 B @ 1.3 s: accept ≤ 50 % or drain p50 ≥ 5 s) | **NOT SUPPORTED** — 450 B is still on the fast path (drain 0.09 s, 86–89 %); the slow path (drain p90 ≈ 10.6–14.5 s) starts between 450 and 600 B and loss grows gradually to 43 % at 1000 B |
| T2.3 big needs slow pacing (≥ 450 B @ 6 s: accept ≥ 90 %) | **NOT SUPPORTED** — 450/600/1000 B 97–100 %, but 900 B only 64 % (drain p50 14.6 s) |
| T2.4 small-fast beats big-slow (best B/s among ≥ 95 % arms has size ≤ 400 B) | **SUPPORTED** — best = **288 B @ 0.6 s: 464 B/s at 95.9 %**; best big-slow 1000 B @ 6 s = 167 B/s |
| T2.5 today's setting is not the optimum (≥ 25 % better at ≥ 95 %) | **SUPPORTED** — 288 B @ 0.6 s is +55 % vs 384 B @ 1.3 s's best run (299 B/s) and +74 % vs the mean (266) |
Prediction "384 B @ 0.6 s best" was **wrong**: 384 @ 0.6 s accepted only 73.6 % (drain p50 1.3 s > the 0.6 s pace).
At 1.3 s the big sizes show high raw B/s (300–364) only because half the messages are rejected and must be healed.

Caveats: n = 1 at 0.6 / 6.0 s and n = 2 at 1.3 s; the 384 @ 1.3 s pair disagrees (78 vs 100 %) and 224 @ 0.6 s
(70 %) is out of line with 160 and 288 @ 0.6 s (97 / 96 %) → the 0.6 s region needs repeats before a production change.
B/s is what the Spotter accepted, not what Sofar ingested (the backend ignores these payloads by design).
Power: nereus000 VBAT 3.63 V between wakes; dips to 3.29–3.43 V only in the :00–:12 wake minutes; no stop.

## Restore (00:18–00:19Z 10/7)
bmcam003 re-armed from ~/hil_backup/20261006T211041Z, snapshot after_t2, halted; `hil_restore_schedule.sh SPOT-33507C`
→ 1 / 3600000 / 600000 confirmed; bus_on_watch off; `hil-r1-cmdres.timer` restarted (its start ran one normal action).
Wrap-up ran ~30 min late (the scheduled check did not fire); the bus stayed held on 23:46–00:18Z with no bursts.
