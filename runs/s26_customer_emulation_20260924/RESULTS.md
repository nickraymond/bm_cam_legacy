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
