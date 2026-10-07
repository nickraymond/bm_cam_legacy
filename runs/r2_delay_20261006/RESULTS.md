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
| 11:00 | 04:00 | A (heal 0, budget_left 469 s) | **10.38** (11/106) | 30×10, 49×1 (11:01:08–11:01:28) | 10 | 11 (11:01:11.2–11:01:30.6) | 9 / 50.3 s / 11 of 11 | no HDR crossed → HDR 0 / other 11 | 11:00:38 | 11:02:27 | 308 s | **no** |

### Desk check: the early non-HDR stalls (07:01:45Z, 11:01:10Z) vs the clean 08Z wake (console only, read-only)
**Answer: hypothesis NOT supported.** No Spotter-own LEGACY/network/topology message is queued and no sync starts
before either stall. The only Spotter-own activity in the 60 s before both stalls is the bridge's topology sampler at
~:01:05 (`Bridge topology in topology sampler: c3c564b91856226c, 53171fa3d81a8e6f` → `Got CRC a73850dc OLD CRC
a73850dc` → `CRCs match, not updating`), which also runs at 08:01:05 in the clean wake and sends nothing (CRC
unchanged). No `Attempting to Sync` before :02 in any of the three wakes; no connected/modem lines; Notecard fill
climbs 5 → 12 % through the burst in all three (the stall wakes are not fuller). At bus-on (08:00:01) the 5-min HDR
(6129 B) + `Neighbor 53171fa3d81a8e6f added` + a topology sample at 08:00:05 appear, then the camera's own boot messages
(168 / 259 / 408 B: WS, ack/heartbeat, <CF>). Evidence (verbatim, Spotter times):
- 07Z: 07:01:05.613 topology sampler … 07:01:05.625 "CRCs match, not updating"; GPS "Dropped gps sentence" 07:01:10 /
  :21 / :32; first reject 07:01:47.761 — no other non-camera line.
- 11Z: 11:00:33.593 / 11:00:39.656 camera boot messages (259 / 408 B); 11:01:05.937 topology sampler → "CRCs match, not
  updating"; first reject 11:01:11.222.
- 08Z (clean): same 08:01:05.453 topology sample, same CRC lines, no stall.
The stall onset is a Notecard-side hand-off delay with no visible Spotter trigger in the console.
| 12:00 | 05:00 | A (heal 0, budget_left 470 s) | **12.62** (13/103) | 33×1, 39×2, 44×10 (12:01:11–12:01:34) | 10 | 13 (12:01:13.7–12:01:34.0) | 12 / 47.0 s / 13 of 13 | no HDR → HDR 0 / other 13 | 12:00:37 | 12:02:23 | 308 s | **no** |
| 13:00 | 06:00 | B (applied; heal 0, budget_left 472 s) | **30.19** (32/106) | 1×6, 21×1, 26×2, 31×12, 50×1, 57×10 (13:03:59–13:04:56) | 12 | 33 (13:04:01.7–13:05:08.0) | 21 / 48.6 s / 33 of 33 | no HDR at :05 this hour → stalls start at the **health-check sync** (see note) | 13:03:58 | 13:05:47 | 497 s | **no** |

13Z note: no 5-min HDR at 13:05 this hour (HDRs at 13:00:01 and 13:10:01 only). The Spotter's health check ran at
**13:03:19** (`Running health check!` + `Reboot limit reached, ignoring. (source 7)` + LEGACY 37 B) → `Attempting to Sync`
13:03:19 → `Waiting for TX` → `All messages sent successfully!` 13:04:06.9 (47.6 s). The B burst started 13:03:58,
inside that sync; the hand-off stalls run 13:03:59 → 13:05:07. So B's hold (START ~:03:57) sits on two Spotter events:
the boot-anchored health-check sync (:03:19–:04:07 today; :03:22–:03:33 on other wakes) and the :05 HDR when present.
| 14:00 | 07:00 | B (applied; heal 0, budget_left 472 s) | **11.36** (15/132) | 62×4, 86×10, 105×1 (14:05:01–14:05:45) | 10 | 15 (14:05:03.9–14:05:47.7) | 13 / 48.3 s / 15 of 15 | HDR crossed 14:05:01 (4045 B) → HDR 15 / other 0; health-check sync 14:03:18 → sent 14:04:00.2 (START 14:03:58 just before its end, no rejects there) | 14:03:58 | 14:06:13 | 497 s | **no** |

**Tally after 2 ABBA blocks (8 counted wakes; EM: no rule stop, continue until Nick decides):**
| arm | n | loss % per wake | median | cause of every hole |
|---|---|---|---|---|
| A (0 s) | 4 | 12.75, 0.00, 10.38, 12.62 | 11.50 | early non-HDR Notecard hand-off stall at ~:01:10–:01:45 (3/4) |
| B (230 s) | 4 | 7.27, 5.71, 30.19, 11.36 | 9.32 | Spotter events at START ~:03:58–:06:13: the :05 HDR (3/4) and the health-check sync (:03:18–:04:07; 1/4) |
Neither arm is clean on this Spotter; the hold moves the burst from one hazard window to another.
