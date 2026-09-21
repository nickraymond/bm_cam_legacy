# Sprint24 kickoff — outdoor HIL baseline (video uplink + command downlink)

You are starting Sprint24. Two earlier sessions did the groundwork; Nick has
minted the plan. The design is decided — do not re-litigate it. Owner and
approval gate: **Nick**. Give him your plan before any work and wait for his go.

REQUIRED READING, in order:

1. `CLAUDE.md` (manifesto; branching — never commit to `main`/`development`).
2. `sprints/Sprint24_outdoor_hil_baseline/PLAN.md` — goal, decisions D1–D7,
   ordered work, pass/fail.
3. `sprints/Sprint22_video_over_spotter/TRACKER.md` (every measurement) and
   `SOAK_RUNBOOK.md` (YAML delta, window arithmetic, bridge commands, NO_HALT
   recovery). NOTE: the runbook's lane guard is SUPERSEDED by Sprint24 D3.
4. `sprints/Sprint23_remote_msg_latency/RESULTS.md` on branch
   `feature/sprint23-remote-msg-latency` (local, unpushed — ask Nick / the
   Sprint23 session to push it or open a PR first).
5. `docs/bm_media_wire_contract.md` (rev 3) — the wire is frozen.
6. Skills: `nereus-spotter-sd-analysis` (§10 system logs, the duplicate-`log/`
   trap), `spotter-usb-console-capture`, `spotter-usb-power-cycle`,
   `spotter-health-check`.
7. Code you will touch: `BM_Devel_Pi/rc_video_tx.py`, `rc_transmit.py`
   (`transmit_video_clip`), `rc_command_hooks.py`, and how
   `rc_progressive_jpeg.run_cycle` wires the daemon (the pattern to port).

FIRST TASKS:
- PLAN step 1: the 15-minute indoor SD logging check on both Spotters.
- PLAN step 2: the command daemon inside the `video_tx` cycle (the only code
  bite). Branch `feature/sprint24-hil-baseline` off `development`. The golden-wire
  test (`tests/test_rc_video_tx.py`) must stay byte-identical.
- Then deploy the SAME build + YAML to bmcam003 and bmcam004, one watched cycle
  each, duty cycle on, outdoors, ≥ 3 h, report.

STANDING RULES: bmcam003 + SPOT-31593C and bmcam004 + SPOT-33507C are in scope;
nothing else. `--only <SPOT-ID>` on every monitor, never open a port twice.
Cellular only. `note sync` OFF. No Spotter reboots or `bridge cfg commit` during
the run. `log flush` before pulling a card. Never run fsck / First Aid on a
Spotter card. This repo is PUBLIC — no bench footage, no tokens, in commits.

Report to Nick at each milestone: What happened / What I learned / What's next,
two bullets each, comparisons in tables.
