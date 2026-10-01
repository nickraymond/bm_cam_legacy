# S6b HIL — bench handoff (2026-10-01 ~05:25Z, from the S5 session)

The next session owns the bench and runs the S6b HIL test: **backend auto-send of heal
commands** (PLAN_S6.md §9.14 H3 → H4 → optional H5, then §5 step 8 R5 24 h loop).

## Rig state at handoff

| | bmcam003 | bmcam004 |
|---|---|---|
| Spotter / bridge | SPOT-33507C / c3c564b91856226c | SPOT-31593C / 0e582dd12c1e1480 |
| bus | HELD on (`bridgePowerControllerEnabled 0`) | HELD on |
| runtime | development 3c1801d (W9) | 3c1801d |
| config | 580ce986 base, overlay `{mode.run: stay_on}` (cmd 52101) | same (cmd 62101) |
| process | stay_on, trigger-only (interval 0), heartbeat 300 s, video transmit | same |
| cron | ARMED (`@reboot … rc_run_capture_cycle.sh`); backup `/home/pi/w9proof/backup/crontab_ARMED.txt` | same |

nereus000 (pi@192.168.1.45): spotter-monitor active; **bm-heal-driver STOPPED** (keep it
stopped: one heal sender); conductor `/home/pi/bm_bench_conductor.py` = development (has
`--no-heal`), NOT running. The transient unit name is in `failed` state from the last 1 h run:
`sudo systemctl reset-failed bm-bench-conductor` before reusing it. Admin token:
`~/.config/nereus/heal_driver.env` (never leaves nereus000). **No Sofar token on nereus000.**

Staging (nvd): S6b PRs #69–#73 merged (staging 715d3ef); auto-send OFF (no Render env yet).
H1 PASS (S6b session, from `h1/*.json` here). Last API heals: BMCAM_004 100072 at
2026-09-30T03:27:28Z, BMCAM_003 100078 ~2026-09-29T19:42Z (both ≫ REASK_S 5400 s).
H2 (Sofar-lane calibration): S6b recommends skipping; **Nick's decision pending**.

## Start the trigger loop (H3)

```bash
ssh pi@192.168.1.45 'sudo systemctl reset-failed bm-bench-conductor; sudo systemd-run --unit=bm-bench-conductor --uid=pi --gid=pi --property=WorkingDirectory=/home/pi /usr/bin/python3 -u /home/pi/bm_bench_conductor.py --rig SPOT-33507C=BMCAM_003=bmcam003 --rig SPOT-31593C=BMCAM_004=bmcam004 --hours 24 --min-interval 30 --drain-min 120 --no-heal'
```

```bash
ssh pi@192.168.1.45 'journalctl -u bm-bench-conductor -n 20 -o cat'
```

## Restore after the S6b test

1. Stop the conductor; Nick removes `BM_HEAL_AUTOSEND` on Render (cron).
2. Each unit: console `{"id":N,"c":"reset","k":["mode.run"]}` → exit 72 → per_boot wake, halt.
3. Unit halted + disarmed? (it is ARMED now: for the schedule restore, follow
   `runs/s5_console_20260928/restore_schedule.sh`: halted first; the stub boots it armed, the
   script halts it cleanly). Read back 1 / 3600000 / 600000.
4. bm-heal-driver back (back up `state.json` first) — only if auto-send is off.
5. Then hand the bench to the "Finish S5 details, then take the rig for 24 h gate" session.

## Gotchas from this run

`cd … && nohup setsid … &` over ssh never returns (use `cd …; nohup …`); `pkill/pgrep -f`
can match your own shell (bracket patterns); the wrapper does not forward its args; the Bash
tool expands `\uXXXX`; zsh does not word-split; `*.log` is gitignored (`git add -f`); the
Spotter USB console takes ≤ 256 B lines; SPOT-33507C's cellular queue overflows often (F1);
bmcam004 lost bus power 3× on 2026-09-30 (cause unknown).
