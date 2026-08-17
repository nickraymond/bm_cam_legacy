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
- [x] BENCH: spotter/printf echoes on v2.16.6 (the D-S13-9 proof gate) —
      123/123 help lines intact, node-id prefixed, zero drops at 0.05 s
      pacing (runs/sprint13_bench_20260801/help_echo_cycle2.txt)
- [x] Fresh Spotter `help`/`post` capture
      (runs/sprint13_bench_20260801/spotter_{help,post}_capture.txt)
- [x] Rehearsal-found fixes: manifest gap + import hardening (4992c10),
      bench listen tail (2088064), cfg live next-boot view (7468bff)

## 3. Bench validation (bmcam003, USB console) — HIL rehearsal DONE
##    2026-08-01, artifacts runs/sprint13_bench_20260801/ (RESULTS.md)
- [x] `help` prints the full reference, readable, no wrapping
      (help_echo_cycle2.txt; dedupe: 1 print for 4 sends)
- [x] `cfg` matches resolved values incl. active command override with
      correct source column (hlt 2 live view cycle3; twn 1 flip cycle6;
      all-yaml restore cycle8) — and answers with the live NEXT-BOOT
      view for commands applied in the same window (fix 7468bff)
- [x] trg 2 live capture+send rehearsed: gate bypassed, 105/105
      COMPLETE (cycle5); trg 3 reef: 192/192 COMPLETE, camera skipped,
      trigger self-cleared (cycle7). Sofar re-poll DONE 2026-08-17
      (sensor-data sweep Jul31–Aug5): the Aug 1 bench images NEVER
      reached the cloud — SPOT-33507C's last Sofar row is
      2026-07-31T19:00Z. Console-COMPLETE proven, cloud delivery not;
      Spotter cellular sync stopped that evening (bench antenna/power
      suspected). Follow-up item, NOT a Sprint13 gate (gates are
      console-side; trg/cellular machinery shipped with Sprint12).
- [ ] tmz smoke on hardware (tmz 1 → gate log shows override → tmz 0)
      — SKIPPED for close-out (2026-08-17 wrap): unit tests + gate
      plumbing tests only; fold into the next bench session on the
      unit (Sprint15 work will put hands on hardware anyway)
- [x] Unit left field-normal: re-armed from
      crontab_armed_sprint13_backup_20260801T065232Z.txt, hlt 0
      restored (yaml real halt), state all-zeros, box up
      — SUPERSEDED by the later 2026-08-01 dev-state session (see
      DEV_LOG): bmcam003 END STATE is DEVELOPER MODE — disarmed
      (backup crontab_armed_devpause_20260801.txt), hlt=3 commanded,
      box up on build 0e56e84
- [x] win 12 sanity: all cycles ran on the 12-min budget (720 s) clean

## 4. Sign-off + docs
- [ ] **Nick reads help/cfg on his terminal and signs off on customer
      readability (gate is explicitly his)** — sign-off artifact
      (rendered help + cfg exactly as the console shows them) delivered
      to Nick 2026-08-17; awaiting his word
- [x] docs/bmcam_command_reference.md updated (tables v5: tmz, awb drop,
      win order, points to on-console help; 2026-08-17 fix: timezone
      removed from the "not remotely configurable" list)
- [x] bmcam-hotspot-update skill note (help/cfg exist on-console;
      "have them run `help` first" doctrine)
- [x] REMOTE_CONFIG_AUDIT.md annotated: timezone moved C → commandable
      (D-S13-4)
- [x] SPEC.md updated with T1 FINAL (spotter/printf proven, D-S13-9);
      DEV_LOG current through the 2026-08-17 wrap
- [ ] PR #33 → development, suite green — development (3a1153d,
      incl. Sprint15 pivot planning) merged in 2026-08-17, 555 tests
      OK (1 skipped); merges after Nick's sign-off above

## Known hazards carried in
- bmcam000: RESOLVED 2026-08-17 — recovered to the tailnet (root cause:
  17-day clock skew broke TLS), updated to development 3a1153d, stale
  command overlay retired, left DISARMED. Mailbox check: the feared
  `twn 2` id 3001 was never actually sent (send log has no such entry).
- bmcam003 is in DEVELOPER MODE (disarmed, hlt=3, always up) since the
  2026-08-01 dev session — re-arm from
  crontab_armed_devpause_20260801.txt before any field-normal use.
- NEW: SPOT-33507C (bmcam003's Spotter) has pushed no Sofar rows since
  2026-07-31T19:00Z — cellular sync stalled; check antenna/power/queue
  next bench session before trusting cloud-side delivery tests.
