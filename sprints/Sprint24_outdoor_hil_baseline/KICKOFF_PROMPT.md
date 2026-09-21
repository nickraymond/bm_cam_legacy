# Sprint24 kickoff — outdoor HIL baseline: video uplink, two rigs

Rewritten 2026-09-21 by the Sprint23 session at Nick's request, after the
scope change below. It replaces the first version (video uplink + command
downlink) and the banner that was added on top of it. `PLAN.md` still carries
the original text under its own banner; where they disagree, this file and
that banner win.

You are starting Sprint24. Two earlier sessions (Sprint22 video-over-Spotter,
Sprint23 remote-command latency) did the groundwork; both are merged to
`development`. Nick has minted the plan. The design is decided — do not
re-litigate it. Owner and approval gate: **Nick**. Give him your plan before
any work and wait for his go.

GOAL: baseline, not optimisation. Characterise the pipeline from camera glass
to computer screen, OUTDOORS, on Spotter firmware v2.16.8, two rigs running the
SAME test as replicates, with as few variables as possible.

SCOPE CHANGE (Nick, 2026-09-21, after the plan was written):

- Remote commands are OUT of this baseline. Do NOT port the command daemon
  into the `video_tx` cycle (PLAN step 2 / decision D2). Do NOT run the
  1-ping/hour overlay (D5). No ack-rate metric. Drop "a ping acked" from the
  indoor watched cycle, and drop the "commands" row from pass/fail.
- Consequence: NO code change gates this test. Deploy `development` as it is.
- Why: Sofar confirmed held `bm` commands replay after a fixed 10 s grace
  period from BM network boot, not configurable. The Pi subscribes at ~38 s,
  so held commands are lost. The fix is a mote-side cache being worked with
  Sofar (TODO-BM-017). Commands return later as a short single-rig bench test.
- KEEP, because it is about the VIDEO: per cycle log `report_utc` (hourly
  `MS_Q_LEGACY` report), `rx_check_utc`, `queue_full_count_in_cycle`
  (`MS_Q_CELLULAR_ONLY is full`). That report minute is where both rigs lost
  chunks. Drop the columns `cmd_arrival_utc`, `held|live`, `ack_utc`.
- Free side-check while logging (unverified hypothesis, Sprint23 finding 10):
  report minute = the post-boot `MS_Q_LEGACY` report rounded up to the next
  5/10-min boundary, then every 60 min; every Spotter reboot moves it.

REQUIRED READING, in order:

1. `CLAUDE.md` (manifesto; branching — never commit to `main`/`development`).
2. `sprints/Sprint24_outdoor_hil_baseline/PLAN.md` — read the banner at the
   top first. Decisions D1, D3, D4, D6, D7 stand; D2 and D5 are dropped.
3. `sprints/Sprint23_remote_msg_latency/HANDOFF.md` — §2 is the bench state
   you inherit, with restore commands; §4 is the list of things that bite.
   Then `RESULTS.md` findings 6, 8, 9, 10, 11.
4. `sprints/Sprint22_video_over_spotter/TRACKER.md` (every measurement) and
   `SOAK_RUNBOOK.md` (YAML delta, window arithmetic, bridge commands, NO_HALT
   recovery). NOTE: the runbook's lane guard is SUPERSEDED by D3 (no lane
   guard).
5. `docs/bm_media_wire_contract.md` (rev 3) — the wire is frozen.
6. Skills: `nereus-spotter-sd-analysis` (§10 system logs, the duplicate-`log/`
   trap), `spotter-usb-console-capture`, `spotter-usb-power-cycle`,
   `spotter-health-check`.

HARDWARE IN SCOPE — nothing else:

- Rig A: bmcam004 + SPOT-33507C (bridge `c3c564b91856226c`), backend
  `BMCAM_004` on staging.
- Rig B: bmcam003 + SPOT-31593C (bridge `0e582dd12c1e1480`, camera node
  `53171fa3d81a8e6f`). Backend device id UNKNOWN — check before relying on it.

STATE YOU INHERIT ON RIG B (verify, do not assume — the Spotter rebooted 3x on
2026-09-21 for the SD-card wipe and nothing has been read back since):

- SPOT-31593C: bus power controller ON at 6 min on / 4 min off (Sprint23 test
  schedule). PLAN step 5 replaces it with 16 min / 10 on.
- bmcam003: `capture_mode: "progressive_jpeg"` (stills), command state
  `twn=2`, `hlt=3` (halt OFF by command — it overrides the YAML's
  `power_halt`). Config backup on the Pi:
  `camera_schedule.yaml.before_gui_20260921T062630Z`. The deploy in PLAN step
  3 must leave bmcam003 identical to bmcam004 — check the command-state
  overlay (`bm_command_state.json`) as well as the YAML, because `hlt=3` will
  silently defeat `power_halt.enabled: true`. bmcam004's overlay has not been
  looked at by Sprint23.
- Nick's Mac: no serial monitor and no tester server are running; both USB
  console ports were free as of 2026-09-21 ~17:50Z.

FIRST TASKS:

1. PLAN step 1: the 15-minute indoor SD logging check on both Spotters.
2. PLAN step 3: the SAME `development` build + SAME YAML on bmcam003 and
   bmcam004 (`video_tx.enabled: true`, `transmit_phase.enabled: false`,
   `power_halt.enabled: true`, `enforce_time_window: false`). `touch
   ~/BM_Devel_Pi/NO_HALT` until one full cycle has been watched on each.
   `software_sha.txt` must equal the deployed sha (START carries `sha=`).
3. One watched cycle per rig indoors (clip complete on staging), then the ebox
   duty cycle on both — ONE commit each, read back, before the run, never
   mid-run — then outdoors, ≥ 3 h, then the per-cycle report from
   `tools/bm_video_soak_report.py`, Spotter side sourced from the SD `log/`.

STANDING RULES:

- `--only <SPOT-ID>` on every monitor; never open a port twice; a monitor's
  `cmd.txt` is one slot, one writer.
- Cellular only (every send `cellular_only`). `note sync` OFF, monitors with
  no `--sync-min`.
- No Spotter reboots and no `bridge cfg commit` during the run: a commit
  forces the bus ON 120 s and triggers a report + mailbox check; a reboot
  moves the report minute.
- `log flush` before pulling a card. Never run fsck / First Aid on a Spotter
  card. Format cards FROM THE SPOTTER.
- The Spotter SD `log/` is THE record of the run (D6; Nick 2026-09-21: no
  monitoring Pi, no console logging needed outdoors). The USB console is for
  the indoor steps only — SD check, watched cycle, the one bridge commit. Do
  not make the outdoor run depend on a laptop staying awake.
- If this session is blocked from state-changing SSH on the Pis (Sprint23's
  was), do not work around it: tell Nick exactly which command you need and
  why, and let him run it or grant the permission.
- This repo is PUBLIC — no bench footage and no tokens in commits. Sofar token
  by env-var name only.

Report to Nick at each milestone: What happened / What I learned / What's next,
two bullets each, comparisons in tables.
