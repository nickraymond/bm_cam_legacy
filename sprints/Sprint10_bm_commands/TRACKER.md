# SPRINT 10 — TRACKER

Checklist only. Rationale lives in SPEC.md; decisions in DESIGN.md;
open questions and bugs in DEV_LOG.md.

## 0. Setup
- [x] Confirm sprint numbering against repo history (renumber docs if needed)
      — sprints/ holds 02–09; 10 is next. No renumber needed. (DEV_LOG 2026-07-26)
- [x] Locate any prior daemon spec in repo; reconcile with SPEC.md
      — none found. `bm-daemon.service` in README/Sprint03 is an unrelated
      legacy systemd service (disabled during manual tests). (DEV_LOG 2026-07-26)
- [x] Create feature branch `bm_commands` from `development` (Branching
      Model in CLAUDE.md; PR targets `development`)
      — created from origin/development @ d44f535.
- [x] Answer/triage open questions in DEV_LOG.md with Nick
      — Q1–Q10 answered pre-sprint; Q11 (cloud downlink) is the only open
      one and blocks §7 send path + Phase C/D only, not §1–§4.

## 1. Command layer core
- [ ] `command_tables.py` — enums + value tables (single source of truth)
- [ ] Command parser: validate `id`/`c`/`v`, reject unknown/malformed
- [ ] Dedupe store: last-N applied command IDs, persisted
- [ ] Settings state file: write-on-change, load-on-boot
- [ ] Ack builder: `{id, ok, st}` full-state ack

## 2. Daemon integration
- [ ] Wire parser into existing UART reader path (inbound)
- [ ] Wire ack into existing UART writer path (outbound)
- [ ] Listen across full active window, concurrent with camera ops
- [ ] Apply between captures only; never mid-capture

## 3. Camera bindings
- [ ] ROI apply (crop per table index)
- [ ] Focus apply (auto / manual positions)
- [ ] AWB apply
- [ ] Exposure apply
- [ ] Active-window duration apply (capture+compression budget)
- [ ] `ping` (ack-only, no camera touch)

## 4. Phase A tests (no hardware)
- [ ] PTY mock-mote harness (`socat`)
- [ ] Unit: parser accept/reject matrix
- [ ] Unit: dedupe (duplicate ID → ack, no re-apply)
- [ ] Unit: state persistence across simulated restart
- [ ] Unit: partial/garbled frame handling
- [ ] Integration: command at t=1min and t=19min of a simulated window

## 5. Phase B tests (bench, Spotter serial)
- [ ] Each of the 6 commands applied via Spotter CLI, ack observed
- [ ] Hard power cycle → settings retained
- [ ] Full active window soak with capture pipeline running

## 6. Phase C tests (remote API — last)
- [ ] Command via Sofar cloud API while node ON
- [ ] Command queued while node OFF → delivered on wake
- [ ] Burst delivery (multiple queued commands) handled in order

## 7. Operator GUI (MVP — SPEC "Operator GUI", DESIGN D9/D10)
- [ ] Confirm Sofar cloud downlink mechanism/endpoint for sending commands
      (DEV_LOG Q11) — blocker for the send path
- [ ] Target selector: registered SPOT-ID list + expected node id
- [ ] Preset dropdowns generated from `command_tables.py` (no free input)
- [ ] Send path: command → Sofar API; show cloud-accept confirmation
- [ ] In-flight state per command id (pending indicator; warn on re-send
      while pending)
- [ ] Ack watcher: poll `api/sensor-data`, match ack by command id, verify
      node id + `st` values; display match clearly, mismatch loudly
- [ ] GUI runbook: how Nick starts it and what each state means

## 8. Phase D — automated permutation test (gate before final acceptance)
- [ ] Define 3–5 command permutations (e.g. zoom+focus, awb+exp,
      win+ping, factory-reset sequence)
- [ ] Automate: send via cloud path → verify ack (node id + values) →
      run capture cycle → confirm image lands in backend with settings
      applied
- [ ] All permutations pass; evidence (per-permutation table + backend
      screenshots/JSON) in runs/ + DEV_LOG

## 9. Final acceptance + wrap
- [ ] Nick drives the GUI end-to-end (definition-of-done items 1–4)
- [ ] Update DEV_LOG.md with findings, bugs, deferred items
- [ ] PR opened (base: development) with test evidence for Nick to review
- [ ] Docs updated (README / operator notes for field use)
