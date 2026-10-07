# G4 — RESULTS (`runs/g4_outdoor12h_20261002`)

**G4: FAIL** — the units ran 24/24 wakes cleanly, but only 17/28 clips completed within 3 h (SPOT-33507C 5/14),
2 of 6 triggers produced no delivered clip, and 9 of 32 unit acks never reached the backend.

Times: UTC in the tables (the logs are UTC); PDT = UTC − 7. Tested config: production schedule (1 h / 10 min),
per_boot + real halt, mixed media alternating hourly, **video.send.message_cap 126 from the 08:00Z wake** (190 before,
phase 2), heal auto-send cap 24/day per Spotter.

## Setup

| unit | Spotter | runtime sha | config | bus | cron |
|---|---|---|---|---|---|
| bmcam003 | SPOT-33507C (bridge c3c564b91856226c) | development 34a6222 | base 67f930c4 + overlay mode.media / message_cap 126 | 1 / 3600000 / 600000, align 5 min | armed, per_boot, halt |
| bmcam004 | SPOT-31593C (bridge 0e582dd12c1e1480) | development 34a6222 | same | same | same |

Bench owner: Test Engineer. Other sessions on the hardware during the run: none.

Window: T0 = the 04:00Z wake (21:00 PDT Fri 10/2) → 16:00Z (09:00 PDT Sat 10/3), + 3 h completion tail.
Units: bmcam003 (SPOT-33507C), bmcam004 (SPOT-31593C), outdoors in the box, mains, nereus000 on both consoles.
Runtime: development 34a6222 (#106, registry v7), config base 67f930c4 + the remote-config overlay the test drove.
Spec: `hil/gates/G4_outdoor_tethered_12h.md` (as amended 10/2 evening). Test Engineer, impartial; verdict from artifacts.

## Verdict: **FAIL** (D1: G4.3; D3: G4.6; D4: G4.7, G4.12)

The units themselves were solid: 24/24 wakes, 0 unscheduled bus drops, 0 Spotter resets in the window, every Pi
halted inside its window (498–524 s of 600 s), and every one of the 32 backend commands was executed and acked by the
unit (console). What fails is **delivery latency and the evidence trail**: heals take two to three wakes, so on
SPOT-33507C a clip that needs any heal lands at ~190 min (3 h limit), and ~1 in 4 unit acks never reaches the
backend (Spotter queue, F-G3-10), so the backend shows trg/set commands that ran as "late"/"superseded".

| id | criterion | result | evidence / note |
|---|---|---|---|
| G4.1 | production config | **PASS** (RC1 sha: N/A) | every window is bus 10 min / hour (`analysis/windows.csv`), cycle logs `per_boot`, `power_halt enabled=True dry_run=False mode=halt`, cron armed; bridge read-back at the production switch (`runs/s27_m_ladder_20261002/RESULTS.md`); heal cap 24/day (EM). RC1 is not frozen yet (Mon 10/5): units ran development 34a6222. |
| G4.2 | every wake, no bus drops | **PASS** | 24/24 windows (12 per unit, 04:00–15:00Z), each with Pi on and a START; 0 unscheduled bus-off; 0 `rebootctl`/charge-mode lines since 03:30Z (both consoles). |
| G4.3 | D1: complete ≤ 3 h | **FAIL** — 17/28 | SPOT-31593C 12/14, SPOT-33507C 5/14 (`analysis/media.csv`, table below). Every SPOT-33507C clip that needed a heal: 190–195 min. |
| G4.4 | 0 redundant heals | **PASS** (by the written rule) | 25 heal requests in the window, 0 asked for a clip the backend already held complete (`analysis/heals.csv`). WARN: 7 SPOT-33507C requests re-asked ~86 chunks still queued at the unit (in flight), spending cap. |
| G4.5 | heal cap | **PASS** | max 20 (SPOT-33507C, ids 100124–100143) and 18 (SPOT-31593C, 100098–100115) heal commands in any 24 h ≤ 24. |
| G4.6 | D3: ≥ 3 trg/unit, each → one media row complete ≤ 3 h | **FAIL** | 3 sent per unit (backend Sofar lane, amended plan). See the trg table: 2/6 delivered ≤ 3 h; 2 captured but never sent (budget defect, finding 5). |
| G4.7 | D4 visible | **FAIL** | heal requests + `<HL>` statuses are all in `/systems/{spot}/heal-events` (the logs page source). But 9 of 32 unit acks never reached the backend (console shows the unit's `OK`): 4 trg show "late" (004 1000048/1000054/1000059, 003 1000025) although 2 of them produced clips; 5 sets show "superseded" though they took effect. The log does not show what happened. logs.html itself not opened (observer gap). |
| G4.8 | 0 SSH writes | **PASS** | only reads over ssh to the units (`ls`, `scp` of `cron_logs/`); all changes went through the backend. |
| G4.9 | thermal / power sanity | **PASS** (passive) | 0 undervoltage / throttle / thermal lines in the 30 unit cycle logs 04:00–18:00Z (`pulled/cycle_logs/`); 0 Spotter rebootctl / charge-mode lines. `post` N/A (console observe-only, amended plan). |
| G4.10 | power-cycle stub cuts | **PASS** (none in the window) | 0 events 04:00–16:00Z. Pre-T0 events (not counted): 003 00:04Z rebootctl reset (clips 0e510h, 0e59kp), 004 23:04Z stub (0e56rl). |
| G4.11 | commanded = captured media | **PASS** | every wake's media = the alternator's command with a constant lag per unit: SPOT-31593C 1 wake, SPOT-33507C 2 wakes (its commands reach the Spotter after the :08 halt). 0 wrong media beyond the lag (table below). 04:00Z: no command (backend outage 03:20–04:25Z), stayed video. |
| G4.12 | every command acked or confirmed | **FAIL** — 30/32 | 32 commands (16/unit): unit `OK` on the console for 32/32; backend ack 23/32 (003 14/16, 004 9/16). Sets with a lost ack are all confirmed by the media type of the wake after (G4.11). trg 1000048 / 1000054 (004 #1, #2): ack lost, effect confirmed by their clips. **1000025 (003 #2) and 1000059 (004 #3): ack lost and no effect at the backend** (clip captured, not sent). |

## D1 table (captured 04:00–15:59Z; completion = backend `timestamp_utc` of the complete row)

| unit | captured (UTC) | media | chunks | complete at (UTC) | min | D1 | note |
|---|---|---|---|---|---|---|---|
| 003 | 04:00 | video | 183/183 | 07:11 | 191 | FAIL |  |
| 003 | 05:00 | video | 182/182 | 08:15 | 195 | FAIL |  |
| 003 | 06:00 | video | 190/190 | 09:15 | 195 | FAIL |  |
| 003 | 07:00 | video | 188/188 | 09:15 | 135 | PASS |  |
| 003 | 07:06 | video | 34/34 | 07:15 | 9 | PASS | trg clip |
| 003 | 08:00 | image | 134/134 | 11:10 | 190 | FAIL |  |
| 003 | 09:00 | video | 124/124 | 11:10 | 130 | PASS |  |
| 003 | 10:00 | image | 131/131 | 13:10 | 190 | FAIL |  |
| 003 | 11:00 | video | 122/122 | 13:10 | 130 | PASS |  |
| 003 | 12:00 | image | 130/130 | 12:16 | 15 | PASS |  |
| 003 | 13:00 | video | 123/124 | — | — | FAIL | 1 still missing at 18:02Z |
| 003 | 14:00 | image | 177/195 | — | — | FAIL | 18 still missing at 18:02Z |
| 003 | 15:00 | video | 118/126 | — | — | FAIL | 8 still missing at 18:02Z |
| 003 | 15:04 | video | 115/123 | — | — | FAIL | trg clip; 8 missing at 18:02Z, its heal (100142) not yet sent; deadline 18:04Z |
| 004 | 04:00 | video | 182/182 | 06:10 | 130 | PASS |  |
| 004 | 05:00 | video | 183/183 | 07:10 | 131 | PASS |  |
| 004 | 06:00 | video | 184/184 | 10:10 | 250 | FAIL |  |
| 004 | 06:06 | image | 25/25 | 09:10 | 183 | FAIL | trg clip; typed image (START lost) |
| 004 | 07:00 | image | 115/115 | 07:11 | 11 | PASS |  |
| 004 | 08:00 | video | 123/123 | 08:10 | 10 | PASS |  |
| 004 | 09:00 | image | 132/132 | 09:10 | 10 | PASS |  |
| 004 | 10:00 | video | 120/120 | 10:10 | 10 | PASS |  |
| 004 | 10:06 | video | 29/29 | 10:40 | 34 | PASS | trg clip |
| 004 | 11:00 | image | 124/124 | 13:10 | 130 | PASS |  |
| 004 | 12:00 | video | 123/123 | 14:10 | 130 | PASS |  |
| 004 | 13:00 | image | 124/124 | 13:10 | 10 | PASS |  |
| 004 | 14:00 | video | 122/122 | 14:10 | 10 | PASS |  |
| 004 | 15:00 | image | 173/173 | 17:05 | 125 | PASS |  |

Totals (power-event clips: none in the window): **SPOT-31593C 12/14, SPOT-33507C 5/14, all 17/28 (61 %).**

### Triggers (G4.6)

| unit | # | id | sent (UTC) | unit ack | fired | clip | result |
|---|---|---|---|---|---|---|---|
| 003 | 1 | 1000019 | 05:21 | 07:06 | 07:06:48 (W10, same boot) | 34/34 msgs | complete in 9 min — PASS |
| 003 | 2 | 1000025 | 09:20 | 11:05 | 11:05:15 | 104 msgs captured, **NOT sent** (`clip needs 136 paced messages, 130 fit in the 169s left`) | FAIL |
| 003 | 3 | 1000030 | 13:21 | 15:04 | 15:04:33 | 123 msgs sent, 115/123 at 18:02Z | FAIL (> 3 h) |
| 004 | 1 | 1000048 | 05:21 | 06:06 | 06:06:56 | 25 msgs, 1/25 first send (queue-full 114 that wake) | complete in 183 min — FAIL |
| 004 | 2 | 1000054 | 09:21 | 10:06 | 10:06:43 | 29/29 | complete in 34 min — PASS |
| 004 | 3 | 1000059 | 13:21 | 14:06 | 14:06:50 | 34 msgs captured, **NOT sent** (`needs 66 paced messages, 61 fit in the 79s left`) | FAIL |

Lag: a trg sent at :20 is heard in the next wake's listen tail (SPOT-31593C) or the one after (SPOT-33507C) and
fires in that same boot with what is left of the 480 s budget.

### Commanded vs captured media (G4.11)

Alternator target for wake H (sent H−1:20): still on even H, video on odd H; 04:00Z had no command (outage) and
05:00Z was sent by hand (video). Captured = the wake's START type (`analysis/media.csv`).

| wake (UTC) | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| commanded for this wake | — | v | s | v | s | v | s | v | s | v | s | v |
| 004 captured (lag 1) | v | v | v | s | v | s | v | s | v | s | v | s |
| 003 captured (lag 2) | v | v | v | v | s | v | s | v | s | v | s | v |

004 at wake H = command for H−1 from 06:00 on; 003 = command for H−2 from 07:00 on (its 05:21 still/trg/cap
arrived only in the 07:00 wake). 0 mismatches beyond the lag. The unit applies `mode.media` at the next boot by design.


Pattern:
- **First-send complete** (no heal): 9–15 min. Every clip of the 126-chunk cap on SPOT-31593C at 08/10/14:00 and all its stills but 11:00/15:00.
- **One heal, SPOT-31593C**: 125–131 min (PASS). Its rsd reaches the Spotter inside the wake → queued → sent next wake.
- **One heal, SPOT-33507C**: 190–195 min (FAIL). Its rsd reaches the Spotter at :10–:12, after the :08 halt → one more wake.
- **Truncated or budget-stopped heal**: 250 min (004 06:00Z, heal > 40 chunks) and > 5 h (003 13:00Z: the 15:00 heal was cut to 7/8 by the trg capture's budget stop).
- **Starving pre-T0 clips** (not D1): 004 01:00Z (43 missing) left out for 9 h, completed 13:10Z (12 h); 004 23:04Z stub clip and 003 00:05Z cut clip still incomplete (newest-first packing never reaches them).

## Phase 2 (video.send.message_cap 190 → 126, sent 05:55Z, Nick's rule)

| | cap 190 (04–06Z video) | cap 126 (from 08Z) |
|---|---|---|
| SPOT-31593C queue-full per video wake | 52 / 66 / 114 | 10 / 15 / 17 / 2 |
| SPOT-33507C queue-full per video wake | 21 / 20 / 5 | 17 / 7 / 13 / 20 |
| SPOT-31593C video clips complete on first send | 0 / 3 | 3 / 4 |
| SPOT-33507C video clips complete on first send | 0 / 3 | 0 / 4 |

Cap 126 removed most of SPOT-31593C's tail loss (its clips now land complete in ~10 min); it did not change
SPOT-33507C (losses there are small, 5–16 chunks, but any loss costs ~190 min). Phase 3 (30-min wakes) was NOT run:
the sessions stopped at 03:22 PDT and the EM then put it on hold (Nick is changing the rig plan).

## How a heal travels (measured from unit cycle logs + console + backend)

capture H:00 → first send H:01–:06 → backend ingests + creates the rsd H:21–:36 (Sofar exposure lag) → Spotter
receives it H+1:05–:12 → unit queues it (`<HL a=requested>`) → sends the chunks **before START at the next wake**
(H+2:01) → backend complete ≈ H+2:10. If the rsd lands after the Pi halted (~:08), add one wake. The halt is
budget-bound (`max_run_time_min 8` → uptime 480 s); heal chunks cost ≈ 1.2 s each from the post-transmit listen
window; a trg in the listen tail fires in the same boot and takes the remaining budget (heals after it are cut).

## Observer gap

All Claude sessions stopped at ~10:22Z (03:22 PDT) until ~17:30Z. The red watcher, the 11:07Z log read and the
planned phase-3 switch did not run. The units, nereus000's console logging and the alternator timer (until its
15:00Z `--until`) kept running; everything after 10:22Z in this report comes from the console logs, the unit cycle
logs and the backend, read after the fact. This is an observer gap, not a unit failure.

## Infrastructure event

03:20–04:25Z: backend remote-config / command sends unavailable (BM_REMOTE_CONFIG + BM_COMMAND_SEND removed from the
staging web service in an env cleanup; Nick restored them). Lost: the 03:20Z alternator run. Heal auto-send was not affected.

## Findings for the release

1. **D1 at 1 wake/h does not meet 3 h on SPOT-33507C** — the two-wake heal path plus a late rsd = ~190 min. Levers,
   no code: 30-min wakes (untested here; expected ≈ 1.6 h), or shorter Sofar latency (not ours). With code: send a
   heal's chunks in the same wake the rsd arrives if budget allows; schedule the rsd so it lands before the halt.
2. **In-flight re-asks**: the backend re-requests chunks queued at the unit until it sees them (7 of 13 SPOT-33507C
   requests). Exclude media with a pending heal until its `<HL a=sent>` + exposure.
3. **Newest-first packing starves** large or old partials (40-chunk cap, truncation costs a full cycle).
4. **Lost acks (F-G3-10)**: 9/32 unit acks never reached the backend; `trg` status cannot fall back on the media
   (`_triggered` did not match 004's two trg clips, one typed `image` because its START was lost).
5. **trg budget defect (release-relevant)**: the trg clip is sized to `affordable_now` after reserving 32 msgs, then the
   send check counts the reserve again and refuses it (`clip NOT sent — budget`); the recording stays on the SD and
   nothing tells the backend. 2 of 6 triggers were lost this way. A trg in the listen tail also cuts that wake's heals.
6. Cap 126 is a clear win on the lossy Spotter (SPOT-31593C); keep it.

## Evidence

`gate.log`, `watch.log` (to 10:14Z), `wakes.csv`, `analysis/windows.csv`, `analysis/media.csv`, `analysis/heals.csv`,
`analysis/d1_heal_2250PDT.md`, `pulled/heal_events_*_final.json`, `pulled/commands_*_final.json`,
`pulled/cycle_logs/bmcam00x/`, `pulled/heal_candidates_*`, `api/P2.*_sofar.json`, `console/`.
Tools: `hil/tools/hil_wake_report.sh`, `hil_media_table.py`, `hil_g4_alternator.py`, `hil_sofar_change.sh`.
