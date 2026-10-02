# G3 — API hard mode — RESULTS (`runs/g3_hardmode_20261001`)

**G3 overnight (2026-10-01 23:22 → 2026-10-02 02:30 PDT, both units): PASS on the release criterion,
with 2 rows PARTIAL.** Every bad value was refused before send: backend 796/796 scored cases, 0 FAIL
on both devices, and the unit refused everything the backend could not decide. Every sent value was
acked by the unit, and 0 SSH writes were needed: bmcam003 was restored to baseline by cellular
(Sofar-lane) commands alone. Both units end at **f7c9194f**.
PARTIAL: G3.7 (4 Sofar-lane commands still `sent` at 09:30Z: acks dropped at the full Spotter queue or not yet ingested, so the
UI shows them stuck: F-G3-10) and G3.10 (logs.html left to Nick's review). Decisions for Nick: F-G3-4, F-G3-5, F-G3-8.

Spec: `hil/gates/G3_api_hard_mode.md`. Timeline + per-step notes: `gate.log`. Every command and answer:
`steps.log`, `commands.log`. Evidence per step: `pulled/<step>/` (action log, still metadata, thumbnails).
Times in PDT (logs in UTC). Console sends were approved by Nick in this chat at 23:21 PDT.

## Setup

| unit | Spotter | runtime | config hash | bus | cron | mode |
|---|---|---|---|---|---|---|
| bmcam004 | SPOT-31593C | 91a08f624011 (development, registry v6, #97 #98 #101 #103) | f7c9194f baseline | HELD | armed | stay_on, video, trigger-only |
| bmcam003 | SPOT-33507C | 91a08f624011 | f7c9194f (untouched) | HELD | armed | stay_on, video |

Staging: Sprint27 API + UI a5de198 (deployed 23:25 PDT), `BM_COMMAND_SEND` + remote-config env set by
Nick for BMCAM_003/004. Both devices became `eligible` once the backend saw one v9 reply (a
backend-recorded console `ping`, id 1000000, at 06:26Z).

## Criteria

| id | criterion | measured | verdict | evidence |
|---|---|---|---|---|
| G3.1 | backend = repo catalog | served sha256 7e9ff7a314d2 == docs/bmcam_config_catalog.json (registry v6) | PASS | `analysis/plan_*/plan_check.json` |
| G3.2 | bad values refused before send (backend) | 875 cases × 2 devices, final run: **796 PASS / 0 FAIL / 79 INFO on both** (identical outcomes 003 vs 004). Run 1 had 6 FAILs, all case-model errors (4 `blocked_values` ignored, 2 wxh rule over-applied), fixed in `hil_hardmode_cases.py`. INFO disposition below | PASS | `analysis/plan_BMCAM_00*/plan_check.csv` |
| G3.3 | bad values refused by the unit | N1–N4, IP5 ×6, E-negatives: all `REJECTED` with `e=xk`/`e=val` and a precise reason; `camera_config.yaml` + journal sha unchanged across them | PASS | `steps.log` N*, IP5 |
| G3.4 | every sent value acked | 100 %: every console `set`/`reset` answered `OK` (or a precise `REJECTED` for the negatives); backend log shows ok/e for every ingested ack (22/36 at 07:45Z, rest within Sofar lag) | PASS | `steps.log`, `api/d4_commands_BMCAM_004.json` |
| G3.5 | every acked value in effect | each step's START carries the new `cfg=` hash; END/metadata show the value (et_us 9990, ag 2, cg 1.8:1.6, lp 0.5, r=800x450+1904+1071, q=20, fps=8, res=640x360, d=8.0, mode 2304x1296→1280x720) | PASS | `console/starts_bmcam004.txt`, `pulled/*/` |
| G3.6 | video retry (IP6) | the trigger condition (duplicate `--denoise`) is now refused at `set` time by the #103 rule (`REJECTED id=64136`); no command can reach the retry path | N/A (prevented upstream) | `steps.log` IP5 |
| G3.7 | Sofar lane end to end | delivery: 7/7 Sofar-lane commands reached the units (23–63 min: the Spotter checks its mailbox around its report), all answered in id order. ui_status at 09:30Z: 1000034 `saved`, 1000005 `in_effect`, 1000002 `saved`; **1000035, 1000003, 1000004 (a rejection), 1000006 stay `sent`**: their acks were dropped at the Spotter (`MS_Q_CELLULAR_ONLY is full`, F-G3-10) or not yet ingested | PARTIAL | `api/l14a.txt`, `api/l12b.txt`, `console/l12_l14_arrival.txt` |
| G3.8 | 0 units need SSH | no ssh write to a unit between baseline (06:06Z) and now; ssh used read-only (snapshots, pulls). Unit answered `ping` throughout | PASS | `gate.log` |
| G3.9 | restore to baseline | bmcam004: f7c9194f (console resets + 2 Sofar resets; again after E2). bmcam003: f7c9194f at 09:29:22Z by **Sofar-lane resets only** (5 commands; one refused `e=xk` for ordering, F-G3-9, re-sent). End snapshots: both overlay `{mode.run: stay_on}`, cron armed, render present | PASS | `steps.log` R.*, `api/r003_sofar.txt` |
| G3.10 | visible (D4) | backend command log has all 36 G3 admin commands with acks as they ingest; logs.html screenshot left to Nick's morning review (the page needs the token in its URL) | PARTIAL | `api/d4_commands_BMCAM_004.json` |

## Steps (bmcam004, console lane unless noted)

| step | change | result | in-effect evidence |
|---|---|---|---|
| B0 | get (split in 2: LADDER's 5 names > the 4-name limit, F-G3-1) | OK, hash f7c9194f | `steps.log` |
| L1 | ping (console id + backend id 1000000) | OK; device became eligible at staging | `api/l1_*` |
| L2 | exposure ev −1 | OK 9bb7c063 | argv `--ev -1`; still produced |
| L3 | shutter 10000, gain 2.0 | OK | metadata ExposureTime 9990, AnalogueGain 2.0 |
| L4 / L4b | WB daylight; manual gains 1.8/1.6 | OK | `--awb daylight` CT 5619; ColourGains [1.8, 1.6] |
| L5 | focus manual, lens 0.5 | OK | LensPosition 0.5 |
| L6 | still.crop 800×450 | OK | START `r=800x450+1904+1071`, source 800x450 |
| L7 | quality ladder 20/15/11/9 | OK | START `q=20`, 14 msgs |
| L8 | framing wide_720p | OK | `mode 2304x1296` → 1280x720 scale 0.556 (no upscale), START `crop=4608x2592+0+0` |
| L9 | send fps 8, size 640x360 | OK | START `fps=8, res=640x360` |
| L10 | send duration 8.0 | OK | START `d=8.0`, 10 s clip |
| L11 | mode.media still → video (next_boot) | OK, exit 72, restart in 5 s, plain trg → still; back to video | `gate.log` 06:50 |
| L13 | set during a clip | answered after the clip's END (06:48:12Z), applied next action | START fps=8 kept |
| N1–N4 | fps 30, sensor 1536x864, send 481x271, roi framing + 1280x720 send | all REJECTED with the exact geometry reason; hash/config/journal unchanged | `steps.log` |
| N5 | restart after negatives | 2 clean restarts (L11), no exit 2 | |
| IP0 | image_processing.enabled (backend console lane) | OK id 1000001 | |
| IP1 | sharpness 0 / 16 | **REJECTED by the unit** (duplicate `--sharpness` with `video.record.encoder.sharpness`=1.0 in YAML; backend only warned: inputs not reported) — F-G3-4 | |
| IP1/IP2 | contrast, saturation 0/32; brightness −1/1 | all OK + produced; extremes destroy the image (F-G3-5) | still stats in `gate.log` |
| IP3 | denoise auto/off/cdn_off/cdn_fast/cdn_hq | OK, argv `--denoise <v>`, stills produced | |
| IP4 | hdr off/auto/sensor/single-exp/false/true | OK; off/false = no flag, true = `--hdr auto`; stills produced; clip with hdr auto + cdn_hq = normal 7.1 MB clip | `pulled/IP.clip2/` thumb |
| IP5 | just outside each range / bogus enums | all `e=val` with the range | |
| E1 | ev ±8, gain 64, shutter 1, lens 32, cap 500, budget 30, bitrate 0.1, duration 1.0 | all OK + produced; libcamera clamps shutter 1→858 µs, gain 64→AG 16×DG 4 silently | `pulled/E1*` |
| E3 | 228 B / 252 B console lines | reached the unit; >256 B refused by `hil_console.sh` before sending | |
| E4 | heartbeat 300→600→300 (next_boot) | exit 72, restart, heartbeat_s=600 in effect, reset back | |
| E6 | re-publish an applied set | original answer, not re-applied (config + journal sha unchanged) | |
| E8 | reset a never-set key | `nothing to reset` | |
| E10 | ping during the exit-72 restart | lost visibly (no answer, not in the log); re-sent → applied once | |
| L12/L14 | 2 Sofar-lane resets ≥ 65 s apart | both received 07:55Z (23 min after send), acked in id order, hash → f7c9194f | `console/l12_l14_arrival.txt` |
| E2 | out-of-frame still/video crops; ev 8.01; gain 64.1; message_cap 501 (console) | crops + ev + gain REJECTED by the unit; **cap 501 ACCEPTED** (F-G3-8), reset at once | `steps.log` E2.* |
| 003 subset | exposure+WB+focus, crop, framing clip, N1–N4, image processing (backend console lane) + still + clip | identical behaviour to 004 (same hashes for the same changes, same refusal texts); denoise cdn_fast + hdr sensor clip 5.1 MB OK | `pulled/003.*` |

Cellular spend (G3, both units): ~700 still messages + 10 clips × ≤ 80 ≈ 1500 messages, plus backend heals of the G3 media.

## Findings

| id | seen | effect | owner |
|---|---|---|---|
| F-G3-1 | LADDER baseline `get` lists 5 names; the unit takes 1..4 (`e=val k=k`) | doc only | Sprint27 (LADDER.md) |
| F-G3-2 | one-shot `save_local` capture refused on a video unit: SD sits at the 75 % ring cap → `storage_full`; log labels the still override `media=video` | save_local can never work on a full-ring video unit | S3c owner / Sprint27 Q3 (next sprint) |
| F-G3-3 | `still.message_cap` is not a ceiling when even the lowest rung exceeds it (no_fit_cap → floor sent, bounded by budget only): m=30 → 32 msgs | small overshoot; matters for the Q7 cost caps | Sprint27 |
| F-G3-4 | `camera.image_processing.sharpness` is refused by the unit on any video unit whose YAML sets `video.record.encoder.sharpness` (bmcam003/004: 1.0); the backend cannot refuse it (keys never reported) and the UI offers it | sharpness control unusable on these units; a UI user sees `rejected` | Sprint27 + UI session |
| F-G3-5 | contrast/saturation/brightness extremes inside the measured range give unusable frames: contrast 0 = flat grey, contrast 32 / saturation 32 / brightness −1 = black, brightness +1 = white (stills AND a black clip) | a valid value can blank every capture until reset | Sprint27: narrow the UI range or add warnings (decision for Nick) |
| F-G3-6 | the mote now delivers each console command ~5× (was 3× on 09-25); dedupe absorbs every copy | none seen | mote session (FYI) |
| F-G3-8 | `still.message_cap` 501 is accepted by the unit: Nick's Q7 cost cap (refuse > 500) lives only in the backend; the unit's range is 1..2000 | a console / direct sender can exceed the cost cap | Sprint27 (decide whether the unit enforces Q7) |
| F-G3-9 | a `reset` that clears `white_balance.gains` while `white_balance.mode` stays `manual` is refused atomically (`e=xk`); the backend accepts resets with a "cross-key left to the unit" warning | a UI "reset group" in the wrong order fails; users must reset mode before gains | Sprint27 / UI (order resets, or reset WB as one group) |
| F-G3-10 | the Spotters' cellular queue overflowed during G3 (126× SPOT-31593C, 54× SPOT-33507C `MS_Q_CELLULAR_ONLY is full`); command acks dropped there are never re-sent, so the UI shows `sent` forever although the unit applied the change and the reported hash matches | D2/D4: a correct change looks stuck in the UI | S6b backend (derive `in_effect` from the reported hash when the ack is missing?) + release plan F1 risk |
| F-G3-7 | `requested_*` fields are not in the capture sidecar (LADDER assumes so); they are in the action log `requested={…}` line; video clips have no JSON manifest in `videos/` | evidence location only | Sprint27 (LADDER.md) |

## Part A INFO disposition (79 `depends` rows, both devices)

Refused (22): malformed hhmm (`24:00`, `7:00`, `12:60`), bad tz, gains of wrong arity / 0.0, crop with 0 width,
ladder `[101,50]`/`[0]`, `640x`, `mode.run stay_on` (fail-safe: `power.bus_always_on` not reported) — all correct.
Accepted (57): valid values of cross-key keys whose inputs the backend has not seen reported (WB mode, video
geometry, crops, send fps/size, mode.media) — by spec (REVIEW_r1 row 7: warn, the unit decides). The risky
ones were checked on the unit: out-of-frame crops, odd/tiny record output, send > record, duplicate
`--sharpness`/`--denoise` → all REJECTED by the unit with `e=xk`.

## Not tested (yet)

- bmcam003 ran a subset (exposure/WB/focus, crop, framing clip, N1–N4, image processing, Sofar restore), not the full ladder.
- logs.html screenshot (D4) — needs the token in the page URL; Nick's review.
- E5 (lower remote id → `e:old`): no unused lower remote id exists without risking the backend allocator; covered in S5 L12.
- E7 still-burst variant; power cut mid-change (not without Nick's OK).
