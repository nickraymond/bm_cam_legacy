# Sprint15 — Video recording, local storage, customer download UI

Status: PIVOT LOGGED (2026-08-17). NOT YET SPECCED — a devoted spec/design
session comes first. Nothing in this file below "Intent" is a design
decision; it is a scoping stub for that session.

## Priority decision (Nick, 2026-08-17)

Nick is moving the target: video recording with local storage and a
customer-facing UI now takes priority over Sprint14 (overnight command
soak / ROI sweep). Sprint14 stays SPECCED-STUB and parked behind this
sprint; its prerequisites (Sprint13 merged + Nick-tested `help`) are
unchanged.

Work order agreed 2026-08-17:

1. bmcam000 recovery — back on Tailscale, then current with
   `development` via `tools/rc_field_update.sh` (TODO-BM-011).
2. Sprint13 wrap: Nick sign-off on `help`/`cfg`, docs, merge PR #33.
3. Sprint15 devoted spec/design session (this sprint), then build
   sessions.

## Intent (Nick, verbatim scope)

> I now need to setup video recording with local storage and a UI that
> will allow my customer to see and download the videos post deployment.
> We need a devoted session to spec this out and design it, then a few
> sessions to build the video recording mechanism, the memory control
> (ring memory?) and the UI.

## Session plan

- **Session A — spec + design (next after Sprint13 merge).** Produce
  DESIGN.md + full SPEC with Nick in the loop. Outputs: recording
  parameters, storage budget + ring-buffer policy, UI access model,
  TRACKER.md with gated chunks.
- **Session B — recording mechanism.** Capture path on the Pi
  (production-path discipline: verify what rpicam-vid / Picamera2 video
  actually do on a Pi Zero 2 W + IMX708 before committing; do not assume
  parity with the still pipeline).
- **Session C — storage / memory control.** Ring buffer over local
  storage. TODO-BM-008 (SD-card ring buffer) is the existing specced
  starting point: dry-run first, never delete active artifacts, oldest
  safe artifacts first, protect OS files, telemetry on cleanup.
- **Session D — customer UI.** Browse + download recorded videos
  post-deployment.

## Open questions for Session A (do NOT resolve here)

- Recording trigger: continuous, scheduled (duty-cycle aligned), or
  command-driven (`trg`-style via the Sprint12 daemon)?
- Codec/encoder budget on Pi Zero 2 W (hardware H.264? thermal + power
  cost per minute of recording?) — measure, don't assume.
- Storage sizing: minutes/GB at chosen resolution; SD endurance;
  interaction with existing image artifacts and SD reporting.
- Ring policy: cap by bytes or by clip count; protect clips flagged by
  the customer?
- UI access model: videos cannot ride the BM/cellular path (bandwidth).
  "Post deployment" presumably means local access after recovery —
  Pi-hosted web page over WiFi/hotspot? USB export? Needs Nick's
  deployment-workflow answer.
- Does video recording coexist with the halt/duty-cycle power model, or
  is this a different (powered) deployment profile?
- ~~Which unit is the bench target (bmcam003 assumed)?~~ ANSWERED
  (Nick, 2026-08-17): **bmcam000** — bmcam003 and bmcam004 are now
  potted (no SD access; bmcam003 off by intention), so bmcam000 (SD
  still swappable) is the development unit. It sits on the bench hosted
  by SPOT-33507C, in developer state (dev_mode.sh on), on development
  10cd9f7. NOTE: still has the default pi password — change before any
  deployment. Also carried in: SPOT-33507C has pushed no Sofar cloud
  rows since 2026-07-31T19:00Z (cellular stall — check antenna/queue
  before any cloud-delivery testing).
