# Sprint22 — duty-cycle video soak: runbook

> **SUPERSEDED IN PART by `sprints/Sprint24_outdoor_hil_baseline/PLAN.md`
> (Nick, 2026-09-21):** the run is now an OUTDOOR two-rig baseline. **No lane
> guard** (`transmit_phase.enabled: false`), the BM command daemon must run
> during `video_tx`, and both rigs run the same test. Everything else here
> (YAML delta, window arithmetic, bridge commands, NO_HALT recovery) still holds.

Goal (Nick, 2026-09-21): evaluate the video pipeline **from camera glass to
computer screen**, under a production-like power cycle, and learn how the
Spotter's cellular queue and timing actually behave before changing how we
stagger our own transmissions.

Hardware: **bmcam004 + SPOT-33507C only.** Never open, command or log any other
Spotter (`spotter_serial_monitor.py --only SPOT-33507C`, always).

Decisions on record: 16-minute period (10 on / 6 off), `note sync` OFF,
cellular-only (unlimited messages), 1080p original stays on the camera SD,
2-pass bitrate, keyframe repeat, 3 hours ≈ 11 cycles.

## 0. Order of operations (one variable at a time)

| # | Step | Owner | Gate |
|---|---|---|---|
| 1 | Spotter firmware → v2.16.8 | Nick | `post` clean; bridge power config still present |
| 2 | Pull the Spotter SD card, hand it over for review | Nick | reviewed; SD-analysis skill updated |
| 3 | Monitoring Pi on the Spotter USB console | Nick + agent | console log growing; `lsof` shows ONE holder |
| 4 | Baseline on v2.16.8: one golden-clip send | agent | 126/126 at Sofar, byte-exact on staging |
| 5 | Merge PR #55, deploy `development` to bmcam004 | Nick merges, agent deploys | `software_sha.txt` == merged sha; `--print-config` clean |
| 6 | One manual video_tx cycle, halt held (`NO_HALT`) | agent | clip complete on staging |
| 7 | Ebox duty cycle on; remove `NO_HALT` | agent, Nick confirms | first cold-boot cycle completes and halts |
| 8 | Run 3 h; build the per-cycle table | agent | section 5 |

## 1. Camera YAML changes (`~/BM_Devel_Pi/camera_schedule.yaml` on bmcam004)

Back up first: `cp camera_schedule.yaml camera_schedule.yaml.before_s22_soak_<UTC>`.

```yaml
capture_mode: "video"            # unchanged

enforce_time_window: false       # the soak runs around the clock
enforce_spotter_time_window: false

progressive_jpeg:
  max_run_time_min: 8            # WAS 16. The cycle budget must sit INSIDE the
                                 # 600 s power window: ~60 s boot + 480 s + halt

power_halt:
  enabled: true                  # WAS false: never let the ebox hard-cut a running Pi
  dry_run: false

video_tx:
  enabled: true
  duration_s: 5
  lead_in_s: 2
  output: "480x270"
  fps: 10
  message_cap: 126
  keyframe_repeat_max: 30
  preset: "medium"

transmit_phase:
  enabled: true
  post_boundary_guard_s: 60      # 30 s lost a keyframe on 2026-09-21; 60 s was clean (n=1)
```

Verify before leaving it: `python3 -u rc_progressive_jpeg.py --print-config`
must show `[VTX] video_tx: enabled=True …`, `transmit_phase (C2): ON … guards=60/20s`,
`power_halt: enabled=True dry_run=False`, `cycle budget: max_run_time_min=8`.

### Does it fit the 10-minute window?

| Stage | Measured / bound | Source |
|---|---|---|
| boot → runtime start | ~60 s, **not measured** | assumption — first soak cycle measures it |
| Spotter time + record 7 s + fit | ~35 s | bmcam004 2026-09-21 |
| lane wait | 0 – 300 s | `transmit_phase.max_wait_s` |
| burst: 126 chunks + ≤30 keyframe repeat + 2 | ≤ 160 s | 1 msg/s |
| halt | ~15 s | rc_power_halt docstring |
| **worst case** | **~570 s of 600** | |

Worst case only fits because the cycle budget (480 s) makes the runtime SKIP a
lane wait it cannot afford and send unscheduled (logged `[VTX][WARN] skipping the
… lane wait`). Those cycles are data, not failures: they are exactly the
bad-phase sends whose loss we want to see. Do not raise `message_cap` above
126 for this run.

### The period walks the phase

