# Sprint11 — Design decisions

Decisions are numbered and dated. Each records the evidence, because every
number here was measured in Sprint10 and should not be re-litigated from taste.

---

**D1 — The transmit is scheduled against an absolute wall clock, not against
cycle-relative time (Nick 2026-07-29).** Blackouts sit on a 5-minute UTC grid
(83 % of gaps within 30 s of a boundary, median offset +1 s). Any scheme phrased
as "wait N seconds after capture" drifts relative to the grid and eventually
lands on it. The scheduler must reason in `epoch mod 300`.

*Clock source:* the cycle already reads Spotter UTC over the BM bus
(`[CMD] spotter UTC decoded: …`). This is what makes the fix **open-loop** — no
USB, no backend, no per-chunk feedback. Field units have none of those.

*Failure mode:* if the time read fails, **fall back to today's unscheduled
behaviour**. A wrong phase is no worse than the status quo; silently trusting a
bad clock would be worse than both.

---

**D2 — Capture-first; the pre-capture listen window is deleted, not shortened
(Nick 2026-07-29).** The 90 s value was picked arbitrarily and is the single
largest cause of boundary collisions: it moves transmit start from ~:01:00 to
~:03:10, so a 194 s burst crosses :05:00 at ~62 % through. Measured first-gap
mean was 65.5 %, which is that arithmetic showing up in the data.

Shortening it would only reduce the collision rate; deleting it removes the
cause. Commands then apply on the **next** boot from cached settings — already
how `win` behaves and consistent with D15 (ack on persist).

*Accepted cost:* a command can no longer affect the *current* cycle's capture.
In the field that was largely illusory anyway — finding 006 shows the mailbox
drain arrives 1–4 min **after** the cycle ends, so same-cycle application only
ever worked on the bench with USB injection.

---

**D3 — Guard bands are sized from the measured blackout distribution, not
guessed.** D: median 9.0 s, 75th pct 16.5 s, 90th pct 24.0 s. Post-boundary
guard **30 s** (covers ~90 % of the tail), pre-boundary guard **20 s**. Usable
lane = 300 − 50 = **250 s**.

This yields the config rule `delay × cap ≤ 250 s`, i.e. at 194 messages only
1.0 s and 1.25 s fit a single lane. That is the concrete argument against
"compromise" pacing values like 1.5 s or 2.0 s — they satisfy neither the
delivery model nor the lane.

---

**D4 — Boot settle cut 30 s → 0.5 s, explicitly marked as the first rollback
candidate (Nick 2026-07-29).** Every pre-transmit second is margin. With the
listen removed, the budget is: power-on → cycle running ~55 s, capture + encode
~5 s, 194 s of transmit → ends ~4 min 14 s in, ~46 s clear of the boundary.
The 30 s settle was a third of that margin.

*The bet:* boot has already settled the Pi, UART and BM bridge by the time cron
runs. **If the next test shows UART open failures, missed time-sync, bridge-not-
ready, or first-message loss, restore 30 s FIRST and re-test before chasing
anything subtler.** Flagged in the script itself, not only here.

---

**D5 — Acks are deferred until the image completes.** An ack is an uplink
message into the same 2-slot cellular queue as the image chunks. Acking
mid-transmit manufactures exactly the collisions the pacing exists to avoid —
incident 001 (silent ack drop) and the soak's "scattered singles = momentary
2-slot collisions (WS + ack)". Queue during the burst, flush after.

---

**D6 — A bounded post-transmit listen tail replaces the pre-capture window.**
Finding 006: the drain is triggered by the sync **our own transmit** initiates
and fires 1–4 min after the cycle ends; bmcam000 took **0/10** commands in the
soak because it was always halted by then. A 150 s tail costs ≈ 0.017 Wh
(~0.5 W) and converts most gap-losses into same-wake applies.

Bounded, not open-ended: the halt is what makes the energy numbers work.

---

**D7 — The bus window is a first-class energy control, and the biggest one
(Nick 2026-07-29).** The bus stays powered for the full window regardless of
when the Pi halts, so the halted-Pi baseline (0.424 W) is **79 %** of a fast
cycle's energy. Measured by integrating the first N minutes of twelve real
on-windows: 20 → 15 min saves 19.7 %; 20 → 10 min saves 39.4 %.

Pacing was never the energy lever. Going 5.0 s → 1.0 s saved 20.4 %; simply
trimming the window 20 → 15 min saves nearly as much and costs **no delivery at
all**, because the Pi is already halted through that span.

*Chosen for the test:* Unit A at **15 on / 15 off** — ~8 min of margin against a
~5–7 min cycle. 10 min is available later once field timing is confirmed.

---

**D8 — Success is measured in COMPLETE IMAGES, never in chunk-delivery percent
(Nick 2026-07-29).** The overnight A/B had the two arms at 95.80 % vs 95.07 %
chunk delivery — statistically indistinguishable — while producing **0/12 and
6/12 complete images**. Progressive JPEG is usable only to its first gap, so a
percentage hides the only outcome that matters. Any future report that leads
with chunk % is answering the wrong question.

---

**D9 — Energy is integrated from the BRIDGE node's addr-65 trace, not the camera
node's.** The camera node's own power log is unusable on SPOT-31593C — 13
in-window samples versus 1444 on SPOT-33507C, because the two Spotters differ in
`transmitAggregations`. The bridge trace reads the downstream camera load,
logged 2225/2226 samples on both units, and drops to exactly 0.000 W in every
bus-off window.

*Validated:* where both sensors exist, bridge 2.156 Wh vs camera sensor
2.259 Wh — **4.8 % agreement**. Use the bridge; cross-check with the camera node
where available. Chain subtraction per the `nereus-spotter-sd-analysis` skill.

---

**D10 — Scope discipline: the sporadic blackout population is explicitly out of
scope.** Phase alignment kills the periodic population only. The sporadic
sync-session events (17 % of gaps, unaligned, 6–36 s) remain, and 1.0 s is
*more* exposed to them than 5.0 s (a 20 s event costs ~18 chunks at 1.0 s vs ~2
at 5.0 s). Arm A's six imperfect images were all this population.

Recorded for the next sprint, deliberately not built now: tail-placement of the
risky window, head-chunk duplication, split-burst with a boundary pause. Fixing
the deterministic 80 % first keeps this sprint verifiable.
