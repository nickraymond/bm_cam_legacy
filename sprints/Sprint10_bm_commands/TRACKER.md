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
- [x] `command_tables.py` — enums + value tables (single source of truth)
      — BM_Devel_Pi/command_tables.py; 22 invariant tests green
      (tests/test_command_tables.py). foc/awb/exp presets are PLACEHOLDER
      (flagged in module docstring + DEV_LOG).
- [x] Command parser: validate `id`/`c`/`v`, reject unknown/malformed
      — BM_Devel_Pi/command_messages.py `parse_command`; accept/reject
      matrix green (tests/test_command_messages.py, 25 tests). Bad JSON /
      bad id = unackable drop; bad cmd/value = ack with error code.
- [x] Dedupe store: last-N applied command IDs, persisted
      — command_state.py (last 32 ids, one file with settings; D4).
- [x] Settings state file: write-on-change, load-on-boot
      — command_state.py CommandState: atomic tmp+fsync+replace on every
      record; tolerant loud load (corrupt file / out-of-table values →
      per-key defaults). tests/test_command_state.py (17 tests).
- [x] Ack builder: `{id, ok, st}` full-state ack
      — command_messages.py `build_ack`; exact wire strings pinned, worst
      case ~70 B << 384-char chunk; `st` always complete (defaults fill).

## 2. Daemon integration
- [x] Wire parser into existing UART reader path (inbound)
      — command_daemon.py CommandDaemon: reader thread feeds
      FrameAccumulator → parse/dedupe/persist on main thread; shared-port
      time sync (proven pattern-scan) included per D11. 16 tests green
      on a fake UART with production-encoded frames.
- [x] Wire ack into existing UART writer path (outbound)
      — drain_acks() via bm.spotter_tx, main-thread only (single-writer
      by construction); send failure requeues. Pacing-slot hookup lands
      with the rc_progressive_jpeg/rc_transmit integration below.
- [x] Listen wake→halt with pre-capture listen window (D5 as corrected
      2026-07-26; early halt retained, no idle post-transmit listening)
      — rc_progressive_jpeg + rc_command_hooks: daemon owns the port for
      the whole cycle; listen window before capture; acks in transmit
      pacing slots; final drain in finally. tests/test_command_integration.py.
- [x] Apply between captures only; never mid-capture
      — by construction: all command processing on the main thread at
      explicit safe points (listen window / pacing slots / final drain);
      the listener thread only enqueues. Integration test pins that a
      mid-transmit command does NOT change this cycle's capture.

## 3. Camera bindings
      (all via command_bindings.py overlay — D13; off-device verified
      against the PRODUCTION rpicam arg builder + full-cycle sidecar
      checks; on-hardware verification is §5 Phase B)
- [x] ROI apply (crop per table index)
      — overlay_rc_settings → progressive_jpeg crop + output_size;
      integration test pins sidecar crop_native_xywh == table rect.
- [x] Focus apply (auto / manual positions)
      — overlay_camera_controls → --autofocus-mode/--lens-position;
      touched-gating keeps YAML manual focus until foc is commanded.
- [x] AWB apply
      — --awb mode / --awb custom --awbgains R,B (underwater preset).
- [x] Exposure apply
      — --ev (support added to process_image_v2 exposure island).
- [x] Active-window duration apply (capture+compression budget)
      — max_run_time_min/budget_seconds; takes effect NEXT cycle when
      commanded mid-window (budget already charged — logged decision).
- [x] `ping` (ack-only, no camera touch)
      — records id for dedupe, settings untouched (state + daemon tests).

## 4. Phase A tests (no hardware)
- [x] PTY mock-mote harness (`socat`)
      — tools/mock_mote.py (os.openpty, no socat dependency; raw-mode
      pty). Sends production-framed commands, decodes daemon replies.
      Verified end-to-end on a real PTY: apply/ping/reject/duplicate all
      acked, ~100 ms round trip, exit code gates scripting.
- [x] Unit: parser accept/reject matrix
      — tests/test_command_messages.py (every command × every index
      accepted; hostile-input sweep never raises; error-code + ackability
      classification pinned).