960 s mod 300 s = 60 s, so each cycle starts 60 s later on the 5-minute grid:
five distinct phases, each visited ~2 times in 3 hours. A 15-minute period would
have tested one phase eleven times.

## 2. Ebox duty cycle (Spotter console, via the monitor's `cmd.txt`)

From skill `spotter-usb-power-cycle` — **read it first**; `<bridge>` is the
bridge node id from `bm info` / the `power |` telemetry lines.

```text
bridge cfg set <bridge> s u sampleIntervalMs 960000     # 16 min period
bridge cfg set <bridge> s u sampleDurationMs 600000     # 10 min on
bridge cfg set <bridge> s u bridgePowerControllerEnabled 1
bridge cfg commit <bridge> s
```

SPOT-33507C's bridge node id is `c3c564b91856226c` (skill, bench IDs) — confirm
it on the console after the firmware update before using it.

- Record the BEFORE values (`bridge cfg status <bridge> s`) in the run manifest.
- **ALWAYS read back** after the commit (`bridge cfg status <bridge> s` or the
  `Bridge network config:` line): plain `bm cfg` fails silently.
- **The commit re-inits the bridge and re-evaluates power IMMEDIATELY — it can
  cut bus power on the spot.** Make sure the camera is halted or idle first
  (no recording, no send in flight), never mid-cycle.
- To stop the soak: `bridgePowerControllerEnabled 0` + commit (bus always on).

**Recovery:** with the halt enabled the Pi is only reachable for the first
minutes of each window. `ssh pi@bmcam004 'touch ~/BM_Devel_Pi/NO_HALT'` during a
window keeps it up (the halt is skipped with a loud WARN) — do that BEFORE
turning the duty cycle on, remove it only once one full cycle has been watched.

## 3. Monitoring Pi on the Spotter USB console

The USB console is the ONLY place a queue-full loss is visible; the camera gets
no error. Any Pi with the repo checked out:

```bash
ls /dev/serial/by-id/ | grep SPOT                 # expect exactly SPOT-33507C
python3 -u tools/spotter_serial_monitor.py --only SPOT-33507C \
    --log-root ~/spotter_logs                     # NO --sync-min: note sync stays OFF
```

Run it under `tmux`/`systemd` so an ssh drop cannot stop it; confirm
`~/spotter_logs/SPOT-33507C/console_<YYYYMMDD>.log` grows and rolls at UTC
midnight. Caveat to record in the results: an attached USB console may itself
change Spotter behaviour (sleep, cellular timing) relative to a fielded unit.

## 4. What gets recorded per cycle

`tools/bm_video_soak_report.py` joins four independent records into one row per
cycle — the camera's own log, the Spotter console, Sofar's raw messages, and the
staging backend:

| Column | From |
|---|---|
| power-on → runtime start, Spotter time read | camera `cron_logs/rc_cycle_*.log` |
| recorded clip, fit (msgs, % of budget, pass-2 tries, trimmed frames) | camera log |
| lane plan (phase, wait, skipped?) | camera log `[PHASE]` |
| burst start/end, sent/planned, keyframe repeat | camera log `[VTX] transmit done` |
| Spotter: queued count, **queue-full count + window**, own-transmission times | console log |
| Sofar: unique chunks received, missing indices, START/END | Sofar API |
| backend: media id, complete?, playable seconds, ingest time | staging API |
| glass→screen latency | capture time vs backend `received_at_utc` |

## 5. Pass / fail — set BEFORE the run

| Metric | Pass | Why |
|---|---|---|
| cycles that boot, run and halt cleanly | 11 / 11 | any stuck or un-halted cycle is a field blocker |
| SD card / filesystem errors after 11 power cuts | 0 | `dmesg`, `fsck` state, recorder ring intact |
| clips complete at Sofar | report, no threshold yet | this run MEASURES loss vs phase |
| keyframe-protected clips that still fail to decode | 0 | a lost keyframe chunk AND its repeat |
| every loss explained by a console event | yes | unexplained loss = we do not understand the queue yet |

## 6. Put something worth filming in front of the lens

Lights ON and a running clock/timer in frame: capture time is then readable IN
the clip on the dashboard, and the budget is not spent encoding sensor noise
(the 2026-09-21 night clip was a dark room).

## 7. Not covered by this soak (field blockers regardless of the result)

- No BM command daemon in `video_tx` mode — a fielded unit could not be
  reconfigured or switched off over Bristlemouth.
- No scene-aware or power-window-aware budget (next sprint).
- Indoor cellular, one Spotter, one camera on the bus.
