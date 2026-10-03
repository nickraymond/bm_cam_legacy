# G4 D1 heal analysis at 05:45Z (22:45 PDT) — for the EM, 22:50 PDT deliverable

Sources (all in `pulled/`): `heal_candidates_BMCAM_00x_0538Z.json` (backend `/admin/ingest/devices/<d>/heal-candidates?hours=8`;
the code lists EVERY incomplete media in the window, no list cap → a clip absent from it is complete),
`heal_events_SPOT-xxx_0545Z.json` (`/systems/<spot>/heal-events?hours=8`: heal_request rows + the unit's `<HL>` answers),
`console_heals_0540Z.txt` (nereus000 console: when the Spotter received each rsd), `wakes.csv`.
Clips before 04:00Z are pre-T0 (context, not G4 D1 counts); the mechanism is the same.

## How a heal moves (measured, both units)

| step | when (clip captured at H:00) | evidence |
|---|---|---|
| first send, tail chunks lost | H:01–H:06 | wakes.csv queue_full 13–66/burst |
| backend creates the rsd (after Sofar exposure lag) | H:21–H:36 | heal_request timestamps |
| Spotter receives the rsd (Sofar 35–50 min) | H+1:05–H+1:12 | console `Remote message received` |
| unit acks + QUEUES it (`<HL a=requested>`) | H+1:06 if received while the Pi is up; **H+2:06 if received after the Pi halted (~:08)** | HL requested rows |
| unit SENDS the heal chunks (`<HL a=sent>`) | the wake AFTER `requested`: H+2:06 or **H+3:06** | HL sent rows |
| backend complete (exposure lag again) | ≈ H+2:30 best case, ≈ H+3:30 when the rsd missed the window | candidates list |

So a single clean heal = **≈ 2.5 h best case, ≈ 3.5 h when Sofar delivers the rsd after the Pi halted** (3 of 5
rsds on SPOT-33507C arrived at :11–:12, after the :08 halt). Any heal that is truncated (> 40) or loses chunks
costs another full hour-pair. The 40 cap is not the only limit: **the two-wake pipeline alone puts D1 at the 3 h edge.**

## Per clip (missing at first send → missing at 05:38Z; which rsd)

| unit | clip (key) | captured | first-send missing | rsd (created → Spotter rx → requested → sent) | missing now | status / on track ≤ 3 h? |
|---|---|---|---|---|---|---|
| 003 | 0e5c4w | 01:00Z | 28 (154–181) | 100128 01:31 → rx not in console grep (inferred after the 02:08 halt) → req 03:05 → sent 04:06; 100130 asked it AGAIN (03:36) | 0 | COMPLETE after 04:06 → **≈ 3.1–3.5 h** (no) |
| 003 | 0e5eww | 02:00Z | 13 | 100129 02:31 → rx 03:11 (Pi halted) → req 04:06 → sent 05:06; 100131 asked it AGAIN (04:36) | 0 | COMPLETE after 05:06 → **≈ 3.1–3.5 h** (no) |
| 003 | 0e5hov | 03:00Z | 8 (137–144) | 100130 03:36 → rx 04:11 (Pi halted) → req 05:05 → sends at 06:00 wake | 8 | ≈ 3.3 h expected (no); in 05:38 preview it is LEFT OUT (34+8 > 40) |
| 003 | **0e5kgv** | **04:00Z (G4)** | 21 | 100131 04:36 → rx 05:12 (Pi halted) → req 06:0x → sent 07:0x | 21 | ≈ 3.4 h expected (**no**) |
| 003 | **0e5n8u** | **05:00Z (G4)** | 13 | preview 100132 (not yet created) | 13 | ≥ 2.5 h if the rsd lands before 06:08 |
| 004 | 0e5ewv | 02:00Z | 37 | 100102 02:21 → req 03:06 → sent 04:06 | 0 | COMPLETE after 04:06 → **≈ 2.2–2.5 h** (yes) |
| 004 | 0e5hou | 03:00Z | 43 | 100103 03:21 (truncated to 40: 145–184) → req 04:06 → sent 05:06; tail 185–187 in 100105 (05:25) | 3 | ≈ 4.5 h expected (no: truncation costs a cycle) |
| 004 | **0e5kgu** | **04:00Z (G4)** | 34 (148–181) | 100104 04:21 → rx 05:06 (Pi up) → req 05:06 → sends at 06:00 wake | 33 | ≈ 2.5 h if the 06:00 heal send is clean (**yes, on track**) |
| 004 | **0e5n8t** | **05:00Z (G4)** | 33 | 100105 05:25 → expected req 06:06 → sent 07:06 | 33 | ≈ 2.5 h if rx before 06:08 |
| 004 | 0e5c4v | 01:00Z | 43 | **never in an rsd**: left out every wake (newest first, > 40 with the newer clip) | 43 | **STARVING** (4.6 h, no heal yet) |
| 004 | 0e59cw | 00:00Z image | 50 | 100101 (40) sent 03:06 | 10 | **STARVING** (left out since) |
| 004 | 0e56rl | 23:04Z stub (G4.10 event) | 147+ | 100100 (40) | 147 | power-event clip, starving (not D1) |
| 003 | 0e59kp | 00:05Z cut (G4.10 event) | 138+ | 100127 (40) | 138 | power-event clip, starving (not D1) |

Losses are tail-heavy (chunks ≥ 145 on almost every clip: the end of the burst, where the Spotter queue is fullest).
Also seen: the backend re-asks chunks that are queued at the unit but not yet sent (0e5c4w in 100128 + 100130, 0e5eww in
100129 + 100131): not redundant by G4.4's letter (backend did not hold them), but they spend cap. A heal that is
pending at the unit should be excluded until its `<HL a=sent>` + exposure (Sprint27 heal fix input).

## Does a 100-chunk heal fit the :00–:10 window?

Measured wake→halt 494–505 s (03:00–05:00Z), window 600 s → margin ≈ 95–106 s. At 1.54 s/msg a 100-chunk heal is
+154 s over no heal, ≈ +92 s over today's 40 → **≈ 600 s: no margin, likely cut**. Caveat: wake→halt did not change
between a 13-chunk and a 40-chunk heal (504 vs 505 s), so the halt may be budget-bound rather than send-bound; I will
read the unit's cycle log (read-only) at the 06:00Z wake to time START → END → heal → halt exactly. Either way > 60
chunks/wake needs a longer window (sampleDurationMs) or fewer clip chunks (phase 2's cap 126 frees ≈ 58 msgs ≈ 89 s).

## Read

- Phase 1 heals are NOT clearly improving: 2 clips starving on 004, 003's clean heals land at 3.1–3.5 h.
  → Nick's rule says apply phase 2 (cap 126). It should cut tail loss (fewer chunks lost, fits under 40) but will
  NOT shorten the two-wake pipeline.
- The config-only lever for latency is **phase 3 (30-min wakes)**: the same two-wake pipeline becomes ≈ 1.2–1.8 h.
</content>
</invoke>

## Update 06:05Z — measured from the units' own cycle logs (`pulled/bmcam00x_rc_cycle_wake0500Z.log`)

- **The halt is budget-bound, not send-bound:** `max_run_time_min=8` → halt at uptime 480 s on every cycle
  (+ ~25 s boot = the 494–505 s wake→halt). Order inside the cycle: boot → capture/encode → **heal chunks
  sent BEFORE START** → clip burst (1.54 s/msg: 283 s for 183) → post-transmit command listen window
  (nominal 150 s) **trimmed by the budget** → halt.
- Heal cost: bmcam003 sent 13 heal chunks → listen window 127 s; bmcam004 sent 40 → 95 s. ≈ 1.2 s per heal
  chunk comes out of the listen window.
- **100-chunk heal with today's 184-chunk clips:** +60 × 1.2 ≈ +72 s → listen window ≈ 23 s (commands arriving
  later in the wake wait for the next one). Wake→halt stays ≈ 505 s (budget), so the bus window (600 s) is NOT the
  limit; the 480 s budget and the listen window are. **With phase 2 (cap 126, −57 msgs ≈ −88 s) a 100-chunk heal
  fits and the listen window stays ≈ 110 s.** Whether the transmit itself is cut when it overruns 480 s: not measured.
- **Wasted heal seen:** rsd 100130 (created 03:36Z) re-asks 0e5c4w 154–181, which 100128 already resent at 04:06Z
  (clip complete before 05:38Z). The unit queued it at 05:05Z, so it will resend 28 chunks at 06:00Z that the
  backend already holds. Not G4.4 by the letter (the backend did not hold them when 100130 was created), but it spends cap.
- **Command → effect lag (G4.11):** the 04:25Z media `set`s (1000045/46) were applied in the 05:00 wake's listen
  window as "(next boot)" → they take effect at the 06:00 wake. A :20 send for wake H+1 therefore lands at H+2
  (one wake late): the alternation is consistently phase-shifted by one hour. Measured lag = 1 wake.
