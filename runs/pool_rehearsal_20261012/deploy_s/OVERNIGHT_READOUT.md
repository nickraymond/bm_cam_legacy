# Overnight read-out: fdeb01a (#146 heal_order=after + #147 progressive), bmcam003 + bmcam004, Tue 10/13 10 PM → Wed 10/14 7 AM PDT

**Verdict: no regression on any pre-set bar. Recommendation: KEEP both keys.**
- Sources: the unit cycle logs (`deploy_s/<unit>/fulllogs/`), the per-wake extracts (`hourly_*.txt`) and the cjxl argv samplers.
- nereus000 (console + media table) has been unreachable since ~12 AM. Spotter-side acceptance is not measured.

| Bar (set 10/13 9:15 PM) | Baseline (8 PM / 9 PM, 9ec4cb7) | bmcam003, 10 wakes | bmcam004, 10 wakes | Verdict |
|---|---|---|---|---|
| CFG | 44b81292 v11 | 0cb7ad9e v13, every wake | 0cb7ad9e v13, every wake | PASS |
| Fallback / ERR | 0 / 0 | 0 / 0 | 0 / 0 | PASS |
| -p in cjxl argv | n/a | 23 sampled pids, 0 without | 28 sampled pids, 0 without | PASS |
| cjxl peak (bar ≤ 150 MiB) | 98 MiB | max 116.9 MiB | max 116.9 MiB | PASS |
| msgs (cap 195) | 189–191 | 180–195 | 101–195 | PASS (see the ladder note) |
| Transmit complete (sent = N/N) | yes | 10/10 | 10/10 | PASS |
| Cycle end / halt (budget 480 s) | 470.4–470.7 s / 480.2–481.6 s | ≤ 470.7 s / ≤ 480.6 s | ≤ 470.7 s / ≤ 480.6 s | PASS |
| Heal order | before START | 6 heal wakes, 179 chunks, all after END | 7 heal wakes, 52 chunks, all after END | PASS (works as designed) |
| Heals landed | — | unknown on the Spotter side (no console); same chunks re-asked 2 wakes later several times | same | OPEN: the backend table decides |

## Findings

1. **bmcam004 low-quality ships (4/10: 12, 4, 5, 6 AM) are existing ladder behaviour, not #147.**
   - What happens: search3 lands at 196–198 msgs, just over the cap, so the unit ships search1 (d=2.595, 101–102 msgs, ~29 KB).
   - Pre-#147 weekend (9ec4cb7, no -p): bmcam004 21/67 wakes (31 %), bmcam003 12/67 (18 %). All pulled pre-#147 logs: 35/161.
   - Overnight: bmcam003 0/10, bmcam004 4/10. n = 10 can't resolve -p's +2 % bytes.
   - Post-pool fix: on a search3 overshoot, step d up from search3 (one more encode, ~10 s) instead of dropping to search1.
2. **Runs of exactly 10 lost chunks** = Spotter `MS_Q_CELLULAR_ONLY` full during ~10.8 s Notecard hand-off stalls. Present on both Spotters since 10/10. See `QUEUE_FULL_FINDING.md`.
   - Heal chunks sent after END cross the same queue. The repeated re-asks fit heal chunks being lost there, or a re-ask before Sofar exposed the rows.
3. **nereus000:** the weak input since 9:00 PM (VIN 4.66 V, battery not charging) ran it down at ~12 AM. The cameras were unaffected. It needs the supply fixed before any console work.
