# RESULTS — soak learning: bmcam003/004 run 10/14–10/16 (c_soak_learning, E4)

Spec: PE `SPEC_c_soak_learning.md` rev 2, sha256 `3f6cc98e7b60f2249d9b398089971141021727e05b25b6266fe74a2cf740fe41`
(QE 004 PASS Sat 10/17 2:58 PM; full text in the PR body). Tier (b), read-only desk study.
Session: soak-learning, Sat 10/17 2:55 PM → 3:40 PM PT.
Run folder (data root, not in git): `~/NereusData/bm_cam_legacy/2026-10-16_soak_learning/` (MANIFEST.md, EXPECTATIONS.md,
raw/, logs/, ledger.csv, wakes.csv, asks.csv, q5_*.json/csv). This folder keeps curated excerpts only.

## Read-out (≤ 8 lines, for the PjM)

1. **Q1:** 122 images were captured in the run window. 118 were hourly production images, 3 were stub images from
   Spotter restarts and 1 was a forced cycle. 114 of 122 (93 %) completed ≤ 24 h. bmcam003: 56/62; bmcam004: 58/60.
2. **Q2:** Nick's NOAA bar (100 % ≤ 24 h) is **not met** on either unit. EXPECTATIONS predicted this (X7). Heals
   took longer than designed: median 4.1 h (003) and 3.1 h (004), against ~2 wakes.
3. **Q3:** The 8 late images break down as **C2 ask side: 4** (all bmcam003) and **C7 pool / pull / stub: 4**.
   C4 queue stalls made the holes in 6 of the 8 (secondary). 0 have "insufficient evidence"; 0 are C8 primary. Three
   C8 mechanisms are described below.
4. **Q4:** C2 → d28 packer (`c_healfill`) plus "don't ask while the unit is down". C7 → TE soak procedure and the
   bmcam002 runbook ("no Spotter cfg save / restart right before a wake; wait for GPS"). C3 → d30-B (merged Sat).
   C4 → `post_pool_notecard_queue_stall`.
