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
- [ ] Binding + persisted state (`bm_command_state.json` schema addition)
      — persistence DONE (test_command_state TestSprint12Settings);
      binding/overlay is chunk 2
- [ ] Overlay application: `power_halt` enabled/dry_run from state, next boot
- [ ] `hlt 0` removes the override (YAML governs)
- [ ] **Ack-before-halt ordering test** — command mid-cycle on a halting
      unit, ack reaches the uplink before the halt fires
- [ ] Boot log states applied halt mode unambiguously
- [ ] Unit tests (table, persistence, overlay, version mismatch)

## 1b. `trg` command (added 2026-07-31, Nick-approved — SPEC Feature 3)
- [x] `TRG_TABLE` (capture / capture+send / reference sends riding
      SRC_TABLE) in command_tables.py (tests green)
- [x] `pending_trigger` state slot: arm/cancel/consume-once, crash-safe
      clear-before-service, v2-file compatibility
      (tests/test_command_state.py TestPendingTrigger)
- [ ] Orchestrator: next boot consumes trigger — window gate bypassed
      (one-shot), trg 1 = capture-only path, trg 3/4 = one-shot src
- [ ] Unit tests for orchestrator consumption
- [ ] Bench: out-of-window `trg 2` captures + transmits next boot;
      `trg 3` sends reef reference with camera skipped

## 2. `twn` command
- [x] `TWN_TABLE` presets in command_tables.py (tests green)
- [ ] Binding + persisted state + overlay (`transmit_window` from state)
      — persistence DONE; overlay + gate override are chunk 2
- [ ] `twn 0` removes the override
- [ ] Window interpreted in unit's configured timezone (no tz change)
- [ ] Test: unit booted outside YAML window transmits after `twn 2` (wide)
- [ ] Unit tests

## 3. Daemon enabled in production configs
- [ ] `bm_commands.enabled: true` in all four device profiles + template
- [ ] Confirm cycle byte-behavior otherwise unchanged (D14 parity check)
- [ ] Document expected command latency (hourly [MS] drain; re-send doctrine)

## 4. Bench validation (bmcam003, USB path)
- [ ] `hlt 2` via Spotter console `bm pub bmcam/cmd ... 1 1` → applied on
      next boot (log artifact)
- [ ] `twn 2` → window opens next boot (log artifact)
- [ ] `hlt 0` + `twn 0` → YAML behavior restored (log artifact)

## 5. Remote validation (bmcam000, Sofar Command API path)
- [ ] One command via cloud mailbox → delivered, acked, applied next boot
      (Sofar ack row + boot log artifacts)

## 6. Wrap
- [ ] GUI: two new commands in tools/bm_command_gui
- [ ] Docs + `bmcam-hotspot-update` skill updated (halt/window no longer
      SSH-only)
- [ ] PR → `development`, gates green
