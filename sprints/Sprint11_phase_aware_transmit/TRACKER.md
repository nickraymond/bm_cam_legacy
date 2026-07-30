# Sprint11 — Tracker

Tick a box only when an artifact proves it. Where a box is partially done, say
what is missing rather than ticking it.

## 0. Setup
- [x] Branch off `development` (claude/sprint11-phase-aware-transmit-73e8cd)
- [x] Read Sprint10 `runs/sprint10_phaseE_20260728/RESULTS.md` (blackout model)
      and `runs/sprint10_overnight_20260729/RESULTS.md` (A/B + energy)
- [x] Confirm both units' current state before changing anything
      (they were left in Sprint10 test config — see §5)

## 1. C1 — capture-first ordering
- [x] Delete the pre-capture listen window; boot → time-sync → capture →
      encode → transmit
- [x] Commands apply from cached settings on the NEXT boot (D2)
- [x] Unit test: a command arriving mid-cycle does not affect this cycle's
      capture and DOES govern the next
- [x] Measured transmit-start offset: START at +33 s after power-on (better than the ~:01:00 target)

## 2. C2 — phase-aware transmit scheduling
- [x] Compute grid phase from Spotter UTC (`epoch mod 300`)
- [x] Transmit only inside `[boundary + 30 s, next boundary − 20 s]` (hardware-confirmed +33 s starts)
- [x] If the burst does not fit the remaining lane, wait for the next lane
- [x] **Clock-read failure falls back to today's behaviour** (D1) — test this
      path explicitly, it is the one that fails silently in the field
- [x] Unit tests with a fake clock at several phases (just-after boundary,
      mid-lane, just-before boundary, no-fit)

## 3. C3 — deferred acks
- [x] Acks queue during the image burst, flush after completion
- [x] Verify no ack is submitted between the first and last image chunk (test + observed on hardware)
- [x] Confirm ack content unchanged (byte-exact wire pin still passes)

## 4. C4 — post-transmit listen tail
- [x] Bounded tail (default 150 s) before the halt
- [x] Tail is skipped/short-circuited if the cycle is already near the window
      end, so it can never cause a power-cut mid-write
- [x] Commands received in the tail persist and govern the next boot (test)

## 5. Config + deployment
- [x] `sleep 30` → `0.5` in `rc_run_capture_cycle.sh` (no D4 rollback symptoms seen on hardware) (**D4 — first rollback
      candidate; flagged in the script**)
- [x] Unit A: `txd` 1.0 s, C1–C4 on, Spotter **15 on / 15 off**
- [x] Unit B: `txd` 5.0 s, C1 on (listen removed), C2–C4 off,
      Spotter **20 on / 10 off**
- [x] Both: 384 chars, cap 195, `src=1` reef primary (after the F4 yaml repair)
- [x] Deploy via `tools/rc_field_update.sh` (wraps deploy_rc_runtime); versions match (648c889)
      on both units (Sprint10 hit a `media_gid` TypeError from a piecemeal
      file copy — deploy the whole manifest)

## 6. Validation run (6 h) — ABORTED ~2 h in; see runs/sprint11_20260729/RESULTS.md
Partial results: C1/C2/C3 confirmed working on hardware; periodic blackout
losses eliminated; Unit A 0/3 complete at backend from SMALL sporadic head
gaps (D10 population — next sprint); Unit B 3/4 + a verified end-to-end
manual cycle; interim energy 0.1643 Wh/cycle (Unit A, console-derived).
- [ ] Pre-flight: `caffeinate` running, console capture on both Spotters,
      both units armed (cron + real `power_halt`)
- [ ] ROI sweep 1 → 2 → 3 → 0 → 4 over USB, same value to both units
- [ ] Run 6 h, then pull Spotter SD cards for energy
- [ ] **Metric 1:** complete images per device (START/END + zero gaps —
      NOT chunk %, per D8)
- [ ] **Metric 2:** measured energy per cycle per device (bridge addr-65,
      60 s means, per D9 + the `nereus-spotter-sd-analysis` skill)
- [ ] **Metric 3:** ACKs arrived when expected AND the commanded `roi`
      visibly took effect on the following cycle
- [ ] Compare against the Sprint10 baseline: 0/12 and 6/12 complete images;
      0.1797 and 0.2256 Wh/cycle

## 7. Wrap
- [ ] `runs/sprint11_<date>/RESULTS.md` + DEV_LOG entry in the same commit
- [ ] Restore both units to field-normal; verify imaging
- [ ] PR into `development`
- [ ] Decide the shipping `(txd, cap, bus window)` from the measured result

## Carried over from Sprint10 (still open)
- [ ] Both units restored to field-normal: `cfg vle 1` on SPOT-33507C,
      `src` back to live camera (0), pacing decision applied
- [ ] Sofar/Blues answers to Q12 (sync pinning, queue depth, backpressure)
