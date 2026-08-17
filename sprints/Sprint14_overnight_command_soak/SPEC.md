# Sprint14 — Overnight command soak: ROI sweep on the reef reference

Status: SPECCED-STUB (2026-08-01). HARD PREREQUISITE: Sprint13 (`help` +
`cfg`) merged AND tested by Nick. Design agreed with Nick during the
Sprint12 close-out; parked deliberately.

PRIORITY UPDATE (Nick, 2026-08-17): Sprint15 (video recording + local
storage + customer download UI, sprints/Sprint15_video_recording/) jumps
the queue ahead of this sprint. Sprint14 stays parked — prerequisites
unchanged — and runs only when Nick re-prioritizes it.

## Goal

A continuous overnight hardware test that exercises the whole Sprint12
command machinery on a duty cycle, while producing a clean single-variable
image experiment: the SAME reef reference transmitted at every ROI preset.

## Design (agreed 2026-08-01)

- bmcam003, Spotter **15 min ON / 15 min OFF** (Nick's pick:
  `bridgePowerControllerEnabled 1`, `sampleIntervalMs 1800000`,
  `sampleDurationMs 900000`, committed while the Pi is halted — the
  commit itself re-evaluates bus power on the spot).
- Bootstrap (one manual transmit cycle, tail injections): `twn 2`
  (all-day window, v4 true-24h) + `src 1` (persistent reef reference —
  camera skipped every cycle, dim-room-proof).
- Every armed cycle thereafter: boot → gate passes → transmit reference
  at current ROI → 150 s listen tail, in which the Mac-side driver
  injects the next `roi` (next-boot semantics make the sweep cadence
  exactly one preset per cycle) → real halt → 15 off → repeat.
- Sweep roi 0→4 (~2.5 h), then leave the unit cycling on the final
  config for the rest of the night (the soak part).

## Deliverables

- Driver script (Mac): console-log watcher + blanket tail injection
  (Sprint12's spam_cmd.sh pattern), ack tracking, run folder + manifest.
- Morning analysis: Sofar API chunk pull → image reassembly → **ROI sweep
  cut sheet** (5 crops of one source, PASS/WARN/FAIL per Sprint02-style
  review); complete-image count per D8; energy per cycle from the bridge
  addr-65 trace (nereus-spotter-sd-analysis skill §9).
- Command-machinery scorecard: commands sent / acked / applied,
  duplicates absorbed, any missed tails.

## Success criteria

5/5 ROI reference images COMPLETE at Sofar; every roi command applied on
the intended cycle; no wedge/boot-loop; overnight cycles healthy through
morning; artifacts + cut sheet in the run folder.

## Open

- Quota budget confirmation for ~5+ reference images (~1000 msgs) +
  overnight cycles.
- Whether to interleave `hlt`/`twn` no-op toggles into later cycles for
  extra command coverage, or keep the sweep single-variable (leaning:
  single-variable per CLAUDE.md experiment discipline).
