# SPRINT 09 — DESIGN

Two halves: (1) how we work this sprint, (2) decisions made and reasons.
See repo CLAUDE.md for global rules; this file adds sprint-specific ones.

## How we work (Claude Code session rules)

- **Where sessions run:** this repo (`bm_cam_legacy`) on the Mac. The Pi
  runtime lives in `BM_Devel_Pi/` and deploys to `/home/pi/BM_Devel_Pi/`
  via `tools/deploy_rc_runtime.sh` (+ `tools/rc_runtime_manifest.txt`).
  The v2 spec's "work in the BM_Devel_Pi repo on the Pi" is obsolete —
  edit here, deploy, run tests over SSH.
- **Bench hardware:** Spotter ebox on Mac USB (Spotter CLI + SD access);
  camera node connected via Bristlemouth, running latest main. The Mac can
  talk to both sides to correlate errors.
- **Branch:** `sprint-09-uart-throughput`; never commit to main.
  *(Updated 2026-07-26, Nick: repo now uses a `development` integration
  branch — see CLAUDE.md "Branching Model". This sprint's PR targets
  `development`, not `main`. Bench Pi runs the active sprint branch during
  testing, `development` otherwise.)*
- **PR gate:** open PR (base: `development`) when TRACKER §1–§2 are green;
  Nick reviews before Phase B cellular quota is spent.
- **Tests Nick can run:** every phase is a single documented command with
  human-readable output (the test script prints JSON per run).
- **Docs discipline:** SPEC = what/why (stable), TRACKER = checklist,
  DEV_LOG = running record, updated in the same commit as the code.
- **Field-ops guard:** crontab backup before disable, restore after; never
  leave a unit halted/disabled. `power_halt` stays `dry_run: true` on the
  bench unit during this sprint.
- **Ask, don't assume:** open questions go to DEV_LOG §Open Questions;
  do not silently pick answers for anything marked (Q#).

## Decisions (with reasons)

**D1 — Pi-side only; no mote firmware this sprint.**
`serial_bridge` hardcodes 115200 (`PLUART::setBaud`), so baud gains need a
reflash — separate compile-tier project. The wire is at 0.5% utilization;
config-side chunk/pacing changes capture nearly all the win at zero
firmware risk.

**D2 — Measure the pacing floor; don't guess it.**
The 5 s gap likely protects the Spotter's 32-deep transmit queue. Phase B
sweeps gaps downward with delivery counted at the backend — the floor is
whatever the hardware says, +25% margin.

**D3 — Values live in `bm_serial:` YAML only (existing single source of
truth).** `image_buffer_size`, `image_transmit_delay_seconds`, and
`network_type` are already config keys read by the RC path. This sprint
changes *values*, and adds only `uart_port`/`baudrate` reads to the
`BristlemouthSerial` constructor (keys already exist top-level in YAML).
No new config surface.

**D4 — Chunk target ~980 base64 chars, not "996 bytes".**
`image_buffer_size` counts b64 chars; the wire message is `<I{i}>` + chunk.
~980 chars keeps every message under the 1000 B cellular cap with framing
headroom. (Corrects v2's raw-byte arithmetic — see SPEC.)

**D5 — cellular_only (0x02) for image data only.**
Already the deployed setting for images. No satellite fallback for bulk
image data is the intended trade; alerts/status stay on 0x01.

**D6 — Do not flip production values until Phase B reports.**
Config plumbing merges first with unchanged defaults (300 / 5 s). The
value change is a one-line YAML edit once the floor is measured — keeps
rollback trivial (restore three YAML values; framing never touched).

**D7 — Phase order A → B → C is quota-driven.**
Phase A (`spotter_log` → SD file) burns no cellular quota and proves link
integrity first. Phase B spends quota deliberately, capped. Phase C is one
real image.

**D8 — Sprint10 dependency.** The command daemon (Sprint10) inherits this
sprint's landed values and the same `bm_serial:` block. Sprint09 lands
first; Sprint10 must not fork chunk/pacing config.
