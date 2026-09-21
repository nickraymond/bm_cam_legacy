# Sprint24 — outdoor HIL baseline: video uplink + command downlink, two rigs

> **SCOPE CHANGE — Nick, 2026-09-21 (after this plan was minted): remote
> commands are OUT of this baseline.** Skip everything below that concerns the
> command daemon inside `video_tx` (decision 2) and the 1-ping/hour overlay or
> any ack-rate metric (decision 5). Reason: Sofar confirmed the replay of held
> `bm` commands is a fixed 10 s grace period after BM network boot; the Pi
> subscribes at ~38 s; the fix is a mote-side cache being worked with Sofar
> (**TODO-BM-017**). KEEP the per-cycle `report_utc`, `rx_check_utc` and
> `queue_full_count_in_cycle` columns — that hourly report minute is where the
> VIDEO loses chunks. Drop `cmd_arrival_utc`, `held|live`, `ack_utc`.
> Sprint23 is now pushed and in PR #57 (not "local, NOT pushed" as written
> below); bench state + restore commands:
> `sprints/Sprint23_remote_msg_latency/HANDOFF.md` §2. Record:
> https://github.com/nickraymond/bm_cam_legacy/pull/56#issuecomment-5765169684

Minted by Nick 2026-09-21. Merges Sprint22 (short video over Spotter; merged to
`development`, PR #55) with Sprint23 (remote-command latency; branch
`feature/sprint23-remote-msg-latency`, local, NOT pushed, no PR — its
`sprints/Sprint23_remote_msg_latency/RESULTS.md` is its source of truth).

## Goal

**Baseline, not optimisation.** Characterise the pipeline from camera glass to
computer screen, OUTDOORS, on Spotter firmware v2.16.8, with as few variables
as possible. We are back to measuring the problem, not solving it.

## Decisions (Nick, 2026-09-21) — do not re-litigate

