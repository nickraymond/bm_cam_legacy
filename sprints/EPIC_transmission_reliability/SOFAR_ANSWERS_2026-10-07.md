# Sofar answers on Spotter queueing (Matt, received 2026-10-07) and what they mean for us

Purpose: keep Sofar's answers to our 10/5 questions (queue limits, sync cadence, Spotter send schedule,
reject feedback) next to the transmission-reliability epic, with what each one changes in our plan.
Source: Matt's email reply to Nick, pasted into the EM session on Wed 2026-10-07 ~12:50 PM PDT.
Labels: **Sofar** = stated by Matt; **measured** = our bench/field data (sources listed); **inferred** = our reading, not tested.

## 1. What Sofar said

| our question | Sofar's answer (Matt) |
|---|---|
| Duplicated command deliveries | Asked us back: does it happen consistently, or only in the first ~60 s window? Suggested publishing a "not ready" message to the mote so it only offloads messages to the Pi when the Pi is ready. |
| Can the sync interval, the 30-min outbound, or the queue size change? | **Sync interval: yes**, the Spotter config **`onsp`** sets how often the cellular modem syncs with the backend. Default **30 min**, minimum **1**. The queue size and the other items are **fixed for now**. Sofar (Matt + Victor) is working on making queueing expandable/unlimited, and an **ack/nack scheme** so the mote knows when it can send more; Matt may be able to provide an **experimental Spotter build**. Meanwhile: add a delay between sequential messages (~100 ms, conservative). |
| Exact schedule of the Spotter's own sends, so we can avoid them | Wave messages sync every **30 min or 1 h** depending on the Spotter config **`smrr`** (0 = 30 min, 1 = 1 h). Otherwise use `onsp` (above) if on cellular. |
| Can we tell when a message was rejected? | **Not currently.** The ack/nack scheme above would provide it. |

## 2. What we had measured before the reply (for context)

| finding | numbers | source |
|---|---|---|
| Spotter's own HDR message | ~6.1 KB every 300 s at the 5-min mark (±2 s); at the report mark it is queued in the same ms as the 50 B report → sync | SD re-mine 2026-10-06 (3 Spotters), EM scratchpad `sd_remine/out/stats.json` |
| Sync hold length | set by the Notecard model: WBNA-500 ~25 s, WBGLW ~45 s (bench and reef buoy alike); an inbound command adds 36–46 s (ack note `sync:true`) | same |
| Where rejects happen | 7–89 % of arrivals inside sync windows vs 0.7–1.0 % elsewhere | same |
| Report minute | post-boot report's GPS time rounded up to the next 5-min mark; every reset re-rolls it (19/19 boots) | same; Sprint25 RESULTS §2 |
| Reef buoy (SPOT-33361C) | 85 % loss in the 60 s after the SAMI pH dev kit's message at ~bus-on +175 s vs 0.09 % elsewhere (45 bursts, 8/31–9/9) | EM scratchpad `field_bmcam001/run_20261007T0403Z` |
| Bench "no visible trigger" stalls | 3 of 4 no-delay wakes on SPOT-33507C lost 10–13 % in a Notecard hand-off stall at ~:01:10–:01:45 with no Spotter event in the console | `runs/r2_delay_20261006/RESULTS.md` |
| A fixed start delay | 230 s moved the burst onto the :05 HDR or the boot-anchored health check: medians 11.5 % (no delay, n=4) vs 9.3 % (230 s, n=4), not significant | same (R2-DELAY, inconclusive) |

## 3. Implications

1. **`onsp` is the one new lever** (Sofar). *Inferred:* the unexplained ~:01:10 hand-off stalls on the bench
   may be the Notecard's own periodic sync. If so, `onsp` moves or reshapes them. Not tested.
   `onsp` is a Spotter console setting, so it can be set remotely through the Sofar Command API
   (the same path as the REMOTE-RESET test, `tools/sofar_send_command.py --raw-message`).
2. **Keep `smrr` = 1 (hourly).** Moving to 30 min doubles the report syncs that hold the queue (inferred from §2).
3. **The 2-slot queue is fixed for now (Sofar).** Timing tricks only move the burst between hazards
   (R2-DELAY), and slower pacing trades shorter per-stall loss for longer bursts (R3-PACE, in progress).
   The durable fixes are Sofar-side: **ack/nack + an expandable queue** (experimental build offered) and
   Sofar's **file transfer protocol** (expected end of 2026).
4. **The ~100 ms spacing advice** is already exceeded: we pace at 1.0–1.5 s per message.
5. **Rejections stay invisible to the unit** (Sofar) until ack/nack exists. Only the Spotter console shows
   `MS_Q_CELLULAR_ONLY is full`, so loss accounting keeps relying on nereus000 console captures and backend heals.
6. **Matt's "not ready" idea** for duplicated deliveries fits our logged design item: no command handling or
   replies during the camera burst (board card, after R2-L1).

## 4. Follow-ups

| item | owner | status |
|---|---|---|
| Reply to Matt: yes to the experimental ack/nack build; answer his duplication question with our counts (4 identical deliveries ~50 ms apart, plus the 60 s replay; #127 notes) | Nick (EM drafts) | open |
| Read the current `onsp` / `smrr` on both bench Spotters (console read; needs Nick's OK for console commands) | TE | open |
| Test card: `onsp` 30 vs e.g. 5 min on bmcam003. Do the in-burst stalls move or shrink? (one variable, after R3-PACE) | EM → TE, Nick approves | proposed |
| REMOTE-RESET: does a cloud-sent `reset` reboot the Spotter? Sent 2026-10-07 1:08 PM PDT to SPOT-31593C (HTTP 202) | EM + TE | running |
