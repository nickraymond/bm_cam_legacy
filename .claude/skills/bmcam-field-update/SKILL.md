---
name: bmcam-field-update
description: Update the RC runtime + bm_serial values on an ARMED bmcam unit (cron @reboot + real power_halt) without leaving it mid-surgery — catch it awake, disarm, run tools/rc_field_update.sh, re-arm, live transmit, verify delivery at Sofar. Use when rolling new code/config to a unit that halts itself after every cycle (bmcam000-style field/soak units). For brand-new units use bmcam-provision instead.
---

# bmcam Field Update (armed unit)

Purpose: take a live, armed unit (cron `@reboot` cycle + `power_halt` real —
it HALTS itself minutes after boot) through a code+config update and end
field-normal: on `development`, new values, cron armed, halted after one
verified live transmit.

Proven on: bmcam000 (Sprint07-era checkout → development e031abd), 2026-07-27.
The mechanics were first proven on bmcam003 the same night.

## Why this is not just deploy_rc_runtime.sh

The unit self-halts after ANY cycle (`power_halt` runs in `finally`). The SSH
window after power-on is only as long as one cycle. Everything below exists to
(a) win that race safely, (b) change only what the update intends, (c) end in
a known field state.

## Pre-flight (Mac)

1. **Ref check**: everything to deploy must be merged into `development`
   (`git log --oneline origin/development | head`). The update script deploys
   `--ref development` by default.
2. **Know the unit's Spotter ID** (ask the human — e.g. bmcam000 → SPOT-31593C,
   bench bmcam003 → SPOT-33507C). You cannot verify delivery without it.
3. **Confirm the human is ready to power the unit** — you get one short SSH
   window per power cycle until the cron is disarmed.
4. **Pre-stage a catch-it-awake watcher** BEFORE the human powers the unit
   (bmcam003/bmcam000 pattern — poll SSH every 2 s, then in ONE session):
   - `crontab -l > /home/pi/crontab_backup_fieldupdate_<TS>.txt`
     — **this backup captures the ARMED state; it is the re-arm file later**
   - disable the `@reboot` RC line (sed comment), also match the legacy
     HEIC `run_capture_cycle.sh` pattern
   - `pkill -TERM -f 'rc_run_capture_cycle.sh|rc_progressive_jpeg.py|main_pi_camera.py'`
     — SIGTERM kills Python without running `finally`, so no halt fires
   - survey: hostname/uptime, crontab, processes (want NONE), repo checkout
     path + sha, `software_sha.txt`, deployed `bm_serial:` values, `power_halt`
     state, `/dev/serial0` target
   On bmcam000 the watcher won the race at 8 s after boot. Run it in the
   background, tell the human "power it now".

## Phase 1 — run the update

The unit's checkout may predate `tools/rc_field_update.sh` — stage it via
`/tmp`, **never scp into the repo tree** (untracked files block the pull):

```
scp tools/rc_field_update.sh pi@bmcamNNN:/tmp/
ssh pi@bmcamNNN 'bash /tmp/rc_field_update.sh --repo /home/pi/repos/bm_cam_legacy \
    --ref development --profile bmcamNNN --leave-disarmed'
```

`--leave-disarmed` is deliberate: the script's own crontab backup was taken
AFTER the watcher disarmed, so its stage-7 restore would restore a DISARMED
crontab. Re-arm manually in Phase 3 from the WATCHER's backup.

The script stages: preflight → disarm → git sync to ref → deploy_rc_runtime.sh
(tar backup + manifest + py_compile + config gates) → surgical bm_serial value
patch from `device_profiles/<profile>/` → UART gate → validation (print-config
+ real UART open). Any failure leaves the unit disarmed-safe with rollback
commands printed.

## Phase 2 — review the stage-4 drift report

The script patches ONLY `image_buffer_size` / `image_transmit_delay_seconds`.
Everything else that differs between deployed YAML and profile is printed but
NOT applied. Read that diff — device-specific drift (halt state, window,
timezone, crop) is usually intentional; anything surprising goes to the human
before proceeding.

## Phase 3 — re-arm

```
ssh pi@bmcamNNN 'crontab /home/pi/crontab_backup_fieldupdate_<TS>.txt && crontab -l'
```
Verify the RC `@reboot` line is active (and the legacy HEIC line still
commented).

## Phase 4 — live transmit (the box WILL halt at the end)