| # | Decision |
|---|---|
| D1 | Two rigs in parallel running the **SAME test** (replicates), not A/B. The rigs differ (Notecard NOTE-WBGLW fw 6.2.5 vs NOTE-WBNA-500 fw 4.2.1, SD card, antenna), so a cross-rig A/B is confounded. A/B comes later, WITHIN one rig, alternating cycles. |
| D2 | **Command daemon runs at all times**, including during a `video_tx` cycle: a received command must be confirmed (acked) even while video is recording/sending. |
| D3 | **NO lane/time guard.** `transmit_phase.enabled: false`. Bursts go when they go; the 16-minute period walks the cycle across the 5-minute grid and the hour mark, so the baseline itself shows where loss falls. |
| D4 | 16-minute bus period (10 min on / 6 off). `note sync` OFF everywhere (monitors run with NO `--sync-min`). Cellular only — every send `cellular_only` (0x02); Iridium fallback is never acceptable. Messages unlimited while cellular. |
| D5 | 1 command ping per hour to EACH Spotter (Sprint23's tester), so both rigs yield downlink arrival + ack data alongside the video uplink. |
| D6 | The Spotter SD `log/` is the primary record of the cellular queue (it matches the USB console and is cleaner). Both cards wiped + formatted clean before the run; verify logging with a 15-minute indoor check first. |
| D7 | The 1080p original stays on the camera SD; rate control = x264 2-pass to a message budget; keyframe repeat at the tail (contract rev 3). |

## Hardware in scope

| Rig | Camera | Spotter | Bridge node | Camera BM node | Backend device |
|---|---|---|---|---|---|
| A | bmcam004 | SPOT-33507C | `c3c564b91856226c` | `0xe6fe83ea6b4a2b7f` | `BMCAM_004` (staging) |
| B | bmcam003 | SPOT-31593C | `0e582dd12c1e1480` (skill; confirm) | `53171fa3d81a8e6f` | unknown — CHECK before relying on it |

Never open a Spotter port twice. `spotter_serial_monitor.py --only <SPOT-ID>`
always. A monitor's `cmd.txt` is a single slot — one writer at a time.

## Work, in order (one variable at a time)

1. **SD cards.** Nick images (optional) + wipes both. Format FROM THE SPOTTER
   (`sd format`), never pre-create folders on a Mac (a Mac-made `log/` is the
   likely cause of SPOT-33507C's duplicate `log/` entry). 15-minute indoor check
   per unit: `ls` shows `log/` ONCE · `cat log/index` increments · `log list`
   sizes grow for MS/NCD/HDR · `sd err` 0/0 · after `log flush` the files are
   visible on the Mac.
2. **Command daemon in `video_tx`** (D2) — the one code bite before the run.
   Port the stills integration into `rc_video_tx.run_video_tx_cycle`:
   `daemon_factory` + `daemon.start()` at cycle start; `cmd_hooks.gate_kwargs_for`
   so the time gate reads Spotter UTC over the SHARED port;
   `make_pending_pump_fn` / `make_ack_drain_fn(defer=…)` in the pacing slots of
   `transmit_video_clip` (add the two optional callables, default None = wire
   unchanged, golden-wire test must stay byte-identical); `flush_acks` +
   `post_transmit_listen` after the burst; `cmd_hooks.shutdown` in `finally`.
   Tests with injected fakes, zero sleep. Known limit it does NOT fix: commands
   HELD while the bus is off replay ~8 s after bus power, the Pi subscribes at
   ~38 s, so held commands are still lost (Sprint23 finding 3).
3. **Same build + same YAML on BOTH cameras** (D1): `development` + bite 2,
   `video_tx.enabled: true`, `transmit_phase.enabled: false`,
   `progressive_jpeg.max_run_time_min: 8`, `power_halt.enabled: true`,
   `enforce_time_window: false`, `bm_commands.enabled: true`. `touch
   ~/BM_Devel_Pi/NO_HALT` until one full cycle has been watched on each.
   `software_sha.txt` must equal the deployed sha (START carries `sha=`).
4. **One watched cycle per rig indoors** — clip complete on staging, a ping acked.
5. **Ebox duty cycle on both**: `sampleIntervalMs 960000`, `sampleDurationMs
   600000`, `bridgePowerControllerEnabled 1`, commit, READ BACK. The commit cuts
   or forces bus power immediately and triggers a LEGACY report + mailbox check —
   do it ONCE, before the run, never mid-run.
6. **Move outdoors; run ≥ 3 h.** No Spotter reboots during the run (a reboot
   moves the hourly report minute). `log flush` before pulling any card.
7. **Report.** `tools/bm_video_soak_report.py` per rig, extended with Sprint23's
   columns: `report_utc`, `rx_check_utc`, `cmd_arrival_utc`, `held|live`,
   `ack_utc`, `queue_full_count_in_cycle`. Source the Spotter side from the SD
   `log/` (MS.log, BM_TX.log, HDR.log, NCD.log), console log as backup.

## Pass / fail — set before the run

| Metric | Pass |
|---|---|
| cycles that boot, run, halt cleanly (both rigs) | all |
| SD / filesystem errors after the power cuts | 0 |
| every lost chunk explained by an MS.log event | yes |
| clips complete at Sofar, by phase on the 5-min grid and vs the hour mark | REPORT (this run measures it) |
| commands: arrival, held vs live, acked | REPORT |

## What is already known (measured; cite, do not re-derive)

- The camera gets NO error when the Spotter rejects a message
  (`Queue MS_Q_CELLULAR_ONLY is full`, 2-slot queue). Sprint22's first real clip
  lost its keyframe to 17 rejections at 07:30:31-48Z — inside SPOT-33507C's
  HOURLY report window (report 07:29:59, sync 07:30:02, mailbox check 07:30:40).
  A send 16 s after an ordinary 5-minute boundary lost nothing. Sprint23 saw 301
  rejections overnight clustered on its report minute.
- The hourly LEGACY report is the window bursts should avoid AND the only time
  commands are delivered (~72 s after it). Hypothesis (Sprint23, unverified):
  report minute = first post-boot report rounded up to the next 5/10 min, then
  every 60 min — so every Spotter reboot moves it.
- Spotter SD `log/` = console, cleaner (skill `nereus-spotter-sd-analysis` §10).
  Bridge `power.log` gives bus watts at 10 s: recorder 1.62 W, record+encode
  1.68 W, burst 0.87 W.
- Pi Zero 2 W: 1080p→480x270 decode 5-8 s, 2-pass fit ~5 s, never throttled.
- A bare `BristlemouthSerial()` defaults to Iridium fallback; `rc_video_tx` forces
  `cellular_only`.
- Full measurements: `sprints/Sprint22_video_over_spotter/TRACKER.md`.

## Not in this sprint

Lane/hour-mark avoidance, scene-aware or power-window-aware budgets, fixing the
held-command boot race, backend changes, anything on `main`.
