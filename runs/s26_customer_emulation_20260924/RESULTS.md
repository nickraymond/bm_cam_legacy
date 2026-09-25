# Sprint26 customer emulation — bus 10 min on at :00 every hour, video cap 190 — both rigs identical
Window covered: 2026-09-24 19:00–22:10Z (4 wakes per rig). Interim report (Nick: report after a few hours).

Setup (identical on both): bmcam003/SPOT-33507C + bmcam004/SPOT-31593C, S5 code (development @ f21e72d
runtime), `video_tx.message_cap: 190`, 1.3 s/msg, `transmit_phase` off, media_key on, bm_commands on
(tail 150 s), real halt, bridge `sampleIntervalMs 3600000` / `sampleDurationMs 600000` / 5-min alignment,
heal driver on nereus000 (console `bm pub`). Config change order: cron disarmed -> YAML -> bridge commit ->
re-arm + clean halt inside the ~2 min stub window (no hard cut this time).

## Bus windows (console `Bridge bus power`)
Both Spotters: ON 18:59:59 / 19:59:59 / 20:59:59 / 21:59:59, OFF +600 s. **60-min interval aligns to the hour.**

## Per wake
| rig | key (wake) | payload | burst | transmit start / halt (uptime) | backend | notes |
|---|---|---|---|---|---|---|
| 003 | 0dq24v 19:00 | 52,656 B = 183 msgs | 282.1 s, repeat 30/30 | 50.1 / 481.3 s | 183/183 after heal | 8 chunks (94–101) lost to a SPOT-33507C queue stall 19:03:02–11 (mid-burst, not a boundary); healed at the 20:00 wake (cmd 100005, `<HL a=sent n=8>`) |
| 003 | 0dq4wu 20:00 | 52,612 B = 183 | 282.1 s | 50.0 / 481.4 s | 183/183 | carried the 8-chunk heal before START |
| 003 | 0dq7ou 21:00 | 52,443 B = 183 | 282.0 s | 50.6 / 481.4 s | 183/183 | |
| 003 | 0dqah3 22:00 | 52,442 B = 183 | 282.1 s | 56.8 / — (log copied before halt) | not yet ingested | |
| 004 | 0dq24y 19:00 | 53,127 B = 185 | 284.7 s | 52.1 / 481.1 s | 185/185 | |
| 004 | 0dq4wy 20:00 | 52,846 B = 184 | 283.4 s | 50.3 / 481.5 s | 184/184 | |
| 004 | 0dq7ox 21:00 | 52,911 B = 184 | 283.4 s | 51.5 / 481.3 s | 184/184 | 3 rejections at 21:05 (the :05 boundary) — landed in the keyframe repeat, no loss |
| 004 | 0dqagw 22:00 | 53,239 B = 185 | 284.7 s | 51.3 / 481.2 s | 139/185 at 22:08 (still arriving) | |

## Findings
1. **Both rigs behave the same**: transmit start 50–57 s, burst 282–285 s, halt at uptime 481 s every wake
   (the fixed 480 s budget binds), ~2 min before bus off. No Spotter reboots, no hard cuts.
2. **Bitrate up ~1.5x**: ~84–85 kbps (52.4–53.2 KB per 5 s clip) vs ~57 kbps at cap 126.
3. **The :05 crossing is harmless at cap 190** in these 8 wakes: the boundary lands at ~message 191, inside the
   keyframe repeat (SPEC §4 prediction held once: 21:05 rejections cost nothing). Stills have no repeat.
4. Heal path works on the hourly shape: partial at 19:00 -> healed at 20:00 (1 wake).
5. **Backend observation (for the nereus-vision-dev session):** the `<HL>` for cmd 100005 was ingested
   (heal_commands.last_hl_action=sent) but its telemetry row 161352 has `message_type=media_capture` with
   capture/GPS fields merged in; every overnight `<HL>` row was `heal_status`. New since ~16:30Z today.

## Final (overnight) — 2026-09-24 19:00Z to 2026-09-25 ~18:00Z
| | bmcam003 / SPOT-33507C | bmcam004 / SPOT-31593C |
|---|---|---|
| clips at backend | 24 (19:00–18:00), **24 complete** | 23 (19:00–17:00), **23 complete** |
| clip size | 50.1–53.3 KB | 50.0–53.7 KB |
| heals issued / completed | 1 / 1 (8 chunks) | 4 / 4 (2, 8, 1, 3 chunks) |
| wakes from command to complete | 1 | 1 each |
| queue-full rejections | 8 total (one mid-burst stall 19:03) | 42: 2–3 at almost every :05 boundary, + stalls 05:03–05 (11), 10:01 (7) |
| Spotter reboots | 0 (24 hourly self-reset requests ignored: "Reboot limit reached") | 1 (see note) |

- **Every clip ended complete** on both rigs (47/47); 5 heals, all done in one wake; all 5 `<HL>` rows are
  `heal_status` now (the misclassified row from 20:05 reads correctly — presumably fixed by the backend session).
- **The :05 crossing at cap 190 is NOT always free:** 3 of the 4 bmcam004 heals were chunks 176–181 — the last
  unique chunks before the keyframe repeat. The boundary lands at ~message 191 ± a few, so it sometimes hits real
  chunks instead of the repeat. Cheap to heal (1–3 chunks) but real. Input for SPEC §5.3 (pause vs heal).
- SPOT-33507C was much cleaner than SPOT-31593C this run (8 vs 42 rejections) — the reverse of the 15-min run.
- **Note:** SPOT-31593C shows 31 bus-ons at irregular minutes and 1 reboot late in the run — another session
  started using that rig (Nick 2026-09-25); its last hours are not clean emulation data. bmcam004's last clip here
  is 17:00.
- Unit-side timing (halt vs bus off) for the overnight wakes was not pulled: the rigs were handed over first.
  The 4 interim wakes all halted at uptime 481 s (~2 min before bus off).

Hand-over (2026-09-25 ~19:05Z): heal driver STOPPED + disabled on nereus000 (`sudo systemctl enable --now
bm-heal-driver` to restore); console monitor still running. Rigs left as configured: both Spotters bus
10 min/hour (`sampleIntervalMs 3600000`), units armed, `video_tx.message_cap 190`. SPOT-31593C untouched by
this session from here on (in use by another session).
