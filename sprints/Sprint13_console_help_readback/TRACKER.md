# Sprint13 — Tracker

Tick a box only when an artifact proves it. IN PROGRESS since 2026-08-01
(branch claude/sprint13-console-help-cfg-966167, off development 5d16747
= Sprint12 PR #32 merge). Chunked rollout: 1 tables/renderer/tests →
2 transport (after T1) + daemon wiring → 3 bench bmcam003 → 4 docs +
Nick sign-off.

## 0. Setup
- [x] Sprint12 PR #32 confirmed merged into development before branching
- [x] Required reading (SPEC, Sprint12 DESIGN/AUDIT/DEV_LOG, command
      reference, console-style memory) before writing anything
- [x] Content drafts iterated with Nick (2026-08-01): jazzy v2 layout,
      ROI pixels-first + 2 reef-test crops, exposure-bias naming, win 12
      production spec, hlt customer labels, awb custom preset dropped,
      tmz command added

## 1. Tables v5 + renderer + bindings (chunk 1)
- [x] TABLES_VERSION 4 → 5; help/cfg QUERY_COMMANDS; tmz settings
      command; roi 5/6; win 12/5/8/16; awb 0-2; customer labels;
      COMMAND_INFO metadata (command_tables.py)
- [x] command_help.py: render_help() + render_cfg() — pure, generated,
      ASCII, <= 72 chars (test-enforced)
- [x] tmz overlay + timezone_override gate plumbing (D-S12-6 pattern;
      command_bindings.py, spotter_time_sync.py, rc_command_hooks.py)
- [x] roi output-width clamp (no upsample; D-S13-7)
- [x] parse_command: v optional for help/cfg
- [x] Profiles: max_run_time_min 16 → 12 in all 5 profiles + repo YAML;
      txd 1.0 confirmed everywhere
- [x] Unit tests: 542 green (was 507) — content-complete help, example
      round-trip, cfg parity + source column, query semantics + dedupe,
      tmz overlay/gate, roi clamp, version fallout

## 2. Transport + daemon wiring (chunk 2)
- [x] Transport DECIDED by Nick (2026-08-01 overnight): console write
      via `spotter/printf`, per the Sofar SDK's bm_serial — SD+cat
      rejected. D-S13-9; clean trio in bm_serial.py
      (tx=cellular / log=SD / print=console)
- [x] `bm_serial.spotter_print()` (frame mirrors spotter_log, empty
      fname; byte-layout tests) — DERIVED layout, bench echo proof owed
- [x] Daemon: query responses queued + drain_console at idle points /
      listen window / pre-halt; duplicate id = ack only, NO re-print;
      render failure never kills processing; send failure requeues
      (tests; 550 green)
- [x] query_render_fn wired over the RESOLVED settings
      (make_query_render_fn — cfg can never disagree with
      --print-config); mock_mote decodes spotter/printf frames
- [ ] BENCH: spotter/printf echoes on v2.16.6 (the D-S13-9 proof gate);
      line pacing measured (0.05 s provisional)
- [ ] Fresh Spotter `help`/`post` capture (D-S13-8 format cross-check)

## 3. Bench validation (bmcam003, USB console)
- [ ] `help` prints the full reference, readable, no wrapping (terminal
      log artifact)
- [ ] `cfg` matches --print-config resolved values incl. an active
      command override with correct source column (send twn 1 → cfg →
      twn 0 → cfg; artifacts)
- [ ] tmz smoke on hardware (tmz 1 → gate log shows override → tmz 0)
- [ ] Unit left field-normal: re-armed from saved crontab backup, real
      halt, daemon on
- [ ] win 12 sanity: one full cycle on the 12-min budget completes clean

## 4. Sign-off + docs
- [ ] **Nick reads help/cfg on his terminal and signs off on customer
      readability (gate is explicitly his)**
- [ ] docs/bmcam_command_reference.md updated (tables v5: tmz, awb drop,
      win order, points to on-console help)
- [ ] bmcam-hotspot-update skill note (help/cfg exist on-console)
- [ ] REMOTE_CONFIG_AUDIT.md annotated: timezone moved C → commandable
      (D-S13-4)
- [ ] SPEC.md updated with T1 results; DEV_LOG current
- [ ] PR → development, suite green

## Known hazards carried in
- bmcam000: Sprint12 §5 remote validation never completed; a `twn 2`
  (id 3001) may still sit in the SPOT-31593C cloud mailbox. DO NOT touch
  bmcam000 without checking the Sofar pending queue first. Not needed
  for this sprint's gates.
- bmcam003 is ARMED with real halt: console `reset` is the wake lever;
  disarm (crontab backup) before bench work, re-arm after.
