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
- **Q3 — ROI preset list.** ✅ ANSWERED 2026-07-26 (Nick): v1 `roi` is
  **centered zoom in/out only** — no pan. Scientists choose detail (zoom
  in on a specific coral) vs. wider scene. Presets are concentric
  centered 16:9 crops in native 4608×2592 coords, all lanczos-downsampled
  to the same 1000 px output, so the transmission budget is ~constant
  across zoom levels. Placeholder table (finalize exact rects before
  field deployment): 0 = default 1600×900, 1 = full-frame 4608×2592
  (widest), 2 = 3072×1728, 3 = 2304×1296, 4 = 1000×562 (max detail —
  floor chosen so output never upsamples). SPEC table updated.
- **Q4 — Final v1 command list.** ✅ ANSWERED 2026-07-26 (default
  adopted): six commands as specced (roi, foc, awb, exp, win, ping).
  One-shot "capture now" stays deferred (D7).
- **Q5 — Persistence.** ✅ ANSWERED 2026-07-26 (default adopted):
  persist to state file, reload on boot — required by the per-wake
  process model (Q10): every active window starts a fresh process.
- **Q6 — Ack/verification depth.** ✅ ANSWERED 2026-07-26 (Nick): ack
  only in v1. Thumbnail-at-new-ROI logged as v2 candidate (with
  Sprint09's larger chunks a 5–10 KB thumbnail ≈ ~10 cellular messages).
- **Q7 — Dead-man's revert.** ✅ ANSWERED 2026-07-26 (Nick): skip for
  v1. Rationale: the planned operator web UI will constrain customers to
  preset commands, and enum tables already bound the blast radius —
  every applicable value is a tested-valid setting.
- **Q8 — Mid-window apply.** ✅ ANSWERED 2026-07-26 (default adopted):
  apply between captures on arrival; fall back to apply-at-next-window
  only if Phase A/B shows fragility.
- **Q9 — Bench hardware availability.** ✅ ANSWERED 2026-07-26 (Nick):
  bench is up now (Spotter ebox on Mac USB, camera node on BM bus,
  latest main). Scheduling: **Sprint09 gets the bench first** (Phases
  A–C); Sprint10 stays on the PTY mock until Sprint09 Phase C is done.
  Sprint09's measured chunk/pacing values feed this sprint's ack path.
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

- **Q11 — Sofar cloud downlink mechanism (NEW 2026-07-27).** Sprint09
  proved the uplink read path (`api/sensor-data`, hex-decoded values, per
  its DEV_LOG Q2). The **downlink** — how a command sent to the Sofar
  cloud reaches Spotter → BM bus → mote → Pi UART — has not been
  exercised by us yet: exact API endpoint/mechanism, payload format, and
  how delivery interacts with the node duty cycle. Review Nick's API
  tooling + Sofar docs at GUI/§7 start. *Blocker for §7 send path and
  Phase C/D.*

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

- 2026-07-26 **§2a `bm_frame_decoder.py` — the repo's first inbound BM
  frame decoder** (closes the Q1 caveat). Strict path: 0x00 split →
  COBS decode → CRC16 verify (bytes 2–3 zeroed, same algorithm as
  bm_serial.crc) → pub-frame parse → topic match. Malformed input
  returns None/counted, never raises; FrameAccumulator bounds its buffer
  at 8 KB. Tests round-trip the PRODUCTION encoder so both directions
  pin one wire format. **Assumption flagged for Phase B:** inbound pub
  frames mirror the outbound layout (type 0x02, node id at [4:12],
  topic len at [14:16]); bytes [12:14] are NOT checked. First `bm pub`
  bench test must confirm real mote traffic parses — decoder stats
  (non_pub/other_topic counters) will show immediately if the layout
  differs.

- 2026-07-26 **Design review with Nick before §2 — eight implementation
  decisions locked (now DESIGN D11–D15 + corrected D5):**
  (1) listener = thread in the per-wake RC process;
  (2) FULL port-ownership refactor — UART opens once at process start,
  one reader thread from t=0, time sync moves onto the shared port but
  keeps its proven pattern-scan clock detection (D11);
  (3) acks drain in the existing 1.0 s pacing slots, no rc_transmit
  rewrite (D12);
  (4) **early halt retained — SPEC's full-window listen dropped** (Nick:
  "power savings trump responsiveness"); new pre-capture listen window
  (default 120 s, YAML) catches queued commands so a prompt delivery
  applies to this window's capture; Phase B measures real delivery
  latency to tune it. SPEC timing model + success criteria and the D5
  text revised in place;
  (5) command settings overlay YAML at resolve time, never rewrite it
  (D13);
  (6) whole feature behind `bm_commands:` YAML island, disabled ==
  byte-identical cycle (D14);
  (7) command topic YAML-configurable, default `bmcam/cmd`, provisional
  until Q11/Phase B `bm pub` check;
  (8) ack on persist — ok=1 means "stored; governs next capture" (D15).

