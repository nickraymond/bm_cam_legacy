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
| heals issued / needed | 5 / **3** (2 redundant — correction below) | 1 / 1 |
| wakes from command to complete | 1–2 | 2 |
| `<HL a=sent>` ingested | 5 | 1 |
| byte-exact vs unit sent record | 5/5 (3 healed, 2 completed from the original send) | 1/1 |
| clips lost entirely | 0 | 0 |
| Spotter reboots | 0 (hourly self-reset suppressed: "Reboot limit reached, ignoring" at :17) | 0 |
| queue-full rejections | 109 (3 stalls of ~36 at 07:27, 10:42, 13:57) | 51 (07:42 x18, 08:44 x1, 11:56 x15, 15:11 x17) |

Every real partial was healed, byte-exact (backend sha256 == unit `sent/*.sent`, `backend/backend_sha256.txt`).
Driver: 0 backend_error, 0 driver_error, 0 expired. Two of the five bmcam003 heals (0dpdlo, 0dpmmm) were
redundant: those rows completed from the ORIGINAL send before the heal bytes reached Sofar (see "Correction").

## Loss causes (per healed clip)
| key (wake UTC) | rig | missing | cause |
|---|---|---|---|
| 0dp5yq 07:25 | 003 | 45–80 (36) | Spotter queue stall 07:27 (36 rejected) |
| 0dp6nn 07:40 | 004 | 49–66 (18) | Spotter queue stall 07:42 (18 rejected) |
| 0dpezo 10:40 | 003 | 25–47 (23) | Spotter queue stall 10:42 |
| 0dpo0m 13:55 | 003 | 33–68 (36) | Spotter queue stall 13:57 (36 rejected) |
| 0dpdlo 10:10 | 003 | 89–123 (35) | **No loss at all.** Sofar exposed the burst ~26 min late in two batches ~75 s apart (head 10:39:14Z, tail 10:40:29Z); the driver read the row in that gap. Row completed from the original chunks at 10:40:30Z, before the heal bytes reached Sofar (10:41:00Z+). Heal 100001 redundant. |
| 0dpmmm 13:25 | 003 | 110–123 (14) | same: head 13:54:25Z, driver 13:55:00Z, completed from original 13:55:26Z, heal bytes at Sofar 13:56:00Z+. Heal 100003 redundant. |

Stalls that did NOT create partials (31593C 11:56, 15:11) hit the first ~26 chunks, which the keyframe
repeat re-sends at the end of the burst — the repeat healed them for free.

## Findings
1. ~~Backend ingest bug~~ **CORRECTED 2026-09-24 (nereus-vision-dev investigation, BUGS.md B19):** there was
   no ingest drop. Sofar api/sensor-data exposes SPOT-33507C rows 11–30 min after their Spotter timestamp
   (48 of 71 BMCAM_003 telemetry rows that day > 10 min, max 29.5) in ~1-min batches, and a batch boundary fell
   mid-burst (88|89, 109|110). The staging cron was polling every minute, so the head batch became a partial
   row within seconds; the heal driver fired at bus-on 35–46 s later; the backend's still-arriving rule keyed
   on `captured_at_utc` (device time, already ~29 min old) and said healable. Both rows then completed from
   the original chunks one poll later, before the heal bytes existed at Sofar; the heal chunks were ingested
   as `skipped_keyed_already_complete`. Fixed backend-side: `media.last_received_at` (migration 0015) +
   receive-time still-arriving rule (nvd #58, #60), cron now `*/5` with `--hours 3`. Driver follow-up for
   this repo: a `/missing` 409 `still_arriving` should not count as a used wake.
2. SPOT-33507C shows a periodic ~47 s queue stall about every 3 h 15 min (07:27, 10:42, 13:57); SPOT-31593C's
   stalls are shorter and irregular.
3. Commit-with-controller-enable gives a 2-min stub bus window -> both first cycles hard-cut at 05:04 (my
   ordering error; SDs recovered clean, sent records intact). Memory: project-bridge-commit-short-first-window.
4. Heal latency: capture -> heal command ~45 min (Sofar exposure lag 11–30 min + ingest + 10-min
   still-arriving rule, now measured from backend receive time) -> complete at backend one wake later.

## Correction (2026-09-24, after the backend investigation)
The "backend ingest bug" in the first version of this file was a misread of a healed-then-complete row.
Evidence (staging DB, read-only): media 53553 / 53619 `created_at` 10:39:14Z / 13:54:25Z (partial, head
batch), `timestamp_utc` 10:40:30Z / 13:55:26Z (completed in place from the original chunks), stored sha256 ==
sha256 of the original burst parsed from Sofar (41cf8be4… / b53ff586…); heal chunks stamped at Sofar
10:41:00Z+ / 13:56:00Z+; `heal_commands` 100001 / 100003 created 10:40:00Z / 13:55:00Z. Net: 6 heals issued
across both rigs, 4 needed, 4/4 byte-exact; 0 clips lost; the two redundant heals cost 49 chunks of airtime
and nothing else. `backend/healed_rows.txt` lists 0dpdlo and 0dpmmm as healed rows — they are complete rows,
but not by heal.

Not verified: partial R2 objects deleted on completion (bucket not listed).
Hardware left: both rigs ARMED and cycling (cron @reboot, power controller ON), driver + monitor services active.
