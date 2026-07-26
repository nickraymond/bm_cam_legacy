# SPRINT 10 — DEV_LOG

Running record: open questions, decisions taken mid-sprint, bugs, and
incidental findings. Newest entries at top within each section.

## Open questions (for Nick — raised 2026-07-26, pre-sprint)

Hand these to the Claude Code session; resolve before or during §1 of the
tracker. Defaults noted where a safe assumption exists.

- **Q1 — Current UART framing.** ✅ ANSWERED 2026-07-26 (repo audit) —
  see §Answers below. Key caveat: outbound framing is complete, but the
  only inbound path today is a payload pattern-scan, not a real frame
  parser. D8 "extend as-is" needs a proper inbound COBS decoder written.
- **Q2 — Camera stack.** ✅ ANSWERED 2026-07-26 — see §Answers. RC path
  is rpicam-still subprocess + PIL post-capture crop; ROI = config crop in
  native coords, no sensor reconfiguration needed.
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
- **Q10 — Deployment shape.** ✅ ANSWERED 2026-07-26 — see §Answers.
  There is no persistent daemon/systemd today: an `@reboot` cron (flock +
  `rc_run_capture_cycle.sh`) runs ONE RC cycle per power-up, then the box
  halts and the Spotter cycles power. The "daemon" should be a listener
  running inside (or alongside) that per-wake process for the active
  window — not a new systemd service.

## Answers (2026-07-26 repo audit, pre-sprint — bm_cam_legacy @ main)

**Q1 — UART framing today** (`BM_Devel_Pi/bm_serial.py`,
`spotter_time_sync.py`, `rc_transmit.py`):
- *Transport frame (both directions):* BM serial packet = 4-byte type
  header (`02 00 00 00` pub) with CRC16 at bytes 2–3, node-id (8 B LE),
  `01 01`, topic-len (2 B LE), topic, payload — COBS-encoded,
  `0x00`-delimited.
- *Outbound topics:* `spotter/transmit-data` (+1 network-selector byte:
  0x01 fallback / 0x02 cellular-only) and `spotter/fprintf` (SD logging).
- *App layer on transmit-data:* base64 image chunks framed `<I{i}>…`,
  plus START/END/inc status messages (`rc_uplink_messages.py`).
  300 b64 chars/msg, 5 s pacing — Sprint09 is changing these values.
- *Inbound today:* `spotter_time_sync.py` sends an official BM_SERIAL_SUB
  frame for `spotter/utc-time`, then **pattern-scans a rolling 4 KB raw
  buffer** for the clock payload (`_find_clock_payload`). There is no
  general inbound COBS frame decoder. **Sprint10 must write one** —
  subscribe mechanism exists and is proven; frame parsing does not.
- Reader/writer today are sequential phases of one process (time sync
  reads, then capture/transmit writes) — full-duplex threads (D2) are new.

**Q2 — Camera stack** (`camera_schedule.yaml`, `rc_progressive_jpeg.py`,
`main_pi_camera.py`):
- Two paths, gated by `capture_mode`: legacy HEIC (Picamera2 or rpicam via
  `image_pipeline`) and RC progressive JPEG (**requires**
  rpicam-still/libcamera-still subprocess; refuses other backends).
- RC path: native 4608×2592 JPEG q95 capture → PIL fixed crop in native
  sensor coordinates (`progressive_jpeg.crop`, default 1504,846 1600×900)
  → lanczos to 1000 px wide → quality-ladder encode.
- Therefore: **ROI command = swap the `progressive_jpeg.crop` values**
  (post-capture crop, native coords — command_tables presets are crop
  rects; no ScalerCrop/sensor work). **Focus/AWB/exposure = rpicam-still
  CLI flags** (`--autofocus-mode`/`--lens-position`, `--awb`, `--ev`) —
  the YAML already reserves a commented `camera:` block for these.
  **win command = `progressive_jpeg.max_run_time_min`.** All six v1
  commands reduce to YAML-value swaps + capture-command flags; no new
  camera API surface.

**Q10 — Deployment shape** (`rc_run_capture_cycle.sh`, Sprint08 P8):
- Launch: crontab `@reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock
  /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh` → one RC cycle → power
  halt (`power_halt` YAML) → Spotter cuts/restores power. Logs to
  `cron_logs/`.
- Implication for D5/§2: "listen the whole active window" means the
  command listener lives in the per-wake RC process (thread per D2),
  starting before capture and ending at halt. Settings state file must be
  read at process start — there is no long-lived process to hold state.
- Deploy tooling: edit in repo `BM_Devel_Pi/`, push with
  `tools/deploy_rc_runtime.sh` (+ `tools/rc_runtime_manifest.txt`).

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

- 2026-07-26 Spotter CLI reference captured:
  [docs/spotter_cli_reference.md](../../docs/spotter_cli_reference.md).
  **Phase B command injection candidate:** `bm pub <topic> <data> <type>
  <version>` publishes onto the BM bus from the bench terminal — verify
  early in Phase B that it reaches the mote→Pi UART path (it exercises
  the same inbound direction the daemon listens on). Also useful:
  `bm topo` / `bm info <node_id>` for node discovery, `bm cfg ...` for
  node config partitions.

- bm_sbc reviewed 2026-07-26: send-only Python client over Unix DGRAM
  socket + CBOR; COBS+CRC32C UART framing; config get/set via BCMP.
  Useful reference for framing ideas if D8 is ever revisited.
- Sofar contact offered help with ephemeral config on the mote — good
  channel for mote-side questions during Phase B/C.
