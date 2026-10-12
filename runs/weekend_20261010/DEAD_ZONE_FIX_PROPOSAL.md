# Dead-zone fix proposal (TE2, Mon 10/12, from the weekend console record)

Weekend follow-up 2. This is a proposal, and nothing is deployed. Evidence is in `wakes.csv`, `console/` and `pulled/` in this folder.

## The problem, measured

**What the dead zone is:** the gap between the Pi's halt and bus-off. In it the bus is still on, so the Spotter forwards a
mailbox item at once rather than holding it, but no Pi is listening, so the item is lost.

- **Halt-to-bus-off gap, n = 57 wakes per Spotter:**
  - SPOT-31593C: median 105 s, range 94–265 s.
  - SPOT-33507C: median 102 s, range 94–159 s.
- **Only SPOT-31593C is hit in practice.** Its boot-anchored health-check sync (~:07) delivers into that gap. SPOT-33507C's syncs fall at
  ~:02:45 (the Pi is awake) and :10 (after bus-off, so the item is held and applied at the next boot).
- **Every console receipt over the weekend, classified by the Pi's state** (receipts in the console wake excerpts, Fri 3 PM → Mon 12 AM):

| Spotter | Receipts in the dead zone | Applied | Lost |
|---|---|---|---|
| SPOT-31593C (bmcam004) | **8** (7 rsd heal asks + cmd 1000079) | 0 | **8** |
| SPOT-33507C (bmcam003) | 0 | – | 0 |

  - Lost on SPOT-31593C: rsd 100255, 100265, 100266, 100268, 100269, 100274 and 100275, plus 1000079.
  - 1000079 was recovered only by the timed re-send.
  - All held receipts (after bus-off) on both Spotters were applied at the next boot. 100259, 1000169 and 100260 are confirmed applied in the backfilled full logs.
  - Re-checked against the complete cycle logs: every wake on both units has a full log with the halt line (`pulled/<host>/backfill/`). The 7 rsds are absent from all of them, so they were really lost.
- **This dead zone is also why media 58313 starved** (bmcam004, 12 missing for 29 h). The backend did ask for its heals, and the asks were lost here.

## Options

| | Change | Closes | Cost / risk |
|---|---|---|---|
| **A (recommended, unit)** | Make the post-transmit listen tail end at **bus-off − margin** instead of a fixed 150 s. The Pi boots ~5 s after bus-on and the bus window is 600 s, so tail_end = boot + 600 − 5 − margin. Keep `TAIL_SAFETY_S` for the halt. | All but the last ~margin seconds (≈ 95 % of the 94–265 s gap at a 20–30 s margin) | Pi on +94–265 s per hour (median ~100 s). **Energy cost not measured.** Measure it from the node current on the rig before deciding. Today the tail is clamped to the 480 s cycle budget (`rc_command_hooks.py` listen tail), so the budget or a separate tail deadline has to change. Needs the bus window in config, which it already is in the supervisor/bridge schedule. |
| **B (backend)** | Auto re-send with the same id when there's no ack within 2 wakes. The unit already de-dups by id (`dup=` counter; the 1000079 re-send applied once). | Every loss cause (dead zone, queue stalls, empty mailbox), not only this one | A heal/command arrives a wake or two later. No unit change. |
| **C (send timing, today's workaround)** | Send bmcam004 commands at ~:08:30 so they ride the :15 report sync after bus-off | Commands only; 3/3 this weekend | Heal asks (rsd) are sent by the backend on its own schedule, so they're **not** covered. That's why 7 of the 8 losses were rsds. |
| D (mote) | Mote persists buffered commands to flash and replays at the next boot | Dead zone | Firmware change (PR #76 lineage). Future. |

**Recommendation: A + B.**
- A removes the cause on the unit.
- B is a cheap backstop that also covers the queue-stall and empty-mailbox losses seen in JXL-RC.
- C stays as the operating rule until A ships.

## Test before recommending A (pre-registered)

- **Unit:** bmcam004 / SPOT-31593C on the bench. Its health check delivers at ~:07, which is where the dead zone bites.
- **Condition:** the A build vs the current build, alternating by day. 12 wakes per arm, minimum.
- **Load:** the backend's normal rsd flow, plus 1 contrast command per 3 wakes, sent at ~:04 so it arrives at the ~:07 health-check sync.
- **Measures, per receipt:**
  - Pi state at receipt (console vs the cycle log), and whether it was applied (`[CMD] applied id=`).
  - Halt-to-bus-off gap.
  - Node current at bus-off: no hard cut allowed.
  - Wake energy from the rig's node current.
- **Pass bars:**
  - A arm: 0 receipts lost in the gap (current arm: expect ≥ 1 per ~7 wakes, as on the weekend).
  - 0 hard cuts.
  - Halt ≥ 20 s before bus-off on every wake.
  - Energy increase reported, and accepted by Nick before any field use.
