# EPIC: Message transmission reliability (draft r1, for Nick's review)

Owner: Nick. Coordinator: EM. Bench: Test Engineer. Started 2026-10-05.
Status: **DRAFT**. Nothing in this epic runs until Nick approves the plan.

## 1. Why

Every user-visible delay comes from the same chain: camera → BM bus → Spotter cellular queue → Notecard →
Sofar → backend. Today a single still that loses 4 of 190 chunks took **3 h 32 m** to render
(media 57389, 2026-10-05). The fixes so far were one-offs (pacing, heal cap, ack re-send, a sync settle).
Nick (2026-10-05): stop fixing one symptom at a time; understand the whole pipeline and drive the failure rate
as low as we can. #127 (sync settle) is decided inside this epic, not alone.

## 2. Goal and metrics (measured per wake, per clip, per command)

| metric | definition | today (source) | target (proposal) |
|---|---|---|---|
| M1 first-send complete | media complete with zero heals | 3/5 per arm (C1), 61 % / 17 % per unit (G1) | ≥ 90 % |
| M2 chunk loss at first send | lost / sent, per media | 0–16 chunks of 120–195 (G4, R1G) | ≤ 1 % median, ≤ 3 % p90 |
| M3 time to complete (D1) | capture → complete at backend | p50 ~130 min, 33507C ~190 min, first nrjxl 212 min | p90 ≤ 60 min |
| M4 queue-full events | Spotter "MS_Q_CELLULAR_ONLY is full" per wake | 0–36 (C1), 10–17 video (G4 cap 126) | ≈ 0 |
| M5 command latency | backend send → unit applies | 1 wake (31593C), 2 wakes (33507C) | same wake |
| M6 acks / status lost | at the Spotter | 9/32 before #121, ~0 after (R1G) | 0 (stay_on included) |
| M7 energy per delivered kB | Spotter bus + Pi per wake / kB delivered | not measured | measure, then don't regress |

## 3. What we know (facts, with sources)

- **Spotter cellular queue holds 2 messages** (cellular-only lane). Bursts that arrive while it is full are dropped
  silently (`MS_Q_CELLULAR_ONLY is full`). Sprint23/25 notes, G4, #126 trace.
- **The Spotter syncs (hub.sync) only at its hourly LEGACY report** (+ boot); the Notecard is in **periodic outbound
  mode, 30 min**. Inbound commands arrive only at the sync; outbound messages wait up to ~30 min to leave
  (first nrjxl: burst 06:12, last chunk at backend 06:40). Sprint25 + 2026-10-04 measurements.
- **For ~40–50 s after a report/sync the queue rejects camera messages.** Anything fired then loses chunks:
  stay_on heals (4/30 chunks survived), W10 trg captures (G4 trg #1: 1/25), immediate acks (G4: 9/32 lost). #126.
- **Moving the burst away from the report minute made no significant difference** to queue loss (C1 phase 1,
  :00 vs :30, 5 + 5 wakes, 2026-10-05). The main per_boot burst is not the problem; events at the sync are.
- **Pacing:** 1.3 s/msg is validated (Sprint25); 1.54 s/msg measured outdoors. 288 B data per chunk (384 chars).
- **Message size:** 300 vs 384 chars made no difference (Sprint10). 1200 B per message is possible with the
  cellular-only flag but untested (C1 phase 2, needs a wire change).
- **Heal path:** rsd at most 1 per wake, ≤ 40 chunks, newest-first; REASK_S 5400 s; still_arriving guard 15 min;
  heal cap 24/day per Spotter. Each partial heal costs one more re-ask cycle (90 min + sync alignment).
- **Losses are mostly small and scattered** (2–16 chunks per clip), plus occasional bursts (sync collisions,
  Spotter resets, the SPOT-31593C memory fault / no-GPS drops).
- **The rig shares one 5 V adapter** (nereus000 + both Spotters' charging), marginal during bus windows.

## 4. Hypotheses to test (one variable at a time, bench first)

| # | lever | hypothesis | how we test | cost |
|---|---|---|---|---|
| H1 | **Sync settle** (#127) | not sending in the 45 s after a sync removes the collision losses | A/B on bmcam003: trg + heals with/without settle, 12 wakes each | ½ day code (done, draft) |
| H2 | **Notecard sync cadence** | the periodic 30 min outbound + sync-at-report is the biggest latency term | read the Spotter/Notecard config; ask Sofar what's configurable (outbound period, sync on queue level) | Sofar dependency |
| H3 | **Message size** (C1 phase 2) | the queue drains per message, so bigger messages move more bytes per drain | step test 288 / 600 / 900 / 1200 B, same bytes, 6 wakes each | wire change + Nick's OK |
| H4 | **Burst shape** | short bursts with gaps (e.g. 2 msgs, then pause for the drain) beat a steady 1.3 s pace | pacing sweep on one unit, with queue-full and drain timing from the console | camera config only |
| H5 | **Forward error correction** | +10 % parity chunks make most media complete on first send, with no heal | desk sim on the real loss traces (study running), then bench | wire change |
| H6 | **Heal timing** | shorter REASK and same-wake heals cut D1 by hours | backend config + camera heal-at-sync-settle | backend config |
| H7 | **Early preview** | a small redundant preview gives users something on screen while heals finish | desk study running (progressive / redundant / split) | wire change |
| H8 | **stay_on gaps** | the ack re-send on a timer (#128) and the duplicate subscriptions fix close stay_on losses | bench in stay_on | camera + mote (Matt) |

## 5. Plan (proposal)

**Phase A, measure the pipeline end to end (2–3 days, no code):**
instrument one unit (bmcam004) for 24 wakes. Per message: Pi send time → console queue accept/reject → Sofar
receive time → backend ingest time. Build the timeline of where messages wait or drop. Output: a loss and latency
budget per stage (M1–M6) and a ranked list of the levers that matter.

**Phase B, desk work in parallel:** finish the FEC / preview study on the real traces (H5, H7);
ask Sofar about the Notecard sync and outbound options (H2, Nick); read the mote subscription behaviour with Matt (H8).

**Phase C, experiments, one lever at a time, ranked by Phase A:** probably H1 → H4 → H3 → H6, each an A/B
with a pre-registered pass bar (like C1). bmcam004 = transmission rig; bmcam003 stays the production-like reference.

**Phase D, combine the winners into a release** (wire changes batched into one contract revision for Nick to sign),
then a 24 h outdoor confirmation on both units.

## 6. Links to the JPEG-XL epic

The JPEG-XL epic needs this one: outdoor stills need more bytes (daylight foliage needed d 9.7 to fit 195 msgs vs
d 4.3 indoors), so the useful message budget depends on how many messages we can move per wake reliably.
The budget decision for nrjxl waits for Phase A numbers plus the TG-7 scene study.

## 7. Decisions for Nick

1. Approve this plan and its metric targets (M1–M7)?
2. Who asks Sofar about Notecard sync / outbound options (H2)?
3. Who talks to Matt about subscription dedupe in the mote firmware (H8)?
4. #127: held until Phase A/C ranks it (Nick, 2026-10-05: not decided in isolation). R1 ships without it.
