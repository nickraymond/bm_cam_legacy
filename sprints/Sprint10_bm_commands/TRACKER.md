# SPRINT 10 — TRACKER

Checklist only. Rationale lives in SPEC.md; decisions in DESIGN.md;
open questions and bugs in DEV_LOG.md.

## 0. Setup
- [ ] Confirm sprint numbering against repo history (renumber docs if needed)
- [ ] Locate any prior daemon spec in repo; reconcile with SPEC.md
- [ ] Create feature branch `sprint-10-command-daemon`
- [ ] Answer/triage open questions in DEV_LOG.md with Nick

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

## 7. Wrap
- [ ] Update DEV_LOG.md with findings, bugs, deferred items
- [ ] PR opened with test evidence for Nick to review
- [ ] Docs updated (README / operator notes for field use)
