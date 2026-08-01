# Sprint12 — Tracker

Tick a box only when an artifact proves it. Where a box is partially done, say
what is missing rather than ticking it. IN PROGRESS since 2026-07-31
(branch claude/sprint12-remote-config-c9a9c1). Rolling out in verified
chunks (Nick 2026-07-31): 1 tables/state → 2 overlay/orchestrator →
3 GUI/profiles/docs → 4 bench bmcam003 → 5 deploy + remote validation.

## 0. Setup
- [x] Branch off `development` (claude/sprint12-remote-config-c9a9c1;
      contains origin/development tip fed413d)
- [x] Read SPEC.md + Sprint10/11 command-daemon code paths
      (`command_tables.py`, `command_bindings.py`, `command_state.py`,
      `rc_command_hooks.py`) before writing anything
- [x] Confirm all four units' current state before changing anything
      (Nick 2026-07-31: bmcam000 + bmcam003 both RUNNING, production
      window 10:00–15:00 ET; plan is to deploy Sprint12 build to both
      for the final test, then open the window remotely via `twn`.
      bmcam001/002: Florida, hotspot-only, updated to main 0d03a62
      2026-07-31, power_halt disabled — unchanged this sprint)

## 1. `hlt` command
- [x] `HLT_TABLE` in command_tables.py, tables_version 2 → 3
      (tests/test_command_tables.py TestSprint12Tables, suite green)
- [x] Binding + persisted state (`bm_command_state.json` schema addition)
- [x] Overlay application: `power_halt` enabled/dry_run from state, next
      boot (TestSprint12HltOverlay; next-boot proof in
      TestSprint12AckBeforeHalt)
- [x] `hlt 0` removes the override (YAML governs)
      (test_hlt_0_touched_leaves_yaml_governing)
- [x] **Ack-before-halt ordering test** — off-device proof
      (TestSprint12AckBeforeHalt) AND hardware proof:
      runs/sprint12_bench_20260731/cycle4_hlt2.log — applied id=2004,
      2 acks on the wire, THEN halt_initiated; cycle halted with boot
      settings (real), commanded dry-run applied next boot.
- [x] Boot log states applied halt mode unambiguously
      ([RC] power_halt line + source= stamp + stranding warnings)
- [x] Unit tests (table, persistence, overlay, version mismatch) —
      257 green across affected suites (chunks 1+2 commits)

## 1b. `trg` command (added 2026-07-31, Nick-approved — SPEC Feature 3)
- [x] `TRG_TABLE` (capture / capture+send / reference sends riding
      SRC_TABLE) in command_tables.py (tests green)
- [x] `pending_trigger` state slot: arm/cancel/consume-once, crash-safe
      clear-before-service, v2-file compatibility
      (tests/test_command_state.py TestPendingTrigger)
- [x] Orchestrator: next boot consumes trigger — window gate bypassed
      (one-shot), trg 1 = capture-only path, trg 3/4 = one-shot src
      (service_pending_trigger + apply_trigger; end-to-end through real
      main() in TestSprint12TriggerEndToEnd)
- [x] Unit tests for orchestrator consumption (chunk 2 commit)
- [x] Bench: `trg 3` — YAML window restored (would block), one-shot
      gate bypass, camera skipped, reef reference 192/192 COMPLETE
      (runs/sprint12_bench_20260731/cycleD_trg3.log). `trg 2`
      (live-camera variant) deferred to chunk 5 remote validation —
      same code path minus the src substitution.

## 2. `twn` command
- [x] `TWN_TABLE` presets in command_tables.py (tests green)
- [x] Binding + persisted state + overlay (`transmit_window` from state)
- [x] `twn 0` removes the override
      (test_twn_0_touched_leaves_yaml_governing)
- [x] Window interpreted in unit's configured timezone (no tz change —
      gate override passes only start/end; timezone stays YAML;
      TestSprint12WindowGateOverride uses America/New_York)
- [x] Test: unit booted outside YAML window transmits after `twn 2` (wide)
      — off-device (test_twn_wide_override_opens_the_window + D14
      plumbing tests). On-unit artifact owed in chunk 4/5.
