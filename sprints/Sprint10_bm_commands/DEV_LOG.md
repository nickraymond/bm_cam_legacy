# SPRINT 10 — DEV_LOG

Running record: open questions, decisions taken mid-sprint, bugs, and
incidental findings. Newest entries at top within each section.

## Open questions (for Nick — raised 2026-07-26, pre-sprint)

Hand these to the Claude Code session; resolve before or during §1 of the
tracker. Defaults noted where a safe assumption exists.

- **Q1 — Current UART framing.** What does the existing daemon use today
  (JSON lines? custom binary? delimiters?)? SPEC assumes we extend it
  as-is (D8). *Blocker for §2.*
- **Q2 — Camera stack.** picamera2/libcamera, OpenCV, other? Determines
  how ROI/focus/AWB/exposure are actually set, and whether ROI is a sensor
  crop or post-capture crop before compression. *Blocker for §3.*
- **Q3 — ROI preset list.** SPEC has placeholder presets (full,
  center-75%, center-50%, top-half, bottom-half). Confirm the crops that
  actually matter for the field scene. *Default: ship placeholders,
  tune in Phase B.*
- **Q4 — Final v1 command list.** SPEC locks six commands (roi, foc, awb,
  exp, win, ping). Anything to add or cut? *Default: as specced.*
- **Q5 — Persistence.** SPEC says persist settings across power cycles
  with state file. Confirm. *Default: persist.*
- **Q6 — Ack/verification depth.** Ack message only, or also a thumbnail
  at the new ROI so the operator can see the framing? Thumbnail costs
  satellite bytes. *Default: ack only in v1; thumbnail as stretch.*
- **Q7 — Dead-man's revert.** If a bad setting is applied and no
  confirmation arrives within N windows, auto-revert to last-known-good?
  Adds safety, adds complexity. *Default: not in v1; log as v2 candidate.*
- **Q8 — Mid-window apply.** SPEC says apply between captures on arrival.
  If that destabilizes the pipeline, fall back to apply-at-next-window.
  *Default: between-captures; revisit if Phase A/B shows fragility.*
- **Q9 — Bench hardware availability.** Is dev kit + Spotter available
  for Phase B, and when? Shapes how much rides on the PTY mock.
- **Q10 — Deployment shape.** systemd service with restart-on-failure,
  or launched by the existing supervisor? *Default: match how the current
  daemon is launched today; do not change launch mechanics mid-field-test.*

## Known constraints (carried in from project context)

- Node duty cycle ~20 on / 40 off; cloud→Spotter latency dominates and is
  non-deterministic; commands queue cloud-side while bus is down.
- Spotter cuts BM bus power at ~15% battery SoC (Sofar figure); exact
  voltage spec unresolved — lives in gated Notion hardware guide /
  `bridge cfg status 0 s` output / PWR.csv empirics.
- Field thermal data (2026-07-23 SD upload): charger THERMAL_FAULT trips
  ≈43°C, resumes ≈40°C — charging can be blocked for hours in sun. Power
  budget headroom is real; the `win` command exists for this reason.
- Camera node (f365) draws ~1.12 W capturing, ~0.34 W avg at ~30% duty.

## Decisions taken mid-sprint

*(empty — append as they happen, with date + one-line reason)*

## Bugs / issues

*(empty — append with repro steps; move to tracker if they block)*

## Scratch / incidental findings

- bm_sbc reviewed 2026-07-26: send-only Python client over Unix DGRAM
  socket + CBOR; COBS+CRC32C UART framing; config get/set via BCMP.
  Useful reference for framing ideas if D8 is ever revisited.
- Sofar contact offered help with ephemeral config on the mote — good
  channel for mote-side questions during Phase B/C.