5. **Q5 (HDR off):** **report only, n = 1 unit**. Only bmcam003 is testable. Its first-send drop rate went **up**,
   7.29 % → 10.24 % (R = −41 %): **FAIL (bar not met)**. Showing a 20 % cut would take 280 wakes per side; 19
   before-wakes exist, so it is **unreachable without HDR back on (Nick's call)**. No recommendation.
6. **First-send loss is 100 % Spotter queue refusals** (`MS_Q_CELLULAR_ONLY is full`, ~10-chunk runs) in all 94
   console-covered production wakes; 0 accepted chunks were lost downstream. bmcam003/SPOT-31593C loses 7.3 % on
   first send, against 1.2 % for bmcam004/SPOT-33507C.

## Method (what was done)

- **A5 lock:** EXPECTATIONS.md was hashed into the MANIFEST at **2026-10-17T22:12:43Z** (sha256 `a20e6e8b…471e06`).
  The first ledger was written at 22:18:30Z. The ledger tool refuses to run unless that hash is in the MANIFEST.
- **Sources** (all read-only):
  - backend admin GETs (media, heal_commands + `<HL>`, config history, gateway heal settings);
  - Sofar `api/sensor-data` for SPOT-31593C / SPOT-33507C (23,935 rows, 10/14 04:00Z → 10/17 22:00Z);
  - the TE's pulled Spotter console logs 10/14–10/16 (SHA256SUMS OK), including the `[BM_TX]` hex dumps of every
    message the Spotter accepted;
  - TE gate/commands logs.
- **Tools:** `tools/soak_learning_fetch.py` (GET-only fetch), `tools/soak_learning_ledger.py` (offline ledger and
  cause classes, `--config` for the next soak), `tools/soak_learning_q5.py` (locked Q5 statistics, stdlib only).
- **A2 clock check:** 5 random rows were checked by hand against raw Sofar and backend: capture time, M, first-send
  count and completion all match. Backend times lag Sofar by 7–31 min (Sofar exposure + poll), as expected.

## Count check (must-do 2, failure mode A1)

| | bmcam003 | bmcam004 |
|---|---|---|
| scheduled production slots in run (59 expected, X1) | 59 | 59 |
| production wakes seen (WS / START / console) | 59 | 59 |
| images in run: Sofar keys / backend rows / matched | 62 / 62 / 62 | 59 / 59 / 59 |
| unit-side keys with no Sofar or backend row | 0 | **1** (`0esrj9`, HDR-off stub, below) |
| **Pi rc_cycle logs** (TE pull 10/17, `2026-10-17_cycle_logs`): image wakes in run / keys missing from the ledger / ledger rows missing from the Pi logs | 62 / 0 / 0 | 60 / 0 / 0 |
| Pi `sent record` msgs vs ledger M | 62/62 equal | 60/60 equal |
| Pi wakes with no `cycle end` (hard-cut) | 2 (the two stubs) | 1 (the HDR-off stub `0esrj9`) |
| backend vs Sofar disagreements (M, completeness) | 0 | 0 |

Every mismatch is explained: the only one is bmcam004's stub image, which the unit sent and the backend never saw.
The unit logs confirm it: `rc_cycle_20261015T160835Z.log` has `[KEY] media key 0esrj9 from Spotter UTC
2026-10-15T16:37:57Z`, 190 msgs, and no cycle end.
The 11 Pi logs that have no image key are all TE bus-hold catches, disarmed before capture (local times match the
gate.log holds). That includes bmcam003's 21:24Z WS-only boot (the 63rd WS row) and bmcam004's Wed 21:44Z restore
stub, so X1's "+1?" resolves to no image.
Reconciliation 100 % (stop line < 80 %: not crossed).

## Q2 — expectations vs observed (EXPECTATIONS.md §3)

| # | Expected | Observed | Met? |
|---|---|---|---|
| X1 | 59 production wakes per unit, plus stubs/forced: 003 = 3 (Wed restore stub, Thu HDR stub, Fri forced), 004 = 2 (+1?) | 59 + 59 production. 003: restore stub `0erasy`, HDR stub `0ess0n`, and a forced cycle Wed 21:15Z (held-bus catch failed during the restore; not anticipated). The Fri forced cycles fell after the run end. 004: HDR stub `0esrj9` (lost); no Wed restore-stub image. | met on production; 1 extra forced image not anticipated |
| X2 | first send often incomplete | complete on first send: 003 7/59 (12 %), 004 10/59 (17 %); first-send drop 003 7.33 % (711/9694 chunks), 004 1.18 % (118/10007) | met |
| X3 | heal lands in ~2 wakes (~130 min) | first ask median 1.1 h after capture; time to complete (images with holes) median 4.1 h / p90 7.1 h (003), 3.1 h / 5.1 h (004) | **not met** (≈ 3–4 wakes) |
| X4 | C2 expected: demand > 24 asks/day | 22.7 / 22.3 asks/day used. Mean **28.1 / 7.5 chunks per ask** against a 40 cap. 4 late images C2 primary (003). See erratum E1 | met (C2 present) |
| X5 | C7 for pool hold, stubs, pull | 4 late images C7 primary: 1 pool hold, 2 pull-truncated, 1 lost stub | met |
| X6 | C1 = 0, C5 = 0, C6 rare, C3 possible | C1 0, C5 0, C6 0 on late images (one `<HL a=refused r=no_payload>` on 31593C, not on a late image), C3 secondary on 1. Asks lost in the dead zone: 5/59 (003), 2/58 (004) | met |
| X7 | NOAA bar not met | 93.4 % (114/122); production only 113/118 (95.8 %) | met (bar not met, as predicted) |

**Errata to EXPECTATIONS (found after the lock; the lock is unchanged):**
- **E1:** X4 says "each ask carries holes of one key only". That is wrong. Asks carried 1.85 / 1.62 keys on average
  (up to 4). The d28 limit is that asks are under-filled (mean 28 / 7.5 chunks of 40), not that they carry one key.
- **E2:** §1 says "nrjxl B3a 1600×900". The unit's `<WS>` reports `rk=1000x562`, and M alternates between ~100 and
  ~195 chunks on bmcam003 at night. Image geometry was not verified here and is not used by any rule.

## Q3 — why: every image not complete ≤ 24 h (8 of 122; full rows in `late_images.csv`)

### C8 mechanisms first (A4). None is a primary cause; each is described with its evidence.
1. **Post-restart GPS gap drops accepted messages.**
   - What happened: the HDR-off `cfg save` restarted SPOT-33507C at 16:37:13Z. bmcam004's stub wake submitted 43
     messages (WS, START, 41 chunks of `0esrj9`); the Spotter accepted and queued all of them.
   - The Spotter's GPS stayed NO_SIGNAL until 16:54:28Z. The Notecard synced "All messages sent successfully" at
     16:55Z, but **0 rows reached Sofar** and the backend never saw the image.
   - Consistent with S6b's "no-GPS silently dropped 3 clips" (Sofar ticket), but **n = 1 here and the cause is
     unconfirmed**: these were also the first messages of a hard-cut stub right after a restart.
   - Evidence: `SPOT-33507C/console_20261015.log` from 16:37Z (BM_TX dumps, `Queuing message 0…`); no
     SPOT-33507C Sofar rows between 16:30Z and 17:00Z.
2. **Asks held while the bus is off are sometimes not delivered.**
   - Asks 100382 (31593C), 100337 and 100343 (33507C) reached the Spotter while the bus was off. None got a unit ack
     or `<HL>` at the next wake.
   - This is separate from the dead zone (`asks.csv`, `dead_zone_why = "rx while bus off"`).
3. **The backend keeps asking when the unit can't answer.**
   - It sent 4 + 5 asks during the Wed pool hold (units in pool mode) and **18 + 7 asks after the Friday pull** (units
     not re-armed).
   - Each ask uses a slot of the 24/day cap, and the stale rsds wait in the Spotter mailbox for the next boot.
   - Evidence: `asks.csv` `sent_at`; d49-C in the TE pre-gate checklist already clears one at the gate boot.

### Late images

| unit | key | captured (UTC) | kind | holes | primary | secondary | short reason |
|---|---|---|---|---|---|---|---|
| 003 | 0eqxwt | 10-14 17:00 | production | 1 | **C7** | C3 | 4 wakes missed (pool hold 18–21Z); then ask 100365 lost in the dead zone (rx 04:05:57Z, after the last submit 04:02:54Z, bus off 04:10Z); completed at 36.0 h |
| 003 | 0erasy | 10-14 21:38 | stub (restore) | 178 | **C2** | C4 | 2 asks × 40 before the deadline for 178 holes; each heal delivered 33 of 40 (queue refusals); no ask for 45 h after 10/15 02:05Z |
| 003 | 0ess0n | 10-15 16:48 | stub (HDR off) | 166 | **C2** | C4 | first ask 15.7 h after capture; 1 ask × 40 for 166 holes |
| 003 | 0ety8u | 10-16 08:00 | production | 33 | **C2** | C4 | never asked in its 11 remaining wakes (first ask 10/17 03:15Z, after the pull); Spotter at 23/24 asks/day |
| 003 | 0eu3t2 | 10-16 10:00 | production | 20 | **C2** | C4 | same pattern: 9 wakes, no ask before the pull |
| 003 | 0eusst | 10-16 19:00 | production | 12 | **C7** | C2, C4 | last wake before the pull; 0 wakes left |
| 004 | 0esrj9 | 10-15 16:37 | stub (HDR off) | 190 | **C7** | C4, C8 | stub cut after 43 submits; the 41 accepted chunks were dropped at Sofar (C8-1); the backend never knew it |
| 004 | 0eussv | 10-16 19:00 | production | 4 | **C7** | C2 | last wake before the pull; 0 wakes left |

Totals: primary C2 4, C7 4. Secondary C4 6, C2 2, C3 1, C8 1. Insufficient evidence 0/8 (bar ≤ 10 %).
**C2 is real product debt on SPOT-31593C:** it ran ~23 asks/day at the 24 cap, mean 28 chunks per ask. Images
older than a few hours stopped being asked once newer images took the asks.

## Q4 — what changes (one line per cause class; counts = late images, primary + secondary)

| Class | Count | Change | Owner card | Before bmcam002 update (10/27)? |
|---|---|---|---|---|
| C2 ask side | 4 + 2 | Fill asks to 40 across keys (d28). Also stop asking a unit that is not waking (pool / pull / disarmed): 34 asks were wasted in this run. | `c_healfill` (nvd #149) + new item on `c_deadzone_b` stale-rsd plan | Not blocking if the d35 soak shows window images ≤ 24 h. bmcam002's 5-h window makes 5 images/day, not 24. Merge per the plan after the soak read-out. |
| C7 pool / pull / stub | 4 | Soak procedure: no Spotter `cfg save` or restart within ~20 min before a wake. Count images whose 24 h runs past the read-out as "censored", not late. Runbook: after any Spotter restart, wait for GPS OK before the next wake. | TE (next soak) + `p_e4_update` runbook | **Yes, runbook line only** (cheap; the 10/27 update restarts things) |
| C3 dead zone | 0 + 1 (asks lost: 5 on 31593C, 2 on 33507C) | Same-id re-send after 2 wakes (d30-B). Verify in the soak: unanswered asks → 0. | `c_deadzone_b` (merged f0be444 Sat 7:20 AM) | Already merged; confirm it is live on staging |
| C4 queue stalls | 0 + 6 | First-send loss is all queue refusals. SPOT-31593C stalls ~6× more than SPOT-33507C. Look at Spotter-specific behaviour (camera swap 10/13 history) and burst pacing around stalls. | `post_pool_notecard_queue_stall` | No (heals repair it when asks are filled) |
| C8-1 GPS gap after restart | 0 + 1 | Add to the Sofar no-GPS ticket; the runbook line above covers it | new card (or the S6b Sofar ticket) | Runbook line yes; the Sofar fix is not ours |
| C8-2 held asks not delivered | 3 asks | Check the mote replay against the late Pi boot (Sprint23 class) | `c_deadzone_b` (same family) | No |
| C1 / C5 / C6 | 0 | none | — | — |

## Q5 — did HDR off cut first-send drops ≥ 20 % (95 %)? (locked rules, EXPECTATIONS §5)

| | bmcam003 / SPOT-31593C (**testable**) | bmcam004 / SPOT-33507C (**not testable**: THERMAL_FAULT covers 100 % of both windows) |
|---|---|---|
| T_off (cfg-save restart) | 2026-10-15T16:47:38Z | 2026-10-15T16:37:13Z |
| windows (D) | [10-14 21:40:24Z, T_off) / [T_off, 10-16 11:54:52Z), 19.12 h | [10-14 21:44:54Z, T_off) / [T_off, 10-16 11:29:32Z), 18.87 h |
| wakes before / after | 19 / 19 (stub excluded; 0.4 % of the after window) | 19 / 19 (sensitivity) |
| HDR adds per wake (console) | 2 before → 0 after | 2 → 0 |
| drop rate before → after | **7.29 % (203/2786) → 10.24 % (307/2997)** | 1.17 % (38/3247) → 0.84 % (28/3334) |
| R observed | **−40.6 %** | +28.2 % |
| bootstrap 95 % lower (6 h blocks, 10 000, seed 0) | −108 % | +0.2 % |
| NB profile 95 % lower (α̂) | −146 % (α̂ = 0.94, overdispersed) | −7.8 % (α̂ ≈ 0, Poisson limit) |
| Poisson exact lower (extra line only) | −64 % | −11 % |
| verdict | **FAIL (bar not met)** | not testable. Sensitivity reading: would be INCONCLUSIVE |
| wakes per side for 80 % power (true 20 % cut) | **280** (19 exist) → unreachable without HDR back on (Nick's call) | 140 (19 exist) → unreachable |

**Overall: report only (n = 1 unit), no recommendation** (R7, TEAM_RULES §9).
- In plain terms: turning HDR off did not measurably reduce first-send loss. On the testable unit loss went up.
- The loss mechanism is the Notecard queue-full stall (~10-chunk runs); HDR doesn't appear to drive it here.
- The windows can't separate HDR from time of day or from the restart itself (EXPECTATIONS §5 confounds).

**Nick's option (one):** count bmcam004 despite its THERMAL_FAULT flag.
- The console raised it 10/14 17:42:55Z. The only later charger reading is a `post` OK on Sat 18:41Z. The TE
  confirms the rig-health THERMAL_FAULT is a stale latch: **fault state unverified for the window**.
- Counting it changes an exclusion after the data exists, so it needs Nick's OK.
- What it would show: bmcam004 INCONCLUSIVE (+28 %, lower bounds +0.2 % and −7.8 %). With two units the worse unit
  decides, so **Q5 = FAIL either way**. The option changes nothing material.

## Study pass / fail (spec "Pass / fail" + R6)

| Item | Status |
|---|---|
| expectations recorded before the ledger (A5) | PASS: lock 22:12:43Z < first ledger 22:18:30Z (MANIFEST) |
| ledger reconciles with backend + unit logs, every mismatch explained | PASS: 100 %, 1 mismatch explained |
| ≥ 90 % of late images have an evidence-backed primary cause | PASS: 8/8, 0 insufficient |
| every expectation row met / not met with numbers | PASS (table above; errata E1, E2) |
| one recommendation per cause with an owner | PASS (Q4 table) |
| soak-card review (re-dated by rev 2: tightenings to the TE for the next soak) | PASS: 8 tightenings sent to the TE + PE (see below) |
| Q5 answered per the locked rules | PASS: report only (n = 1), bmcam003 FAIL |

## Soak-card review (must-do 6)

Reviewed: `runs/soak_noaa_20261017/TEST_CARD.md` (soak **rev 4**) and `GATE_CARD_offwin_heals.md` (gate **rev 5**) at
**92921f76**. The spec named rev 3.2 / rev 3; those were superseded. Line numbers below are from `git show … | cat -n`.

1. **Per-image ledger + cause classes: not required.**
   - The card counts a "miss" as: no backend row, `d1=FAIL` at 24 h, or an un-keyed image (T:58–61, T:176).
   - Cause analysis is one line, "A miss is root-caused" (T:84). There is no class list and no first-send / ask /
     heal-delivery / age fields.
2. **Read-out:** the time is set (Tue 10/20 ~11:30 AM PDT, T:188). The owner is TE2 only by inference (T:4, T:119),
   and no deliverable or recipient is named.
3. **Causes seen here, as pre-registered risks:**
   - C1: the soak bar is `heal_chunks ≤ 40` (T:180), so a wake that sends 0 heals while holding a backlog passes. The
     gate has the stricter min(cap, backlog) bar (G:55).
   - C2: no bar.
   - C3: absent from both cards.
   - C4: report-only (T:186).
   - C5: absent.
   - C6: `r=expired` only.
   - C7: indirect (no-row miss).
   - C8: none.
4. **Pass bar vs Nick's 100 % ≤ 24 h:** matches in substance (T:9, T:175–178). Not defined: stubs, forced cycles,
   images whose 24 h ends after the read-out, and the expected count during console gaps.
5. **Spotter restart / cfg save / GPS NO_SIGNAL mid-run:** no stop rule in either card. The HDR `hrpm 0` change is
   itself a restart.

**Proposed tightenings for the NEXT soak** (to the TE; never retro-fitted; nothing loosened):
1. Add a per-image ledger, written at each 6 h check and at the read-out. Use `tools/soak_learning_ledger.py --config`.
2. Every image not complete ≤ 24 h gets one class, C1–C8, with an evidence pointer. C8 needs a named follow-up owner.
3. Read-out: name the owner, the deliverable (ledger + class table + Q1–Q4) and the recipient.
4. Off-window bar: `heal_chunks = min(cap, pending)` on every off-window wake, not just ≤ 40.
5. New C3 rule: log each ask's Spotter receive time against camera halt and bus-off. Bar: 0 asks lost in the gap,
   0 held asks undelivered at the next wake (d30-B now live).
6. C2/C4 bars, set before the start and no looser than this study:
   - chunks per ask (vs 40);
   - asks per day ≤ 24;
   - **0 asks while the unit is down**;
   - first-send loss % per unit (here 7.3 % / 1.2 %), attributed to queue-full refusals.
7. Expected images = every image the camera captured, stubs and forced cycles included. Keep measuring until the last
   image's 24 h has passed. Keep a second count source for console gaps.
8. Stop rule: any Spotter restart or cfg save mid-run is an event that needs Nick's line. After it, a `post`/GPS check
   within 10 min, and a flag if NO_SIGNAL lasts > 15 min. The affected images stay in the count, classed C7.

The Sat–Mon soak has not started yet: the bench was dark, and plan B starts it Sun night / Mon. Per rev 2, these go to
the next soak. Adopting any of them earlier is the TE's / Nick's call.

## Limitations

- The Pi rc_cycle logs arrived after the first draft (TE pull, 10/17 ~3:45 PM PT). They are used for the count check
  only (`--cycle-log-dir`); the per-image fields still come from Sofar, the backend and the console. Console gaps: 10/14 06:43–16:00Z and 17:43–19:13Z, a nereus000 reboot at
  ~22:02Z 10/14, and SPOT-31593C 16:47–16:52Z 10/15. Wakes in those gaps have no accepted/refused split (blank
  columns).
- Dead-zone timing uses the camera's last console submit as its halt time.
- The Q5 windows are only ~19 h per side (3 blocks), set by the post-pool restore. The bootstrap is coarse by
  construction (27 resamples per side).

## Reproduce

```
python3 tools/soak_learning_fetch.py meta
python3 tools/soak_learning_fetch.py backend --start 2026-10-14T04:00:00Z --end 2026-10-17T22:00:00Z
python3 tools/soak_learning_fetch.py sofar   --start 2026-10-14T04:00:00Z --end 2026-10-17T22:00:00Z --slice-h 3
python3 tools/soak_learning_ledger.py --console-dir ~/NereusData/bm_cam_legacy/2026-10-17_console_logs --cycle-log-dir ~/NereusData/bm_cam_legacy/2026-10-17_cycle_logs --readout 2026-10-17T22:13:00Z
python3 tools/soak_learning_q5.py
```
Next soak: write a `soak_config_<run>.json` like `soak_config_20261014.json` (units, run window, stubs, hold,
heal_since) and pass it with `--config`. Using the 10/14 config reproduces this ledger byte for byte.
