# R2-DELAY — RESULTS (bmcam003 / SPOT-33507C)

Gate: `hil/gates/R2_DELAY.md`. Setup 2026-10-07 05:00Z (patch f1dfe38 installed, sha 544f48b3; pacing 1.0 s; effective
config in `pulled/bmcam003_effective_setup.txt`). 06:00Z wake = setup (driver trg + setup command), not counted.

| wake (Z) | PDT | arm | loss % | gaps (start idx × len) | max gap | queue_full (first–last) | hand-off stalls >1 s (n / longest / rejects in) | START | burst end | wake→halt | clean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 07:00 | 00:00 | A | **12.75** (13/102) | 61×1, 65×12 (07:01:45–07:02:03) | 12 | 13 (07:01:47.8–07:02:03.0) | 5 / 13.35 s / 13 of 13 | 07:00:43 | 07:02:28 | 308 s | **no** |

07Z note: no Spotter-own event in the loss window (health check 07:03:22, HDR 07:05:01). The Spotter → Notecard hand-off
(`Added` → `Queuing message`) stalled from 07:01:45: drain 0.05–0.08 s before, then 2.7 / 2.5 / 1.3 / **12.6 / 13.3 s**,
back to 0.08 s by 07:02:09. Wire len 528 B per chunk (384 chars + keyed framing) — between T2's fast (≤ 450 B) and
slow (≥ 600 B) path sizes. heal_msgs / budget_left of this wake: from its cycle log at the 08Z window.

Chunk framing: bench 528 B = R1 keyed framing (what the reef would send after an R1 update); the reef's current legacy
framing is ~390 B (EM 10/7). Kept for both arms.
| 08:00 | 01:00 | A (extra) | 0.00 (0/110) | – | 0 | 0 | 1 / 46.8 s (08:10, after END; the :10 report HDR) / 0 | 08:00:43 | 08:02:36 | 318 s | **yes** |
`[R2DELAY]` lines (from schedule.json): 07Z `start_delay_s=0 uptime=35.36s wait=0.0s burst_est=105s heal_msgs=0 budget_left=464s skipped=False`;
08Z `start_delay_s=0 uptime=35.8s wait=0.0s burst_est=113s heal_msgs=0 budget_left=464s skipped=False`.
| 09:00 | 02:00 | B (applied; heal_msgs 0, budget_left 468 s) | **7.27** (8/110) | 63×7, 91×1 (09:05:01–09:05:31) | 7 | 8 (09:05:04.1–09:05:32.7) | 8 / 43.5 s / 8 of 8 — starts at the **09:05:01 HDR** | 09:03:57 | 09:05:50 | 498 s | **no** |

HDR attribution (EM 10/7 ~09:4xZ; scorer: rejects inside a hand-off stall within 60 s after an HDR = HDR):
07Z A: crossed none; rejects HDR 0 / other stalls 13. 08Z A: none; 0/0. 09Z B: crossed 09:05:01; HDR 8 / other 0.
**EM decision 10/7:** the rules do not cover "both arms have holes from different causes" (A: Notecard stall, B: :05
HDR) → no stop, no move to R2-L1; keep ABBA running until Nick is up (~07:00 PDT); delay value unchanged.
| 10:00 | 03:00 | B (applied; heal 0, budget_left 469 s) | **5.71** (6/105) | 63×6 (10:05:01) | 6 | 6 (10:05:04.1–10:05:09.2) | 4 / 44.1 s / 6 of 6 | HDR crossed 10:05:01 → HDR 6 / other 0 | 10:03:57 | 10:05:45 | 498 s | **no** |

First block done (2 A + 2 B; EM: no rule stop, continue): A 12.75 % (Notecard stall, no HDR) and 0 %; B 7.27 % and
5.71 %, both entirely at the :05 HDR (the 230 s hold puts START at ~:03:57, so every B burst crosses :04:59–:05:01).