- [x] Unit: dedupe (duplicate ID → ack, no re-apply)
      — tests/test_command_state.py TestDedupe (incl. survives restart,
      last-N eviction).
- [x] Unit: state persistence across simulated restart
      — tests/test_command_state.py TestPersistence + TestCorruptRecovery
      (restart = fresh CommandState on same path, the actual Q10 boot path).
- [x] Unit: partial/garbled frame handling
      — tests/test_bm_frame_decoder.py (24 tests): byte-at-a-time splits,
      corrupted frames, junk recovery, overflow bounding, 200-chunk
      random hostile stream; frames round-trip the PRODUCTION encoder.
- [x] Integration: command in pre-capture listen window applies this
      cycle; command during transmit acks + persists for next cycle
      (revised with the 2026-07-26 early-halt decision)
      — tests/test_command_integration.py: real run_cycle + real daemon
      on fake UART; sidecar crop proves this-cycle apply; mid-transmit
      command acked in pacing slot, image send byte-sequence intact;
      disabled-island regression guard (no port, no [CMD] output).

## 5. Phase B tests (bench, Spotter serial)
- [x] Each of the 6 commands applied via Spotter CLI, ack observed
      — 2026-07-27 overnight bench (bmcam003 + SPOT-33507C): 40/40
      commands delivered via `bm pub bmcam/cmd <json> 1 1`, all acked
      (Spotter console shows cell-queue submits); roi/foc/awb/exp flags
      verified in the actual rpicam command line; win verified next-
      cycle; reject/dup/burst/factory-reset all pass. Latency n=40:
      45–262 ms (median 159). Evidence: runs/sprint10_phaseB_20260727/.
- [x] Hard power cycle → settings retained
      — 2026-07-27 ~16:16–16:26Z: Nick hard-cycled the ebox (Spotter +
      node). bm_command_state.json survived byte-intact (settings,
      touched, all 32 dedupe ids verified post-boot). Shutdown forensics
      in DEV_LOG: the unit had NOT halted itself — it ran continuously
      from the overnight tests until the cycle (fake-hwclock save at
      16:16 pre-NTP; power controller off; battery 23.89 V unchanged).
- [ ] Full active window soak with capture pipeline running
      — partial: 5 capture+encode cycles with daemon concurrent, zero
      errors; full-window soak WITH transmit deferred (cellular spend).

## 6. Phase C tests (remote API — last)
- [x] Command via Sofar cloud API while node ON
      — 2026-07-27: ping id=801 POSTed 17:33:12Z (202), mailbox drained
      18:35:54Z ("Remote message received(52)"), daemon applied + acked
      within the same minute. Full chain: API → mailbox → Notecard sync
      (INDOORS, no GPS fix) → console exec → bus → Pi → ack. E2E latency
      62.7 min (sync-bound; bench ping 802 via console: ~1 s). Payload
      arrived byte-clean (strict parser accepted; quotes intact).
      Evidence: runs/sprint10_phaseC_20260727/. Backend ack visibility
      pending (Notecard lag).
- [ ] Command queued while node OFF → delivered on wake
- [ ] Burst delivery (multiple queued commands) handled in order

## 7. Operator GUI (MVP — SPEC "Operator GUI", DESIGN D9/D10)
- [x] Confirm Sofar cloud downlink mechanism/endpoint for sending commands
      (DEV_LOG Q11) — blocker for the send path
      — POST /user-rest/devices/:spotterId/command, message = Spotter
      console line (≤270 B), telemetry=cellular. Digitized from Nick's
      capture: docs/sofar_command_api_reference.md. Caveats: cURL/
      Responses toggles were collapsed in capture (auth + response
      schemas missing); bm-pub-via-mailbox proven only on paper until
      Phase C test 1. (DEV_LOG 2026-07-27)
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
- [x] PR opened (base: development) with test evidence for Nick to review
      — PR #15 (2026-07-27), §1–§4 green + Phase B bench evidence.
- [ ] Docs updated (README / operator notes for field use)
