# Sprint13 — Dev log

## 2026-08-01 (session 1) — chunk 1: tables v5, renderer, tmz, tests

- Pre-flight: PR #32 merge into development verified (5d16747); branch
  sits on it. The T1 partial results (fprintf = SD-only on v2.16.6, no
  console echo; SD path mystery) were NOT committed anywhere — they
  arrived via the kickoff brief; recorded in SPEC/DESIGN now.
  Sprint12 §5 (bmcam000 remote path) confirmed never completed —
  mailbox hazard noted in TRACKER, unit untouched this sprint.
- bm_serial.py audit (Nick asked): exactly two publish methods exist —
  spotter_tx (spotter/transmit-data: console-visible but CELLULAR
  QUEUE, costs quota) and spotter_log (spotter/fprintf: SD-only per
  T1). No console-print method in the repo; `spotter/printf` (bm_core's
  console printf, public-SDK knowledge) is the chunk-2 probe.
- Content drafts iterated with Nick to an approved v2 layout (one value
  per line, boxed cfg tables, quick actions with plain-English intent).
  His table changes, all in tables v5 (D-S13-4..7): roi pixels-first
  labels + 800x450/640x360 reef-test crops; exposure-bias naming; win
  0=12 min AS PRODUCTION SPEC (profiles moved 16→12 in the same
  commit); hlt customer labels (power savings / developer mode); awb
  custom preset DROPPED (untested values don't ship); tmz command
  (LA/NY/UTC) — audit category-C revision, gate override plumbed via
  the D-S12-6 pattern.
- New: command_help.py (render_help 143 lines / render_cfg boxed
  3-group table, both pure + table-generated, ASCII <= 72 enforced);
  QUERY_COMMANDS class (help/cfg — dedupe-id-only record, v optional).
- Caught in review: sub-1000px roi crops would have CRASHED the overlay
  (output_size_for_crop raises on upsample) — clamp added + tests.
  Also cfg column overflow on long trigger labels — generic clip added.
- Tests: 542 green (was 507) at chunk-1 commit. New: content-complete help (every
  command + every index + every note), example lines round-trip
  parse_command (a help example can never go stale), cfg source-column
  semantics incl. index-0, query no-state-change + dedupe, tmz
  overlay/gate/kwargs, roi clamp. Fallout updated: command set/version
  pins, ack byte-pin (+tmz), win reorder, awb removal (gains path kept
  covered via patched temp entry), repo-YAML 12-min pins.

## 2026-08-01 (session 1, overnight) — chunk 2: console transport

- Nick (before bed): live demo in the AM — he will trigger trg 2 (camera
  capture+send) and trg 3 (reef transfer) himself as the acceptance test
  before approving the PR. Network access to this session STOPPED for the
  night: no SSH deploy, no Sofar API; console reset also skipped (no
  deploy possible → no reason to power-cycle; zero SD-corruption risk
  overnight). Deploy to bmcam003 becomes the pre-demo step.
- Nick decision (D-S13-9): NO SD+cat — add the Sofar SDK's console
  write to our bm_serial.py. Done: `spotter_print()` (spotter/printf,
  fname_len=0, frame mirrors spotter_log). No local SDK copy on this
  Mac + no network → layout DERIVED not copied; bench echo on v2.16.6
  is the proof gate, diff vs real SDK when network returns.
- Daemon wiring: query responses render via make_query_render_fn (over
  the RESOLVED settings dict) → console queue → drain_console at idle
  drains, listen windows, and pre-halt (help must beat the power cut).
  Duplicate id acks WITHOUT re-print (D-S13-10). mock_mote now decodes
  spotter/printf frames for off-device runs.
- Tests 550 green (+8): frame byte-layout, console-vs-cellular
  separation, dedupe no-reprint, send-failure requeue, renderer-failure
  isolation, hooks flush ordering.
- docs/bmcam_command_reference.md → tables v5 (tmz, awb drop, win
  order, exposure-bias wording, on-console help pointer).

## 2026-08-01 (session 1, late overnight) — HIL rehearsal on bmcam003

Nick (before bed): rehearse the ENTIRE demo runbook so his morning demo
is a re-run of a proven sequence; 2 rehearsal quota images authorized.
Push unblocked by Nick (classifier denies git push without prompting —
he ran the first push + added the allow rule); PR #33 opened.

Deploy path per Nick: Pi pulls from git (pi-deploy flow) + known-good
rc_field_update wrapper. Deployed 3x as fixes landed (79fc3c9 →
4992c10 → 7468bff), profile YAML installed (win 12 live on-unit).

Rehearsal verdict: ALL RUNBOOK STEPS PASS — full detail in
runs/sprint13_bench_20260801/RESULTS.md. Headlines:
- T1 PROOF: spotter/printf echoes on v2.16.6 — 123/123 help lines
  intact on the USB console, zero drops at 0.05 s/line.
- cfg on-console: boxes aligned, source column flips proven (hlt, twn),
  live next-boot view proven (hlt 2 → cfg seconds later correct).
- trg 2: live camera 105/105 COMPLETE, window bypassed.
  trg 3: reef 192/192 COMPLETE, camera skipped, trigger self-cleared.
- Three real bugs found+fixed by the rehearsal (manifest gap, bench
  listen tail, cfg frozen-view) — each with tests; suite 555 green.
- One operational scar: overlapped manual cycles (no flock) let the
  PREVIOUS cycle's real halt kill the box mid-run — diagnosed via
  Spotter power telemetry (0.034 A awake / 0.018 A halted) + cycle
  logs. All manual cycles now take cron's flock; runbook updated.
- End state: state file all-zeros + no pending, hlt 0 → yaml real halt,
  re-armed from backup, box up. Demo-ready.

Open at handoff: Sofar backend rows for the 2 rehearsal images (13-30
min lag; re-poll pre-merge). tmz on-hardware smoke optional in demo.
Out-of-window command-deafness flagged as a Sprint14 doctrine question.

## 2026-08-01 (day, Nick remote) — dev state + console formatting

- Nick's morning demo hit the OLD formatting (double-spaced help): his
  CoolTerm owned the USB port from 14:56 (Spotter power-cycle), so
  overnight monitor was dead. Per Nick: took the console back (CoolTerm
  killed after graceful quit failed), fresh monitor from this worktree
  (runs/sprint13_bench_20260801/spotter_logs/).
- Formatting fixes (3d00676): spotter_print drops the payload newline
  (Spotter console adds its own -> was double-spacing every line);
  blank spacer lines filtered at the transport boundary. VERIFIED on
  hardware: help_echo_singlespaced_cycle9.txt — dense, 123 content
  lines, no blank stamped rows.
- Timestamp prefix (epoch + node id): CANNOT be removed — probes proved
  v2 print_time=0 renders identically to v1 on v2.16.6; the prefix is
  Spotter-side console rendering. Recorded as a known cosmetic.
- Dev state per Nick (console commands, dev_mode.sh tool SKIPPED but
  committed for later, 0e56e84): crontab disarmed (backup
  crontab_armed_devpause_20260801.txt), hlt 3 commanded via bm pub
  (12/12 acked), box up >90 min no halt, bus always-on. Two scheduled
  in-window images were spent by armed wake cycles during the morning
  (14:58 Nick power-cycle, 15:53 my reset) — flagged to Nick.
- bmcam003 END STATE: DEVELOPER MODE — disarmed, hlt=3, box up,
  build 0e56e84, cfg shows "halt OFF (developer mode) | command hlt=3".

## 2026-08-17 — wrap session (close-out toward PR #33 merge)

- Merged development 3a1153d into the branch (clean — docs/skills only:
  PR #34 photo-check skill, PR #35 Sprint15 video-pivot planning).
  Suite: 555 tests OK (1 skipped).
- Docs finalized: command reference timezone-list fix, hotspot skill
  help/cfg note, AUDIT D-S13-4 annotation, SPEC T1 FINAL paragraph.
- Sofar re-poll (owed from §3): sensor-data sweep Jul31–Aug5 shows
  SPOT-33507C's LAST cloud row at 2026-07-31T19:00Z — the entire Aug 1
  bench (trg 2 105/105, trg 3 192/192, both console-COMPLETE) never
  reached the cloud. Cellular sync stalled that evening. Logged as a
  carried hazard; not a Sprint13 gate.
- tmz hardware smoke: SKIPPED for close-out; fold into the next
  hands-on bench session.
- Sign-off artifact (rendered help + cfg) delivered to Nick; the
  readability gate remains his, then PR #33 merges.
- Context: earlier today bmcam000 was recovered (clock-skew root cause,
  now on development 3a1153d, disarmed) — see TODO-BM-011 and the
  Sprint15 planning PR #35.
