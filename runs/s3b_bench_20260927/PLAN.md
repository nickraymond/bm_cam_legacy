# Sprint26 S3b bench gate: plan (bmcam003 under stay_on; bmcam004 = control)

The gate (DESIGN §8.3; PLAN_S3b §2):
- 4 triggered cycles on bmcam003 under stay_on, with no reboot;
- a 20-action RSS measurement, which sets the RSS ceiling (H9).

**Code:** `feature/sprint26-s3b-stay-on` at the reviewed SHA.

**bmcam004** stays as it is, as the control: development 1636c8b, supervisor per_boot, armed hourly.

**Why the bus must be held on:** a stay_on Pi on SPOT-33507C's hourly bus is hard-cut at :10, which risks the SD card.

**Needs Nick's OK in chat at the time:**
- every SPOT-33507C console command (bus hold, `bm pub`);
- the cellular budget below.

## Steps (all recorded in this folder; console commands in console_commands.log)

**1. Hold the bus on.**
- `bridge cfg set c3c564b91856226c s u bridgePowerControllerEnabled 0`, then `bridge cfg commit c3c564b91856226c s`, then read it back.
- The commit power-cycles the bus.
- `watcher_host.sh bmcam003` catches the boot, disarms cron and takes backups into `/home/pi/s3abench/backup/` (this overwrites crontab_ARMED.txt with the same armed line).

**2. Deploy** `rc_field_update.sh --ref feature/sprint26-s3b-stay-on --profile bmcam003/live_20260925 --leave-disarmed`.
- Expected: parity OK, and a new hash (registry v3).
- Back up `camera_config.yaml` as `camera_config.s3b_before.yaml`.

**3. Edit the stay_on config** (on-Pi python edit of the v2 file, then verify with `load_for_boot`):
- `mode.run: stay_on`
- `mode.interval_s: 0`
- `mode.heartbeat_s: 300`
- `video.send.message_cap: 40` (cellular saver, see below)

**4. Start the wrapper by hand, exactly as cron would:**
```
nohup /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh >/dev/null 2>&1 </dev/null &
```
Check the log for:
- `[RUN] stay_on`
- one `shared UART open`
- the boot time read
- no action (trigger-only)
- a heartbeat at +300 s

**5. Four triggered cycles:**
- Send `bm pub bmcam/cmd {"id":2631N,"c":"trg","v":2} 1 1`, 4 times, each after the previous clip's END.
- For each cycle:
  - START/END on the console;
  - same PID;
  - still one `shared UART open`;
  - no reboot (`uptime`).
- Backend check: `heal-candidates` for BMCAM_003.

**6. Restart path.**
- **Stop:** `pkill -TERM` the wrapper and the runtime. Expect `stay_on exit 0`, no restart, no halt, and the port closed.
- **Crash restart:**
  - start the wrapper again, then `kill -9` the python;
  - expect a restart after 10 s (marker path), with a new PID and one UART open;
  - this exercises the crash restart, not the watchdog itself, and will be labelled that way;
  - stop it with SIGTERM.

**7. RSS run (20 actions):**
- Settings:
  - `mode.media: still`
  - `still.message_cap: 25`
  - `mode.interval_s: 120`
  - `mode.heartbeat_s: 0`
- Run the wrapper for 20 scheduled actions (≈ 40 min).
- Record `rss_now_kb` and `rss_kb` per action from `supervisor_actions.jsonl`.
- Decide the ceiling: ≥ 1.5 × the plateau and well under 415 MB total. Write it into `rc_stay_on_guard.RSS_CEILING_KB` as a follow-up commit.
- Stop with SIGTERM.

**8. Restore** (the end state for the next session):
- `camera_config.yaml` ← `camera_config.s3b_before.yaml` (per_boot, video, supervisor).
- Armed crontab back from the backup.
- Halt.
- Controller back on: `… 1` + commit + read-back.
- Catch the stub-window boot (`rearm.sh`), confirm armed, halt.
- Write RESULTS.md.

## Cellular budget (all SPOT-33507C cellular)

| step | messages |
|---|---|
| 4 triggered video cycles at cap 40 (≈ 45 msgs each, incl. START/END/WS/acks) | ≈ 190 |
| heartbeats while idle (300 s) | ≈ 15 |
| 20 stills at cap 25 (≈ 30 msgs each, incl. WS/START/END) | ≈ 600 |
| **total** | **≈ 800** |

Without the video cap, the 4 cycles alone would be ≈ 760 messages.

## PASS

- Steps 5–7 complete with no reboot, one UART open per process, and no halt from stay_on.
- The wrapper behaves per H7.
- The RSS ceiling is chosen from the data.
- The unit is restored armed, with the bus schedule back on.
