# Sprint26 SPEC (DRAFT v0) — bus-window-aware, media-agnostic transmit

Status: **DRAFT v0 for Nick's review. No code.** Written 2026-09-24 from the S5 session's analysis.
Owner / approval gate: Nick. Every number below is measured (with its source) or marked ASSUMPTION.
Open research for a separate session: §7 (does the Ebox broadcast the bus-on duration / a turn-off warning?).

## 1. Why

The field customer runs the BM bus **10 min on at the top of every hour, 50 min off**, and is energy-sensitive.
Today each cycle sizes its payload against a **fixed** time budget (`progressive_jpeg.max_run_time_min`,
8 min = 480 s, counted from `main()`), not against the time the bus will actually stay on. Stills and
video size themselves with two different mechanisms. Nick's goals:

- The camera knows **how much bus time it has** and plans the whole wake (capture, encode, heals,
  burst, listen tail, halt) inside it — never overruns, never wastes it.
- **Stills and video use the same mechanism**; the media type only changes the encoder.
- Raise the video message cap toward **~190** where the window allows.

## 2. What exists today (code review, bm_cam_legacy `development` @ f21e72d)

| Piece | Where | What it does |
|---|---|---|
| `CycleBudget(budget_seconds, seconds_per_message, clock)` | `rc_time_budget.py` | elapsed/remaining vs a fixed budget; `messages_fit(n)`, `max_messages_now()` |
| budget source | `rc_progressive_jpeg.resolve_rc_settings` | `budget_seconds = max_run_time_min * 60` (YAML; 8 min on the bench units) |
| stills sizing | `rc_quality_selector` | walk the JPEG quality ladder down until `message_count <= message_cap` AND `budget.messages_fit(count + 2)`; floor still too big -> bounded partial send + `<WS a=inc>` |
| video sizing | `rc_video_tx` + `rc_video_clip.fit_clip_to_budget` | `budget_msgs = min(video_tx.message_cap, max_messages_now() - (2 + keyframe_repeat_max))`; x264 2-pass into that size |
| lane planner | `rc_transmit_phase` (island `transmit_phase`, off on bmcam003/004) | waits so the whole burst lands in one clean 250 s lane of the 300 s UTC grid (post-guard 30 s, pre-guard 20 s); `burst_exceeds_lane` -> go now; wait skipped if `budget.has_time_for(wait + burst)` fails |
| heal slot (S5) | `rc_heal` | <= 40 heal chunks before START, reserving the capture's whole burst |
| listen tail | `rc_command_hooks.post_transmit_listen` | 150 s after END, trimmed to `remaining - 20 s` |
| halt | `rc_power_halt` | real halt at cycle end |

So the "how many messages can I afford" question is already shared (`CycleBudget`); what is missing:
(a) the budget is not the **bus window**; (b) the sizing call is two code paths; (c) boundaries are
only considered at burst START (lane planner), never inside the burst.

## 3. Measured timings (bmcam003/004, video_tx, 1.3 s/msg; `runs/s3_bench_20260923T2325Z`, `runs/s5_ladder3_overnight_20260924`)

| Segment | Time |
|---|---|
| bus on -> `main()` (boot + `py_compile`) | ~21 s uptime |
| `[CMD] subscribed` | ~21.4 s |
| Spotter UTC read | ~23–30 s |
| transmit start (video: record 7 s + fit ~10 s) | ~49–55 s |
| 126-chunk clip burst (126 + ~26 keyframe repeat + START/END) | ~190–200 s |
| listen tail | 150 s |
| halt | ~396–409 s uptime |

Spotter-side loss causes seen overnight (S5 ladder 3): queue-full stalls (SPOT-33507C: ~47 s every
~3 h 15 m; SPOT-31593C shorter, irregular) and the 5-min boundary stall. A stall inside the first ~26
chunks is healed for free by the keyframe repeat.

## 4. The math for the customer schedule (10 min on at :00)

Pacing 1.3 s/msg; transmit start ≈ :00:52 (measured ~52 s after bus on).

| Case | Burst msgs | Burst s | Window | Planner |
|---|---|---|---|---|
| today, cap 126 | 126 + 26 + 2 = 154 | 200 | :00:52–:04:12, inside lane (:00:30–:04:40) | no wait |
| cap 190 | 190 + 30 + 2 = 222 | 289 | :00:52–:05:41, crosses :05 | `burst_exceeds_lane` -> go now, no wait |
| burst 228–250 s | — | — | would fit a lane only from :05:30 | would wait ~278 s — **but** `has_time_for(wait+burst)` fails with the 480 s budget (449 s left at transmit start) -> wait skipped |

Conclusions:
1. With the bus anchored at :00 the transmit start is fixed by boot (~:00:52), so the planner is
   effectively a no-op for this customer, and its budget guard prevents it from overrunning the window.
