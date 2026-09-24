# S5 ladder 3 (indoor, overnight) — both rigs, console-driven heals — 2026-09-24T05:00Z – 16:30Z

Setup: bmcam003 (SPOT-33507C) + bmcam004 (SPOT-31593C), identical S5 runtime (34 files byte-identical,
`feature/s5-rsd-heal`), identical YAML values (video_tx, 1.3 s/msg, media_key on, bm_commands on, real halt,
`transmit_phase` OFF), identical bridge configs (840000/600000 ms, 5-min alignment, power controller ON),
cron armed. Dark room -> dark clips; the encoder still fills ~120 msgs (it sizes to the budget).
Heals: `tools/bm_heal_driver.py` as a systemd service on nereus000 — on each `Bridge bus power: 1`, backend
heal-candidates -> POST heal-commands -> console `bm pub` at +30/+40/+50/+260 s. No Sofar downlink used.

## Headline
| | bmcam003 / SPOT-33507C | bmcam004 / SPOT-31593C |
|---|---|---|
| wakes (driver) | 47 | 47 |
| clips at backend (keyed) | 45 (+2 newest not yet ingested) | 46 (+1) |
| complete now | 44 | 45 |
| partial now | 1 (05:02 stub-window hard cut, 74 missing > 40: not healable) | 1 (same, 76 missing) |
| heals issued / completed | 5 / 5 | 1 / 1 |
| wakes from command to complete | 1–2 | 2 |
| `<HL a=sent>` ingested | 5 | 1 |
| byte-exact vs unit sent record | 5/5 | 1/1 |
| clips lost entirely | 0 | 0 |
| Spotter reboots | 0 (hourly self-reset suppressed: "Reboot limit reached, ignoring" at :17) | 0 |
| queue-full rejections | 109 (3 stalls of ~36 at 07:27, 10:42, 13:57) | 51 (07:42 x18, 08:44 x1, 11:56 x15, 15:11 x17) |

Every healable partial was healed, byte-exact (backend sha256 == unit `sent/*.sent`, `backend/backend_sha256.txt`).
Driver: 0 backend_error, 0 driver_error, 0 expired.

## Loss causes (per healed clip)
| key (wake UTC) | rig | missing | cause |
|---|---|---|---|
| 0dp5yq 07:25 | 003 | 45–80 (36) | Spotter queue stall 07:27 (36 rejected) |
| 0dp6nn 07:40 | 004 | 49–66 (18) | Spotter queue stall 07:42 (18 rejected) |
| 0dpezo 10:40 | 003 | 25–47 (23) | Spotter queue stall 10:42 |
| 0dpo0m 13:55 | 003 | 33–68 (36) | Spotter queue stall 13:57 (36 rejected) |
| **0dpdlo 10:10** | 003 | **89–123 (35)** | **NOT a device/cellular loss**: Spotter accepted all 35 into MS_Q_CELLULAR_ONLY (console hex decode) AND Sofar api/sensor-data holds all 124 unique chunks (tail stamped 10:13:41–44). Backend ingest missed them. |
| **0dpmmm 13:25** | 003 | **110–123 (14)** | same: all 14 at Sofar (13:28:40–43); backend missed them |

Stalls that did NOT create partials (31593C 11:56, 15:11) hit the first ~26 chunks, which the keyframe
repeat re-sends at the end of the burst — the repeat healed them for free.

## Findings
1. **Backend ingest bug (new, S2/M1 area):** keyed chunks present at Sofar never reached `media`/chunk map for
   2 of 45 bmcam003 clips — both lost exactly the burst TAIL. The heal masked it (re-sent bytes already at Sofar).
   Needs investigation in nereus-vision-dev (poll window / Sofar paging / group close timing are suspects —
   unverified).
2. SPOT-33507C shows a periodic ~47 s queue stall about every 3 h 15 min (07:27, 10:42, 13:57); SPOT-31593C's
   stalls are shorter and irregular.
3. Commit-with-controller-enable gives a 2-min stub bus window -> both first cycles hard-cut at 05:04 (my
   ordering error; SDs recovered clean, sent records intact). Memory: project-bridge-commit-short-first-window.
4. Heal latency: capture -> heal command ~45 min (Notecard sync + ingest + 10-min still-arriving rule) ->
   complete at backend one wake later.

Not verified: partial R2 objects deleted on completion (bucket not listed).
Hardware left: both rigs ARMED and cycling (cron @reboot, power controller ON), driver + monitor services active.
