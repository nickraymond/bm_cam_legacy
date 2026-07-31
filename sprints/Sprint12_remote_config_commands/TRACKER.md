# Sprint12 — Tracker

Tick a box only when an artifact proves it. Where a box is partially done, say
what is missing rather than ticking it. Sprint is SPECCED, NOT STARTED — this
tracker is the to-do list for the session that picks it up.

## 0. Setup
- [ ] Branch off `development` (feature/sprint12-* or claude/sprint12-*)
- [ ] Read SPEC.md + Sprint10/11 command-daemon code paths
      (`command_tables.py`, `command_bindings.py`, `command_state.py`,
      `rc_command_hooks.py`) before writing anything
- [ ] Confirm all four units' current state before changing anything
      (all should be: main + production halt config as of 2026-07-31;
      bmcam001/002 halt flip may still be pending a hotspot session —
      check `device_profiles/` comments and fleet memory)

## 1. `hlt` command
- [ ] `HLT_TABLE` in command_tables.py, tables_version 2 → 3
- [ ] Binding + persisted state (`bm_command_state.json` schema addition)
- [ ] Overlay application: `power_halt` enabled/dry_run from state, next boot
- [ ] `hlt 0` removes the override (YAML governs)
- [ ] **Ack-before-halt ordering test** — command mid-cycle on a halting
      unit, ack reaches the uplink before the halt fires
- [ ] Boot log states applied halt mode unambiguously
- [ ] Unit tests (table, persistence, overlay, version mismatch)

## 2. `twn` command
- [ ] `TWN_TABLE` presets in command_tables.py
- [ ] Binding + persisted state + overlay (`transmit_window` from state)
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