- 2026-07-26 **§1 complete — `command_state.py` lands dedupe + settings
  persistence in ONE file** (`bm_command_state.json`, runtime-dir default
  like bm_serial.py, env/ctor override). Rationale: one atomic write path
  (tmp + fsync + os.replace) because the Spotter cuts power hard — a
  half-written state file must be impossible. Dedupe keeps last 32 ids
  (Spotter queue is 2 slots; a wake burst is a handful). Rejected
  commands are NOT recorded in dedupe (no state change; keeps file lean).
  Corrupt/out-of-table loads reset per-key to defaults, loudly. Full
  suite: 201 tests OK (64 new across the three §1 modules).

- 2026-07-26 **§1 parser + ack builder landed (`command_messages.py`).**
  Contract decisions (smallest-surface defaults, flag if wrong):
  (1) *Unackable vs ackable rejects:* bad JSON or bad/missing `id` can't
  be correlated → drop + log, no ack. Bad command name or value index →
  ack `{"id":N,"ok":0,"e":"cmd"|"val","st":{...}}`. Error codes: json /
  id / cmd / val. (2) *`id` bounded to uint32*, bools rejected as ids and
  values. (3) *Unknown extra JSON keys tolerated* (forward compat; can't
  mis-apply a setting). (4) *ping normalizes missing `v` to 0*; settings
  commands require `v`. (5) Ack always carries the complete 5-key `st`
  (defaults fill gaps) per D4; worst-case ack ~70 B, fits one 384-char
  Sprint09 chunk with margin. Duplicates will ack ok=1 with no special
  flag — `st` already tells the truth (D4 keeps re-sends safe).

- 2026-07-26 **§1 `command_tables.py` landed.** ROI presets computed as
  exact centered rects per Q3 (index 0 == the S07-validated production
  default 1504,846,1600x900; the SPEC's "placeholder" rects for 1–4 are
  now concrete centered 16:9 rects — still flagged for final framing
  review before deployment). **Placeholders needing Nick/tank-test
  sign-off before field use:** foc manual lens positions (in-air dioptre
  guesses; flat-port water shifts effective focus), awb underwater gains
  (1.8, 1.2 guess), exp EV step list. Validation detail: `valid_value`
  rejects bools explicitly (JSON `true` would otherwise alias index 1 —
  Python bool is an int subclass). Tables carry `TABLES_VERSION` for
  GUI/device revision matching.

- 2026-07-26 **§0 setup complete (session).** Sprint numbering confirmed:
  sprints/ holds 02–09, so 10 is correct — no renumber. Prior-daemon-spec
  search: none exists; the `bm-daemon.service` strings in README.md and
  Sprint03 refer to an unrelated legacy systemd service that gets
  *disabled* during manual testing — nothing to reconcile with SPEC.md.
  Branch `bm_commands` created from origin/development @ d44f535
  (Sprint09 merge). Open-question triage: Q1–Q10 answered; Q11 (Sofar
  cloud downlink) remains the sole open blocker, scoped to §7 + Phase C/D.

- 2026-07-27 **Scope addition (Nick, pre-kickoff): operator GUI + Phase D
  automation are part of v1 "done".** Five-item definition of done added
  to SPEC (GUI with SPOT-ID/node targeting, preset-only dropdowns,
  send/in-flight feedback, verified ACK display, and an automated 3–5
  permutation end-to-end test that must pass before Nick's final manual
  acceptance). DESIGN D9/D10 bound the GUI to MVP: local Mac-served,
  dropdowns generated from `command_tables.py`, lifecycle states to
  prevent queue-stuffing. TRACKER gained §7 (GUI), §8 (Phase D), §9
  (final acceptance + wrap). New blocker logged as Q11 (downlink
  mechanism).
- 2026-07-27 **Branch corrected to the repo Branching Model:** work on
  `bm_commands` off `development`, PR into `development` (was
  `sprint-10-command-daemon` off main, written before the model was
  adopted in Sprint09). DESIGN + TRACKER + worker prompt updated.
- 2026-07-27 **Sprint09 carry-forward facts for this sprint:** locked
  uplink values 384 chars / 1.0 s / 0x02 (acks ride the ≤~400 B fast
  drain path — a full-state ack is well under this); Spotter-side drops
  are SILENT to the Pi with only 2 queue slots — validates D4 (dedupe +
  cloud re-send) as required, not optional; backend visibility lags by
  minutes (Notecard batch sync) — ack polling must tolerate multi-minute
  latency and never treat "not seen yet" as "not delivered"; bench unit
  bmcam003 + SPOT-33507C is registered, disarmed (cron off, halt off),
  and running `development` — ready for this sprint.

## Bugs / issues

*(empty — append with repro steps; move to tracker if they block)*

## Scratch / incidental findings

- 2026-07-26 (Nick, via Q7): a **customer-facing web UI** for sending
  commands is planned — it will expose only preset options. Command
  origin is therefore always preset-constrained end to end; keep the
  enum-index contract stable since the UI will build against it.

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
