# JXL-RC: RC2 (JPEG XL B3a stills) overnight soak on bmcam003 / SPOT-33507C

Card from the EM (Nick decided 2026-10-08 9:40 AM PDT). Owner: Test Engineer (TE2). Evidence: `runs/jxl_rc_20261008/`.
Times UTC (PDT = UTC − 7). Window: wake 1 = 22Z (3 PM PDT 10/8) → Fri 2026-10-09 15Z (8 AM PDT).

**Question:** does the RC2 build deliver B3a JPEG XL stills that decode at the backend and complete, with remote
commands confirmed, no budget skips or wake overruns, and no SSH?

## Build (BUILD_RECORD.md in the run folder)

- Runtime **9ec4cb7** (development tip, = 786b100 + #145 docs-only; `git diff 786b100 9ec4cb7 -- BM_Devel_Pi tools` empty).
  Deployed 2026-10-08 20:02Z (parity OK, validation PASS). The EM recorded RC2 sha = 9ec4cb7.
- cjxl 0.11.2 (libjxl-tools 0.11.2-0.1~deb13u2, Nick's OK) installed 19:02Z.
- Base YAML = the REEF-RC reef config (BUILD_RECORD of runs/reef_rc_20261007) + `still.format: "nrjxl"` (Nick's OK,
  POST in the 21Z window) + `still.raw.layout: rgb` (hil_b3a_layout.sh) + `camera.exposure.profile: auto` (registry default).
- self_heal ON (24/day), hourly bus 10 min from :00, no window, per_boot + real halt.

## Commands (sent by the EM via the backend; the TE never sends)

- Wake 1 (22Z) = nrjxl + rgb + auto exposure, no command applied.
- **Low-gain lock (command under test, overlay):** `camera.exposure.profile low_gain`, `max_shutter_us 60000`,
  `max_gain 1.0`. Sent 2:50 PM PDT → console at the 22:10Z sync → applied at the 23Z boot (wake 2+). It goes into the BASE of
  the flashed build only if tonight is clean (Nick decides Fri AM).
- Then one contrast toggle about every 2 wakes.

## Pass (pre-registered)

1. B3a images decode at the backend (Linear DNG) and every image completes ≤ 48 h. The heal budget binds: nrjxl needs
   ~37 asks/day vs the 24 cap, so 6 h is not the bar. Overnight, the check is decode OK + the completion trend.
2. Commands 100 % confirmed (ack or hash) and in effect at the next wake.
3. No budget skips (`skipped_no_budget` / `[PHASE][WARN] skipping`) and no wake overruns: halt before bus-off at
   :09:57, not a hard cut.
4. 0 SSH writes from wake 1 on (TE reads are read-only: cycle log cat, sent/ listing via stdin, ls).

**Stop:** a unit needs SSH, or loses 2 commands → stop and report.

## Measures per wake

Same as REEF-RC (`runs/reef_rc_20261007/reef_wake.sh`, `console_loss.py`), plus:
- per wake from the cycle log: still.format / layout / exposure profile actually used;
- nrjxl bytes and message count;
- the encoder's time and peak memory if logged;
- backend decode status (EM / media table).