With `power_halt` enabled/real, the cycle halts the box in `finally` — that
is the field-normal end state (updated + armed + halted). Two hard-won rules:

- **Log to a file on the Pi, not through the SSH pipe.** The halt kills SSH
  abruptly; piped output (especially through a buffering `grep`) is lost, and
  the ssh process can hang ~40 min on the dead TCP session before exiting 255.
  Run detached:
```
ssh pi@bmcamNNN 'cd /home/pi/BM_Devel_Pi && nohup python3 -u rc_progressive_jpeg.py \
    --transmit > cron_logs/field_update_live_$(date -u +%Y%m%dT%H%M%SZ).log 2>&1 &'
```
  The log survives on disk for the next power window even if everything dies.
- **SSH death / unit going dark ≈ 2 min later is SUCCESS**, not failure.
  Expected timing at 384/1.0: ~5 s capture, q90 select, ~100–110 msgs ≈ 2 min.

## Phase 5 — verify delivery at Sofar (patience required)

Spotter → Sofar API delay is significant (many minutes; the Notecard batches
uploads and the Spotter schedules syncs). **Do not diagnose failure from an
empty query in the first ~15 min.** Poll `api/sensor-data` for the unit's
Spotter ID (pattern: `sprints/Sprint09_mote_throughput/count_phase_b.py`;
token in `SOFAR_API_TOKEN_BM_REEF`):

- our cycle's rows: `<I{i}>` chunks + `<WS`/`<START`/`<END` envelopes, hex in
  `data[].value`, filtered by timestamp ≥ cycle start
- beware stragglers: a pre-update cycle's tail (old chunk indexes, old START
  filename) can arrive in the same window — filter by the START/END filename
  timestamp
- PASS = all chunk indexes 0..N-1 present + START + END; the website render
  follows its own poll cadence after that

## Video-mode units (Sprint15)

A unit with `capture_mode: "video"` and `session_minutes: 0` records
continuously and does NOT self-halt — the catch-it-awake race above
does not apply; SSH in any time. What still applies, plus differences:

- **Disarm the same way** (comment the `@reboot` line, then
  `pkill -TERM -f 'rc_run_capture_cycle.sh|rc_progressive_jpeg.py'`).
  SIGTERM at most costs the in-flight clip (`.part` — swept at next
  boot, crash contract D-S15-2). The encoder owns the camera while
  recording: any bench `rpicam-*` command fails "in use" until then.
- **ffmpeg is a runtime dependency** (mux + posters), NOT preinstalled
  on trixie: `sudo apt-get install -y ffmpeg` before first video run
  (found missing on bmcam003/004, 2026-08-18).
- **Settings GUI**: `http://<unit>:8080/settings` edits
  `camera_schedule.yaml` with timestamped backups
  (`camera_schedule.yaml.before_gui_*`). LEAVE the backups — they are
  the customer's undo. Changes apply on restart (reboot or runtime
  restart), not live; the gallery lives at `http://<unit>:8080/`.
- **Re-arm + verify**: after update, reboot and confirm a `.part` is
  growing in `~/BM_Devel_Pi/videos/` and the gallery answers 200 —
  clip triples + manifest are the delivery evidence (no Sofar image
  rows in video mode; per-clip `{"t":"vid",...}` status lines ride the
  cellular path instead, when a Spotter is attached and transmitting).

## Failure modes seen / expected

- **Cycle running at preflight** → script aborts; wait for the halt, power
  cycle, catch the next window.
- **UART gate fails** → unit was provisioned before the UART fix; run
  `tools/setup_bm_uart.sh` + reboot (needs the disarmed window!), then re-run.
- **`git pull --ff-only` fails** → untracked/scp'd files in the checkout
  (`git status --short`); `rm` them and re-run.
- **Nothing at Sofar after ~30 min** → check the unit's Spotter `post`
  (cellular states) if reachable, else power-cycle the unit: the armed cron
  runs a fresh cycle (free retest) and the Phase-4 log file on disk says how
  far the previous cycle got.

## Rollback (printed by the script; all timestamped)

- crontab: `crontab /home/pi/crontab_before_field_update_<TS>.txt`
- YAML: `cp /home/pi/BM_Devel_Pi/camera_schedule.yaml.before_field_update_<TS> ...yaml`
- runtime: `tar xzf /home/pi/backups/BM_Devel_Pi_before_rc_deploy_<host>_<TS>.tgz -C /home/pi`
