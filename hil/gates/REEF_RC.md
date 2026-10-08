# REEF-RC — the exact NOAA build + reef config, with remote commands in use (bmcam003)

Card from the EM (Nick approved 2026-10-07 4:55 PM PDT). Owner: Test Engineer (TE2). Evidence: `runs/reef_rc_<date>/`.
Times UTC (PDT = UTC − 7). **Starts on bmcam003 when R3-PACE stops** (the 02Z interim; if 1.5 s is not meaningfully better,
1.0 s is final).

**Why:** NOAA needs a software fix ASAP. Nick will FLASH A NEW CAMERA with the tested build + config and ship it (no field
update). REEF-RC must test EXACTLY that build + config, and record the sha + full print-config so provisioning can
reproduce it.

**Question:** with the reef config and remote commands in use, do images still arrive as well as without commands, and
is every command confirmed and applied?

## Build (recorded before the first counted wake)

- development tip + bm #143 (prune fix) **if PRUNE-GATE passes** (verdict ~9:20 PM 10/7), else the development tip
  alone. The card records which, plus the exact sha (`software_sha.txt` read back on the unit).
- Deploy with `hil/tools/hil_deploy_window.sh bmcam003 <ref> <sha> <profile> <window> reefrc` in one window. This
  replaces the R2 single-file patch (rc_progressive_jpeg.py). `r2_start_delay_s` is removed in the same session
  (no delay). Deploy + config writes happen BEFORE wake 1; from wake 1 on there are 0 SSH writes.

## Config = reef (written into the BASE camera_config.yaml, not into overlays)

- pjpg, crop 1504,846,1600,900 → 1000 px wide, message cap 195, 384 chars/msg, cellular-only 0x02, pacing
  `uplink.msg_interval_s` 1.0 s.
- per_boot + real halt; no lane (`uplink: lane:` off), no start delay.
- Bridge SPOT-33507C: bus 10 min on / 50 off from :00 (bridgePowerControllerEnabled 1, sampleIntervalMs 3600000,
  sampleDurationMs 600000, ticksSamplingEnabled 0, alignmentInterval5Min 1). Read back on the console before wake 1 (no change expected).
- Backend: self_heal ON for SPOT-33507C at production 24/day. **The EM flips it at the start.**
- **Remote-config overlay cleared before wake 1** (bmcam003 carries `mode.media still`, cid 1000159, from R2). A
  freshly flashed NOAA unit has no overlay, so the reef values must live in the base YAML. The card's commands then
  create the only overlay entries.
- **Provisioning record** (`runs/reef_rc_<date>/BUILD_RECORD.md`): sha, full `--print-config` output, base YAML
  sha256 + copy, bridge read-back, backend rollout state. Taken after the deploy and again after wake 12; the two
  must differ only by the commands' overlay.

## Commands

- One remote `set` about every 2 h through the backend (product path) on a harmless key, then back (e.g. a WB gain or an
  `image_processing` value). Plus normal heal traffic (rsd). **Cloud/backend sends are done by the EM or the backend, never by
  the TE.** The TE logs each send time from the EM.
- Confirmed = backend ack or config hash matches. In effect = the value shows in the next wake's print-config/cycle log
  (read-only).

## Measures per wake (read-only: console via nereus000, cycle log via bounded ssh reads)

First-send loss on the wake's key (`hil_r2_score.py`); gaps; queue_full; stall attribution (HDR / report-sync /
other); heal chunks sent + rsd served; command landing time vs the report minute (SPOT-33507C reports at :10); START
/ END; wake→halt; image completion time at the backend (EM / media table on nereus000).

## Rules (pre-registered)

- n = 12 consecutive wakes (≈ 6 with a command landing).
- **PASS:**
  - 100 % of commands confirmed (ack or hash) AND in effect at the next wake;
  - median first-send loss on command wakes ≤ median on no-command wakes + 2 pp;
  - every image complete within 6 h;
  - 0 SSH writes and 0 hard cuts (bus-off with the Pi still running) during wakes 1–12.
- **STOP:** answered at 12 wakes; any command lost twice, or SSH needed → stop and report.

## EM answers (2026-10-07 ~5:15 PM PDT; Q1/Q3 from Nick)

1. Test AROUND THE CLOCK: every hourly wake, no transmit window. The window/time-source choice for the shipped unit is
   set at provisioning. BUILD_RECORD.md notes it as "not tested by REEF-RC".
2. Command key `camera.image_processing.contrast`, 1.0 → 1.1 → 1.0 alternating, one `set` every ~2 h (backend product
   path). If contrast is not in the control tier of the live catalog, use the closest image_processing key that is.
   **TE note:** the unit registry has the key (FLOAT 0.5–2.0, nullable). It reaches rpicam only while
   `camera.controls_enabled` AND `camera.image_processing.enabled` are true (config_registry.py, rc_capture.py:486).
   The reef config has image_processing disabled, so with this key "in effect" = the applied config value
   (print-config / config hash in the cycle log), not a change in the image.
   **EM decision (A):** keep contrast, with config-level proof = the applied value + config hash in the cycle log / `<CF>`
   at the next wake. Reasoning: REEF-RC asks whether commands land, are confirmed and applied, and whether they hurt
   message delivery. A no-op on the image keeps image size and message count identical between command and no-command
   wakes, so the loss comparison is not confounded. A "capture really changes" check (e.g. a WB fix with controls
   enabled) is a later card. Pre-check: the backend catalog must let `set camera.image_processing.contrast` through (not "blocked").
   **Pre-check DONE (TE2, nereus-vision-dev origin/staging 9a9c3f6, vendor catalog registry v11):** `tier: control`,
   `blocked_values: []`, range 0.5–2.0, `apply: next_action`. The `requires` (controls_enabled, image_processing.enabled)
   produce only a plan WARNING ("saved but not applied until … true", services/remote_config.py `_warnings`), not a
   refusal. `one_shot: true` = the key may also be used in a one-shot capture command (command_v9.one_shot); it does not
   affect a persistent `set`.
3. self_heal ON, 24/day. The EM flips the backend for SPOT-33507C when the TE reports wake 1.

Start: when R3-PACE stops after the 02Z (7 PM) interim. The interim verdict goes to the EM first.