- [x] Unit tests (chunk 2 commit)

## 3. Daemon enabled in production configs
- [x] `bm_commands.enabled: true` in all four device profiles + template
      (template gained the island — it had none; all 5 parse +
      validate_schedule clean, chunk 3 commit)
- [x] Confirm cycle byte-behavior otherwise unchanged (D14 parity check)
      — enabled-but-untouched overlay changes nothing
      (test_untouched_state_changes_nothing); integration suites run
      enabled with stock state and produce the normal START/chunks/END
      wire; gate override key absent unless twn commanded
      (TestSprint12GateKwargsPlumbing)
- [x] Document expected command latency (hourly [MS] drain; re-send
      doctrine) — profile comments, template island comment,
      docs/bmcam_command_reference.md

## 4. Bench validation (bmcam003, USB path) — DONE 2026-08-01Z,
##    artifacts in runs/sprint12_bench_20260731/ (RESULTS.md + manifest)
- [x] `hlt 2` via Spotter console `bm pub bmcam/cmd ... 1 1` → applied on
      next boot (cycle4_hlt2.log + cycle-5 boot log: dry_run=True
      source=command hlt=2; box stayed up)
- [x] `twn 2` → window opens next boot (cycleC_twn2_transmit.log:
      "Within transmit window 00:01-23:59 (command override)" at
      22:35 EDT; real image q80/142 msgs complete). Baseline skip_win
      at 22:34 EDT captured first.
- [x] `hlt 0` + `twn 0` → YAML behavior restored (restore_verification.txt:
      state hlt=0/twn=0/pending null; print-config both source=yaml).
      Bonus: twn 0 + trg 3 + hlt 0 all delivered via the 150 s C4 tail —
      the field-realistic arrival path (finding 006) — with dedupe
      handling 18 duplicate re-sends.

## 5. Remote validation (bmcam000, Sofar Command API path)
- [ ] Deploy Sprint12 v4 build to bmcam000 + enable daemon + re-arm
      — IN FLIGHT at PR time: bmcam000 rides a DERP relay and its 15/45
      duty cycle gives ~1 min of SSH per hour; relay-tolerant watcher v2
      is hunting. (v1 watcher's 2 s ConnectTimeout could never complete
      a relayed handshake — diagnosed 2026-08-01 ~04:00Z.)
- [ ] CELLULAR PATH SKIPPED per Nick 2026-08-01 (unit indoors); replaced
      by a full USB console command sweep on bmcam000: SET PHASE 12/12
      PASS (every v4 command applied + acked; 88 acks; artifacts in
      runs/sprint12_bench_20260731/). Factory-reset phase INCOMPLETE —
      blocked by an OPEN BUG: SPOT-31593C console bm pub → Pi delivery
      died after the 05:52Z reset (see DEV_LOG session-end entry for
      the full diagnosis + next experiment). bmcam000 left disarmed,
      safe-idle, v4.

## 6. Wrap
- [x] GUI: new commands in tools/bm_command_gui — zero code change needed
      (D9: dropdowns generated from tables; hlt/twn/trg + labels verified,
      tables v3 shown). sofar_send_command.py likewise table-driven;
      dry-run verified: `--cmd trg --value 3` → 50-byte console line
- [x] Docs + `bmcam-hotspot-update` skill updated (halt/window no longer
      SSH-only): docs/bmcam_command_reference.md (new, operator-facing),
      skill Phase 0 "try a remote command first", REMOTE_CONFIG_AUDIT.md
      (Sprint13 input: 5 preset candidates + cfg readback à la `post`)
- [ ] PR → `development`, gates green — PR opened 2026-08-01 for Nick's
      review; §5 (bmcam000 remote path) completes in parallel and will
      be appended to the PR before merge. Follow-on sprints specced per
      Nick's close-out: Sprint13 (console `help` + `cfg` readback,
      customer-facing) and Sprint14 (overnight command soak / ROI sweep
      on the reef reference — deliberately parked until Sprint13 is
      merged AND Nick has tested `help`).
