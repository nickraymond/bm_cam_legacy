# SPRINT 11 — DEV_LOG

Running record: decisions taken mid-sprint, bugs, incidental findings.
Newest entries at top within each section.

---

## Build log

### 2026-07-29 — C1–C4 landed off-device; 436/436 tests green (was 412)

Baseline before any edit: `python3 -m unittest discover -s tests` = 412
tests, OK. After C1–C4: **436 tests, OK**. Net +24, all new, no test
deleted — three existing tests were rewritten to the new contracts (see
"Contract changes" below), which is a different thing and is called out.

**C1 — capture-first.** Deleted the `pre_capture_listen` hook and its call
site in `run_cycle`. The cycle is now boot → time-sync → capture → encode
→ phase wait → transmit. `bm_commands.pre_capture_listen_s` is gone from
the config island; a stale config carrying it prints a loud
`[CMD][WARN] ... IGNORED since Sprint11` rather than silently looking
applied. That warning exists because a config that appears to have been
accepted is exactly how a field unit ends up on the wrong schedule.

**C2 — phase-aware scheduling.** New pure module
`BM_Devel_Pi/rc_transmit_phase.py`, island-gated by `transmit_phase:`.
28 fake-clock tests including an exhaustive sweep of all 300 phases
asserting that any burst that *can* fit a lane always both starts after
the post-boundary guard and ends before the pre-boundary guard.

**C3 — deferred acks.** `rc_transmit` gained `pending_pump_fn`, called in
each pacing slot. It parses and **persists** inbound commands mid-burst
but touches no wire; the ack queues and flushes after END. So a command
that lands mid-image still governs the next boot even if the cycle dies
before the flush — the persist and the ack are deliberately decoupled.

**C4 — post-transmit tail.** `post_transmit_listen()` in
`rc_command_hooks`, bounded by config AND clamped to
`budget.remaining_s() - TAIL_SAFETY_S` (20 s), so it can never run into
the Spotter's bus-power cut mid-write. Skips loudly when there is no room.

### Contract changes (tests rewritten, not deleted)

1. `test_command_integration.TestListenWindowApply` →
   `TestCaptureFirstOrdering`. The Sprint10 assertion was "a command in the
   pre-capture window governs THIS capture". Under C1/D2 the assertion is
   inverted: it must NOT govern this capture, and MUST govern the next.
   Both halves are now tested (`test_command_waiting_at_boot_does_not_
   change_this_capture`, `test_the_same_command_governs_the_next_cycle`).
2. `test_command_daemon.TestConfigIsland` — `pre_capture_listen_s` →
   `post_transmit_listen_s` + `defer_acks_during_transmit`, plus a new
   test pinning the loud-ignore of the retired key.

---

## Findings

### F1 — The integration harness was modelling acks and image chunks on *different* wires (2026-07-29)

Found while writing the C3 test. `test_command_integration`'s
`bm_open_fn` appended image chunks to a Python list while daemon acks went
through `BristlemouthSerial` to the fake UART — two independent sinks. On
hardware they are one UART feeding one 2-slot Spotter queue, which is the
entire premise of C3.

The assertion "no ack between the first and last chunk" would have been
vacuously true on the old harness. Fixed by adding a single ordered
`self.wire` log that both paths append to. **Any future test about
ordering or contention between acks and chunks must use `self.wire`, not
`tx_messages`.**

### F2 — `bm_serial.load_bm_serial_config` silently returns `{}` without PyYAML (2026-07-29)

Pre-existing, not introduced here, but it bit the C2 orchestrator tests.
`_load_camera_schedule` returns `{}` when `import yaml` failed, so pacing
falls back to the 5.0 s default on a dev Mac with no warning. A test that
*thinks* it is exercising 1.0 s pacing is quietly running at 5.0 s — and
at 5.0 s a 194-message image can never fit a 250 s lane, so every C2
phase assertion failed with `burst_exceeds_lane`.

Worked around in the tests with an explicit `settings_override`. The Pi
runtime has PyYAML so field behaviour is correct, but the silent-`{}`
fallback is a trap worth closing later (it would equally hide a genuinely
missing config file on any host).

### F3 — A phase wait spends the same budget the transmit needs (2026-07-29)

Not obvious until the wiring was done. `CycleBudget` starts at process
start and `select_quality` decides `fits` against it *before* the phase
wait. Waiting 130 s for the next lane can therefore make a selection that
fitted no longer fit, and `rc_transmit`'s per-chunk guard
(`budget.messages_fit(2)`) would truncate the image mid-send.

That is strictly worse than the problem being solved: a bad phase costs
~7 chunks, a truncated send costs the whole tail of the image. So the
wait is skipped (reason `skipped_no_budget`, logged loudly) whenever
`budget.has_time_for(wait + burst)` is false. Covered by
`test_wait_is_skipped_when_it_would_starve_the_burst`.

**Consequence for config:** `win` must leave room for the worst case
`wait + burst` — at 1.0 s and cap 195 that is 300 + 197 ≈ 500 s minimum,
comfortably inside the 13-minute `win` used for Unit A.

---

## Open questions

- **Q12 (carried from Sprint10, unanswered).** Sofar/Blues: can the
  5-minute sync be pinned/deferred/disabled; is the 2-slot cellular queue
  depth configurable; is there a backpressure signal; why does blackout
  duration vary 9→36 s? Any one of these makes C2 unnecessary.
