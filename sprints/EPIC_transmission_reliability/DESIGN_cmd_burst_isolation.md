# DESIGN: keep commands out of the camera's uplink burst (goal 3, remote control)

Status: **DRAFT for Nick's review.** Desk only, no code. Camera session, Wed 2026-10-07.
Scope (EM, 10/7): commands vs the uplink burst. `uplink.start_delay_s` is **out**: R2-DELAY was inconclusive
(`runs/r2_delay_20261006/RESULTS.md`), and Nick's rule was "remote key only if the delay works".
Labels: **[F]** = fact, with its source; **[I]** = our inference, not tested; **[?]** = unknown.

## 1. Question

How do commands (and what they trigger: acks, console replies, `<HL>`, heals, trg captures) and the camera's
uplink burst stop costing each other messages?

## 2. What we know

| # | fact | source |
|---|---|---|
| F1 | The Spotter checks its cloud mailbox **only after a LEGACY report** (hourly report, boot report, health-check alert, bridge-commit network report). A command lands **~72 s after the report**. `note sync` and periodic outbound do not trigger it. | Sprint23 RESULTS; Sprint25 SD re-mine |
| F2 | The report minute R = the post-boot report rounded up to the next 5-min mark, then hourly. **Every Spotter reset re-rolls it.** The health check is boot-anchored too (~:01 to :03 on the bench). | Sprint25 SD; SOFAR_ANSWERS §2 |
| F3 | A report sync rejects camera messages for ~40–50 s. An inbound command adds **36–46 s** more (the Spotter's ack note is `sync:true`). | SD re-mine 10/6, SOFAR_ANSWERS §2 |
| F4 | A command that arrives while the bus is off is queued by the Spotter and released at bus-on. The mote buffer (nereus_cam, `cmdWaitMs` 60 s) delivers it at about +61 s, inside the Pi's boot drain and **before capture**. | mote cmd-buffer test 2026-09-25 (19/19), PR #76 |
| F5 | **Duplicates:** (a) a command that arrives after the Pi subscribes but before `cmdWaitMs` comes in live **and** again from the buffer at ~+62 s; (b) one rsd came in **4× within 150 ms** (probably 4 live subscriptions; the TE is counting `sub` frames). The Pi dedupes by id (`d:1`). | PR #76; #127 |
| F6 | **During the burst, the device already sends nothing extra.** Mid-burst commands are only stashed in the durable inbox and handled at the next decision point (S4 b.7, `make_pending_pump_fn`). The supervisor always defers acks to after END (`make_ack_drain_fn defer`). Heal chunks go out only **before START**. | `rc_command_hooks.py`, `rc_progressive_jpeg.py`, `rc_heal.py` |
| F7 | **The gaps are after END and in stay_on.** After END the unit sends, without waiting: the ack flush (≤ 15 s), `<HL>`, console replies and the acks of the listen tail. A W10 trg fires in the tail, and in stay_on the trg and O5 heal passes fire at once. Fired at a sync, they lost heal chunks 83/84 + `<HL>`, a trg clip 24/25, stay_on heals 26/30 (#126), and acks 9/32 (G4, before the #121 re-send). | #126, #127, EPIC PLAN §3 |
| F8 | #127 (draft) waits 45 s after the **last command** before a trg action or a heal pass. Acks, `<HL>` and console replies are **not** gated, and a duplicate id still restarts the wait. | PR #127 |
| F9 | The backend sees Sofar rows 11–30 min late, so it cannot react to a burst in real time. | Sofar exposure-lag finding, 2026-09-24 |
| F10 | Sofar: the queue is fixed for now. Rejections are invisible to the unit. An ack/nack build is offered (experimental), and `onsp` sets the modem sync period (default 30 min). | SOFAR_ANSWERS §1 |

**What follows from F1 + F4 [I]:** the backend **cannot choose which sync delivers a command**. Whenever it posts, the
command lands at the next report, R + ~72 s. What decides interference is **where R falls against that unit's bus
window**, a per-Spotter fact that every reset re-rolls:
- **R outside the window** (e.g. SPOT-33507C on 10/7: R = :10, Pi halted by :05–:08): the command waits bus-off, arrives
  at boot, and is applied before capture. That is clean, and the command takes effect at the next wake.
- **R inside the window:** the report hold, the Rx check and the +36–46 s command hold all fall inside the window,
  every hour. The device can only stay quiet through them.

## 3. Options

| | option | what it changes | gain | cost / risk |
|---|---|---|---|---|
| A | **#127 as is** | trg + heal pass wait 45 s after a command | fixes the #126 bursts | acks, `<HL>` and console replies still go out into the hold; a duplicate restarts the wait (F5: +60 s) |
| **B** | **Quiet-after-command rule** (#127 widened) | after a **new** command id, nothing but an in-flight main burst goes out for `SYNC_SETTLE_S` 45 s: acks, `<HL>`, console replies, trg, heal pass. Duplicates do **not** restart it. If the settle does not fit before the halt margin, acks fall back to the cloud re-send (#121, existing) and `<HL>` to the next wake. | closes every F7 gap with one rule; small change on top of #127 | tail time: ≤ 45 s per wake that saw a command [I]; slower replies |
| C | **B + pause the main burst** on a new live command | stop chunks for 45 s, resume if the rest still fits the budget (the same rule as `skipped_no_budget`), else send straight through | saves the chunks the +36–46 s command hold would eat [I] | 45 s of the 480 s budget; matters only when R + 72 s falls inside a burst, which may be rare (§5 desk check) |
| D | **Matt's "not ready"** (the Pi tells the mote to hold commands during the burst) | the mote buffers until ready | fewer duplicates | mote firmware change + flashing both motes; the Spotter has already synced and acked (F3), so no queue gain; the Pi then cannot see the sync, so C is impossible. F6 already gives the same deferral. |
| E | **Backend send-timing rule** | hold commands until a safe minute | none, given F1 (one mailbox check an hour) | only useful if `onsp` syncs also check the mailbox **[?]** |
| E′ | **Backend R-vs-window check** (read-only) | per unit, flag "report minute inside bus window" from the report timestamps and the unit's wake + budget | shows which units are exposed every hour; feeds the wake-phase decision (on hold, Nick) | small backend/dashboard item |
| F | **Sofar ack/nack build** | the unit learns about rejections and re-sends | the durable fix for all of this | Sofar timeline; separate test card |

**What changes for heals (with B):**
- An rsd that arrives bus-off is healed before START of the next wake, as today.
- An rsd that arrives mid-burst is stashed. Its `<HL a=requested>` waits for the settle; the chunks go next wake (today: no post-END heals).
- In stay_on, the O5 pass waits out the settle (#127).
- Budget:
  - The settle is charged to the cycle budget like a lane wait.
  - It never runs into `TAIL_SAFETY_S`.
  - The `HEAL_CAP_PER_WAKE` 40 cap and the reserve for the capture's burst are unchanged.
- Backend REASK / `still_arriving` timing: unchanged. That is H6, a separate card.

## 4. Recommendation

1. **B now (MVP).**
   - It is the smallest change that makes "nothing extra into a sync hold" one rule, in one place.
   - It builds on #127's code and tests and needs no wire change.
   - It folds #127 in, so #127 stops being a separate decision.
2. **C only if the desk check says it pays:** in the existing Phase A / SD data, count the hours where R + 72 s fell
   inside a burst. If it is < 1 in 24 per unit, drop C.
3. **E′ (cheap, read-only), not E.** Re-check E after the `onsp` test card shows whether `onsp` syncs deliver commands.
4. **D no.** Answer Matt's duplicate question with F5's counts, and say yes to F (already Nick's follow-up).

## 5. How we'd test B (test card, per EM_HANDBOOK §3)

- **Question:** do acks, `<HL>`, heal chunks and trg chunks sent after a live command still arrive when they wait out the
  45 s settle, without costing the main burst?
- **Conditions:**
  - bmcam003 / SPOT-33507C in **stay_on**, so the Pi hears every report-sync command live; nereus000 holds this bus only.
  - Pacing, framing, `onsp`/`smrr` and lights held fixed. Backend self_heal OFF.
  - Each hour, one cloud command lands at R + 72 s: an rsd for 10 chunks of a stored media, plus one `get`. This
    **needs Nick's OK**: a cloud command to a bench Spotter, never a field unit.
  - A = development + #127 as is; B = rule B. Alternate A/B by hour.
- **n:** 6 hours per arm (12 h). #126 showed a large effect (≈ 13 % of heal chunks survived), so 6 is enough for a large
  difference. It is not enough for a small one.
- **Metric (per hour):** the fraction of the post-command messages (acks + `<HL>` + heal chunks) that reach the backend.
  Cross-check against the console's `MS_Q_CELLULAR_ONLY is full` lines.
- **Pass bar (pre-registered):** B ≥ 95 % in ≥ 5 of 6 hours **and** B's median ≥ A's median + 20 pp. Also, main-burst
  loss in B is not worse than in A (Mann–Whitney, p ≥ 0.1 counts as "not worse").
- **Stop:**
  - Answered: after 3 hours per arm, if A ≤ 30 % in all 3 and B ≥ 95 % in all 3.
  - Broken: a Spotter reset (it re-rolls R), a console capture gap, or the bus dropping. Stop and fix.
  - Power: one bus held on at a time.
- **Cost:**
  - ½ day of code + tests (golden re-record, timing only, as in #127).
  - 12 h of bmcam003 + nereus000. Runs after R3-PACE frees the unit.
- **Worth it?** Yes. It removes a loss class that M5/M6 count today (acks/status lost, command latency), at near-zero
  wire risk. It does not touch the per_boot main burst, which C1 found is not the main problem.

## 6. Open items

- [?] Do `onsp` syncs check the mailbox? (This decides E. Ask Sofar, or watch the console during the `onsp` card.)
- [?] Is the +36–46 s hold per command, or per sync with any commands in it? (Batching commands would help only if it
  is per command.)
- Mote duplicate root cause (F5b): the TE's `sub` frame count.
