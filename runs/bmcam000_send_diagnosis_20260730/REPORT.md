# bmcam000 — "stopped sending" diagnosis + 20/10 overnight arm (2026-07-30)

Session: worker session, branch `feature/bmcam000-send-diagnosis` (off `development`).
Unit: bmcam000 (pi@100.119.14.92) on SPOT-31593C, bridge 0e582dd12c1e1480,
camera node 49cfe4d7cceb2771. Runtime sha 648c889115d5 (Sprint11, PR #21).
All timestamps UTC (PT = UTC-7).

## Root cause of "no new image since ~9 PM PT"

The Sprint11 wrap-up committed `bridgePowerControllerEnabled 0` (bus
always-on) at **01:58:52Z** — clobbering the 20/10 schedule Nick had
committed at 20:51Z. Cycles only fire on boot; with the bus never cycling,
the Pi sat up-idle and nothing sent. Console log has both commits.

Last complete image before the stall: `refsrc_20260730T020923Z` (192/192,
capture 02:09Z ≈ 7:09 PM PT).

Additional bench chaos after that (all explained, none code bugs):
- **03:43:00Z** console: `Power switch change to OFF. Restarting` → Spotter
  in charge mode ~1 h; the 03:44Z cycle died at 17/192.
- An 04:30Z cycle reached 168/192 before the Spotter woke at 04:44Z and its
  message service dropped the final chunks (the 04:45Z queue-full burst).
- The 04:00:21Z boot landed inside the 23:59–00:01 America/New_York
  transmit-window gap and legitimately skipped (8.8 s cycle, exit 0).

## The five requirements — evidence

1. **Reef reference image** — `bm_command_state.json` `settings.src=1`;
   `--print-config`: `[CMD] override source_image_path: None ->
   reference_images/prepared/P7071008/synthetic_native_4608x2592.jpg (src=1)`.
   On-wire (decoded console hex): `<START IMG> filename:
   refsrc_20260730T050857Z_compressed.jpg ... length: 192, q=60`.
2. **Sends on reboot** — `crontab -l`: `@reboot /usr/bin/flock -n
   /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh`
   (active, no leading #). Proven three times tonight (05:08 reset cycle,
   06:00 and 06:30 scheduled cycles).
3. **5-second pacing** — on-device `yaml.safe_load`:
   `image_transmit_delay_seconds=5.0`, `image_buffer_size=384`,
   `progressive_jpeg.message_cap=195`, `max_run_time_min=18`.
   `--print-config`: `pacing ... chunk_b64_chars=384 delay_s=5.0`, `cycle
   budget: max_run_time_min=18 (1080 s)`, `message cap: 195`. On-wire:
   `<END IMG> ... uart_duration_sec: 969.5, sent_buffers: 192` = 5.05 s/msg.
4. **No 90 s dwell** — deployed sha 648c889115d5 (runtime telemetry line).
   `--print-config` loud-ignores the leftover key: `[CMD][WARN]
   bm_commands.pre_capture_listen_s is IGNORED since Sprint11`. Measured
   power-on→capture: **28 s** (bus power 05:08:29Z → capture 05:08:57Z);
   repeated at 06:00 (:00:00.8 → :00:27) and 06:30 (:30:00.9 → :30:27).
5. **20/10 cycling prepared, in the load-bearing order**:
   a. power_halt re-enabled FIRST (`enabled=true, dry_run=false`), patched
      with `tools/patch_camera_schedule.py` (backup
      `camera_schedule.yaml.bak_20260730T050438Z`), read back via
      `yaml.safe_load` AND `--print-config`.
   b. Verification cycle while still always-on: console `reset` 05:08:23Z →
      boot → START refsrc_ → 192 chunks → END 05:25:05Z → **Pi self-halted**
      (addr-65 draw 0.448 W = halted signature, ssh unreachable).
   c. Schedule committed 05:28:06Z and read back via `bridge cfg status`:
      `bridgePowerControllerEnabled=1, sampleIntervalMs=1800000,
      sampleDurationMs=1200000, samplesPerReport=1`.
   d. Two full scheduled cycles observed (see below).

## Two-cycle result (the overnight configuration, working)

| cycle | boot (bus power 1) | capture | END on-wire | halt (0.45 W) | bus cut |
|-------|--------------------|---------|-------------|----------------|---------|
| 1 | 06:00:00.8Z | 06:00:27Z | 06:16:37Z (`refsrc_20260730T060027Z`, sent 192/192, 969.6 s) | by 06:18:56Z | 06:20:00.8Z |
| 2 | 06:30:00.9Z | 06:30:27Z | 06:46:41Z (`refsrc_20260730T063026Z`, sent 192/192, 969.6 s) | by 06:48:46Z | 06:50:00.9Z |

Cycle 3 opened on schedule at 07:00:01Z (left running unattended).

Timing margin (LOUD, per instructions): power-on → END ≈ 16.6 min; halt
lands ~17.5–18.3 min into the 20-min window. **~1.5–3 min margin.** It fits,
but there is no room for any added per-cycle overhead at 5.0 s pacing.

## Delivery caveat — Spotter-side chunk drops (systemic, NOT fixed tonight)

`MS_Q_CELLULAR_ONLY is full` / `Unable to submit` bursts drop chunks
permanently (no retry path). Observed bursts: 01:42Z (1), 04:45Z (~5, tail
of the cut 04:30 image), 05:18:44–59Z (4 → gap at ~chunk 115 of the verify
image), 06:08:38–53Z (4 → gap at ~chunk 96 of cycle 1). Signature matches
the historical single-mid-gap incomplete images (gap@104, gap@5, gap@135
earlier tonight). Mechanism: the Pi paces 1 chunk/5 s with no backpressure;
the Spotter RAM queue drains into the Notecard and stalls ~15–20 s at a
time (Notecard busy — inferred from timing, not proven). Roughly one stall
some cycles, none in others → historically ~10/16 scheduled images
COMPLETE. Fix options (out of scope tonight): slower pacing (budget is
already tight), Spotter firmware queue depth/retry, or END-side repair.

## Final unit state

- bmcam000: ARMED (@reboot flock cron) + real halt ENABLED + 20/10 schedule
  COMMITTED and cycling on :00/:30 boundaries. Reef reference source,
  5.0 s / 384 B / cap 195 / 18 min budget, sha 648c889115d5.
- One known hard cut at 05:30:06Z hit the Pi ~2 min after boot (the commit
  re-init granted only 120 s of bus power — commit during the OFF phase
  gives a 2-min grace, then cuts). Pi survived; subsequent cycles clean.
  RULE going forward: commit schedule changes only while the Pi is halted,
  and expect the 2-min grace cut.
- bmcam003 / SPOT-33507C: untouched (separate rebuild effort).

## API delivery (Sofar sensor-data, polled 07:10Z via curl + count_complete_images)

- `refsrc_20260730T063026Z` (cycle 2): **recv=192/192, END seen, COMPLETE** —
  the end-to-end pass in the exact overnight configuration. Website render
  follows on its own polling delay.
- `refsrc_20260730T060027Z` (cycle 1): recv=188/192, gap@96, incomplete —
  the four chunks the Spotter dropped in the 06:08Z queue stall (Pi-side
  sent 192/192). Renders progressively to ~chunk 96.
- `refsrc_20260730T052837Z` (the 05:30 commit-grace cut): 3/192 stub, expected.
- `refsrc_20260730T050857Z` (verification cycle): its START and pre-05:18Z
  chunks had not been ingested by Sofar as of 07:10Z (later chunks present
  but orphaned). Ingestion there is batchy and out of order; not a unit
  problem — the on-wire END (`sent_buffers: 192`) is the unit-side proof.

## Known behaviors to expect overnight

- ~1 Notecard/queue stall in some cycles → a single mid-image gap →
  incomplete at the API for those cycles. Historically ~10/16 complete.
- The 04:00Z boot (00:00 America/New_York) lands in the 23:59–00:01
  transmit-window gap and skips its transmit — one skipped cycle per night,
  by design of the current window.
- Commit-time rule: a `bridge cfg commit` during the OFF phase grants 120 s
  of bus power, then cuts. Only commit while the Pi is halted.