2. At cap 190 the burst crosses :05. Coincidence: the boundary lands at ~message 191 = the start of the
   keyframe repeat, whose loss is harmless. Stills have no repeat -> real chunk loss there.
3. **Capacity:** bus off at :10:00; halt by ~:09:30 (ASSUMPTION: 30 s margin) -> from :00:52 there are
   ~518 s for burst + tail -> ~368 s burst with a 150 s tail -> **~283 msgs at 1.3 s**. 190 fits easily.
   Today's fixed 480 s budget leaves ~100 s of the window unused.
4. Energy (ASSUMPTION: Pi ≈ 0.5 W, from the bm_commands island comment; measure with the Spotter SD
   power logs): a cycle ≈ 400 s ≈ 0.056 Wh; the 150 s listen tail ≈ 0.02 Wh of it — the largest item
   we control. A lane wait of ~278 s would add ~0.04 Wh.

## 5. Proposed design

### 5.1 Deadline, not a fixed budget
```
bus_on   ≈ now_utc − /proc/uptime − boot_offset        (boot_offset measured, ~0–2 s; ASSUMPTION)
deadline = bus_on + bus_window_s − halt_margin_s
```
`bus_window_s`: YAML (600 for the customer) until §7 gives a live source. `halt_margin_s`: YAML
(ASSUMPTION 30 s). The cycle's `CycleBudget` becomes `remaining = deadline − now`. Keep
`max_run_time_min` as a hard ceiling for backward compatibility (min of the two).

### 5.2 One message budget, one sizing call
```
budget_msgs = floor((deadline − now − listen_tail_s − heal_slot_s) / pacing) − envelope − repeat
payload     = sizer.fit(budget_msgs)       # JPEG: quality ladder; video: x264 2-pass
```
The transmit loop, heal slot, `<HL>`, acks, tail and halt are shared and media-agnostic. `message_cap`
stays a per-product ceiling (video 190 target, stills 195 field-tested).

### 5.3 Boundaries inside the burst — Nick to choose
- **(A) pause across the blackout:** stop at :x4:40, resume at :x5:30 (~50 s idle per crossing; the
  pause is charged to the budget). No boundary loss; costs Pi-on energy.
- **(B) accept + heal:** send straight through; boundary losses become heal work (cellular messages)
  on a later wake.
- (C) today's planner (whole-burst lane fit) is kept only as an option; it does nothing for the
  hour-anchored schedule.

### 5.4 Listen tail
Until the mote-side command handoff lands (Matt / Sofar), the tail is how commands are heard. After
it lands, the tail can shrink or go (S7 is gated on that patch).

## 6. Test: customer emulation (before any A/B)

Both rigs **identical**: bus 10 min on / hour (`sampleIntervalMs 3600000`, `sampleDurationMs 600000`),
video `message_cap: 190`, `transmit_phase` off, S5 heals via `bm_heal_driver` (console path), 14-day
retention. Measure per wake: bus-on / START / END / halt vs bus-off (`[BOOT]` marks + console),
boundary losses, heals per clip, the two rigs side by side. Open check: whether a 60-min interval with
`alignmentInterval5Min: 1` starts at :00 or at any 5-min boundary.

## 7. Open research — separate session (Nick 2026-09-24)

Nick believes Sofar's recent Ebox/bridge firmware **broadcasts how long the BM bus will stay on and
warns before it turns off**. To confirm and specify:
- Which topic(s)/message(s), payload format, cadence; firmware version that introduced it; whether
  SPOT-33507C / SPOT-31593C (Spotter v2.16.8, bridge FW 0.13.11) have it.
- If it exists: the Pi subscribes (the command daemon already owns the UART and subscribes to topics),
  and `deadline` in §5.1 comes from the broadcast instead of YAML; a turn-off warning becomes a hard
  "finish the current message, send END, halt" signal.
- If it does not: §5.1's uptime anchor + YAML window is the MVP.

## 8. Decisions needed (Nick)
1. §5.3 boundary policy: A (pause) or B (heal). **DEFERRED (Nick 2026-09-25):** decide after the code
   refactor lands AND another test round with Matt's integrated mote command code (two development
   sprints now in flight). Evidence so far: `runs/s26_customer_emulation_20260924/RESULTS.md` — at cap 190
   the :05 crossing usually lands in the keyframe repeat but hit real chunks 176–181 in 3 of 23 bmcam004
   wakes (1–3 chunks each, all healed in one wake).
2. `halt_margin_s` and whether `max_run_time_min` stays as a ceiling.
3. Video `message_cap` target (190?) and whether stills keep 195.

## 9. Not in scope
Mote-side command cache (Sofar), backend auto-heal (M5), post-END heals, the backend ingest-miss bug
(separate nereus-vision-dev session).
