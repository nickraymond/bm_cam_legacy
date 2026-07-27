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

## Answers (updated during sprint)

**Q1 CORRECTION (Phase B, bmcam003 bench, 2026-07-27).** The wire is
ASYMMETRIC. Pi→mote is COBS + 0x00-delimited as documented. But
**mote→Pi arrives RAW**: pub packets with zero bytes inline, no COBS,
no delimiter, frames back-to-back — captured with a raw UART dump while
publishing from the Spotter console. This is why production only ever
needed a pattern-scan and why the original strict COBS inbound decoder
counted cobs_errors on real traffic. Verified structure (CRC16 checked
against three captured frames): type 0x02, flags 0x00, CRC16 LE at
[2:4] over the whole frame (bytes 2–3 zeroed), PUBLISHER node id u64 LE
at [4:12] (= Spotter bridge c3c564b91856226c for `bm pub`), 01 01,
topic len u16, topic, payload — NO payload length field; frame end is
recoverable only via CRC scan. Decoder rewritten as RawPubScanner
(CRC-scan end detection); captured bytes are pinned as test vectors in
tests/test_bm_frame_decoder.py.

**`bm pub` verified as the Phase B injection path:** `bm pub bmcam/cmd
{"id":101,"c":"ping"} 1 1` — compact JSON (no spaces) passes through
LITERALLY (not hex-decoded, quotes intact); type/version `1 1` works.
Console→Pi one-way latency ~81 ms (NTP-synced clocks).

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

- **Q11 — Sofar cloud downlink mechanism.** ✅ ANSWERED 2026-07-27
  (Nick supplied Sofar's "Spotter Command API Reference Document";
  digitized to [docs/sofar_command_api_reference.md](../../docs/sofar_command_api_reference.md)).
  Mechanism: `POST /user-rest/devices/:spotterId/command` with
  `{telemetry: "cellular", message: "<Spotter console command>"}` —
  the message is a console command line (max 270 bytes, printable ASCII
  + `\n` chaining, no tabs), so the send path is the cloud queuing our
  bench-proven `bm pub bmcam/cmd <json> 1 1`. Cellular mailbox: no
  expiry, no queue limit, no satellite credits. Rate limit 1 successful
  request/min/Spotter (all requests rejected during cooldown — GUI must
  enforce client-side). Delivery on the Spotter's next successful
  cellular transmit (matches queue-while-off model). **Remaining
  caveats:** (1) the capture's "Example cURL Request" and "Responses"
  toggles were collapsed — auth header + response/error schemas missing
  (assume Sprint09's api.sofarocean.com token auth; verify at first
  Phase C send); (2) `bm pub` executing from the command mailbox is
  inferred from the doc's `cfg …` example — first Phase C test is a
  single cloud ping to prove it.

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

- 2026-07-27 **Phase C started — first cloud command enqueued; Spotter
  reboot instability discovered on the bench.** Nick supplied the doc's
  Responses section (202 = enqueued-not-executed; 400 with reason) and
  authorized cellular-only cloud commands (satellite disabled on the
  account). New tools/sofar_send_command.py (23 unit tests) sent ping
  id=801: **HTTP 202, auth via ?token= confirmed working** — the
  response echoes the full console line, so the endpoint/format is
  right. Delivery pending the Spotter's next successful cellular
  transmit; Pi runs back-to-back --bench-commands cycles as listener.
  **Incidents (all in runs/sprint10_phaseC_20260727/):** the Spotter
  (fw v2.16.6) rebooted at 17:21:11Z and again 17:25:52Z ("Running
  health check!" → "[SYS] [ERROR] rebootctl reset 2. Source: 7"), and a
  `cfg save` at 17:34:14Z also triggered an immediate reboot; console
  then showed **"Reboot limit reached, ignoring. (source 7)"** —
  something requests source-7 reboots repeatedly and the Spotter now
  suppresses them. EVERY Spotter reboot cuts BM bus power → hard
  power-cycles bmcam003 (observed 3× today; the RC state file survived
  each, as designed). Phase B overnight had zero such resets — this
  behavior is new today; possibly related to whatever Nick observed
  pre-16:16Z ("unit looked off"). Raise with Sofar if it persists.
  **Bench config change (Nick request, on the record):** visibility LED
  disabled — `cfg vle 0` + `cfg save`, persisted through reboot,
  verified `cfg vle` → 0. **Restore `cfg vle 1` before deployment.**

- 2026-07-27 **Q11 closed — Sofar Command API doc digitized** (new
  session, post-PR-#15-merge). Nick supplied a screen capture of Sofar's
  Notion "Spotter Command API Reference Document"; transcribed to
  `docs/sofar_command_api_reference.md` (source PDF was image-only —
  visually transcribed, includes an implications-for-Sprint10 section).
  Two Notion toggles ("Example cURL Request", "Responses") were
  collapsed in the capture and are flagged missing — ask Nick for a
  re-capture with toggles expanded, or discover auth/response shape
  empirically at the first Phase C send. §7's first tracker item
  (confirm downlink mechanism) is now satisfied on paper; the empirical
  half (a cloud ping actually reaching the Pi) is §6 Phase C test 1.

- 2026-07-27 **PR #15 finalized for review/merge (Nick). Handoff notes
  for the next session (Sofar cloud integration — Nick will supply
  Sofar's API docs for submitting messages over cellular, answering
  Q11):**
  - *What the downlink must produce:* a BM pub on topic `bmcam/cmd`
    (YAML-configurable, `bm_commands.topic`) with a compact-JSON
    payload `{"id":N,"c":"roi","v":2}` — the bench used `bm pub
    bmcam/cmd <json> 1 1` (type 1, version 1). Key question for the
    Sofar API: what topic/type/version does a cloud-submitted message
    arrive on at the node, and is it configurable?
  - *Ack read-back:* acks arrive at `GET api.sofarocean.com/api/
    sensor-data?spotterId=...` as hex-encoded `value` fields;
    `bytes.fromhex(value).decode()` yields the ack JSON. Observed
    backend lag 13–30 min (Notecard batch sync) — the GUI poller must
    tolerate it. Bench evidence: 38/40 unpaced then 12/12 paced acks
    reconciled this way.
  - *Device side is done and merged-ready:* daemon listens each wake
    (pre-capture window 120 s default), dedupes by id, applies between
    captures, persists across hard power cycles, acks at 1.0 s pacing.
  - *Open for that session:* Q11 mechanics, §6 Phase C (on/off/burst
    via cloud), §7 GUI (dropdowns generate from command_tables.py;
    lifecycle states per D10), §8 Phase D permutations.

- 2026-07-27 ~16:30Z **Hard power cycle PASSED; "unit halted itself"
  ruled out.** Nick reported bmcam003 apparently off ("halt command
  still running?") and hard-cycled the ebox. Forensics after boot:
  (1) every overnight cycle logged `power_halt.enabled=false; skipping
  halt` (dry_run also true — double guard); nothing ran after 07:07Z;
  (2) the new boot's pre-NTP clock was 16:16:05 (fake-hwclock's last
  save — only written while running) with NTP stepping to 16:26:45 ⇒
  the Pi was ALIVE at ~16:16Z and dark only during the 16:16–16:26
  cycle window itself; (3) Spotter `bridgePowerControllerEnabled=0`,
  battery 23.89 V (unchanged overnight) — no Spotter-side cutoff.
  Conclusion: no self-halt occurred; whatever looked "off" pre-cycle
  was not a halt (ask Nick what he observed — possible tailnet/LED
  visibility red herring). **Value extracted: the cycle completed §5's
  hard-power-cycle test — bm_command_state.json survived byte-intact
  (settings + touched + all 32 dedupe ids).** Unit state post-check:
  cron still disabled, no processes, Tailscale healthy.

- 2026-07-27 **Backend ack reconciliation caught a real bug; fixed +
  re-verified on hardware.** `api/sensor-data` showed **38/40** bench
  acks (~13–30 min Notecard sync lag; both acks for the duplicate id
  201 arrived). Missing: **605 and 616 — both mid-rapid-burst**, i.e.
  silent Spotter 2-slot-queue overflows on the ACK path (Sprint09's
  documented drop mode, now observed first-hand; unpaced `drain_acks`
  was sending ~5–6 acks/s against a ~1.27 msg/s sustained drain).
  **Fix (commit 1455f31):** `drain_acks` paces at `ack_interval_s`
  (default 1.0 s, the Sprint09-locked floor; injectable clock keeps
  fake-time tests deterministic); cycle shutdown flushes pending acks
  paced with a 15 s bound and LOUDLY logs any left (cloud re-send +
  dedupe recover them). **Hardware retest (run 7):** 12-command rapid
  burst → 12/12 applied in order, 12 acks at exactly 1.0 s gaps, zero
  pending at halt. **Backend confirmed 12/12 retest acks delivered**
  (~30 min sync lag) — with pacing, ack loss went from 2/12 to 0/12 on
  the identical burst. Full chain proven: console `bm pub` → mote → Pi
  UART → parse/dedupe/persist → paced ack → Spotter cell queue →
  cellular → `api/sensor-data`. Suite 280 OK. GUI note (§7): ack
  polling must tolerate the observed 13–30 min backend lag.

- 2026-07-27 **Phase B bench COMPLETE (overnight, Nick-authorized) —
  all six commands verified end-to-end on hardware.** Full results
  table + artifacts: `runs/sprint10_phaseB_20260727/RESULTS.md`.
  Highlights: 40/40 command deliveries (5 cycles), acks observed
  entering the Spotter cell-only queue, roi drove THIS-cycle capture
  crop, foc/awb/exp verified in the literal rpicam command line, win
  correctly next-cycle (720 s budget), burst 12/12 in order, factory
  reset clean, shared-port time sync 9.8 s, latency median 159 ms
  (45–262, n=40). Wire-format finding logged under Answers (Q1
  correction) — decoder rewritten mid-session and redeployed.
  **Cosmetic nit found:** the cycle-end log line prints the RE-OVERLAID
  budget ("of 720s") while the running CycleBudget kept the cycle-start
  value — fix with a stored copy or label; harmless.
  **Not tested:** hard power cycle (remote reboot blocked by session
  permissions — physical pull needed), --transmit ack-in-pacing-slots
  on hardware (cellular image spend), backend ack visibility (the 40
  bench acks may already be at api/sensor-data — check in the morning;
  they'd be the first data for GUI ack-polling work, §7).
  **bmcam003 end state:** disarmed as found; bm_commands @ 812c825
  deployed; island ENABLED in live YAML (backup
  camera_schedule_backup_sprint10_20260727T062014Z.yaml); state file
  factory-zero; no stray processes.

- 2026-07-26 (late) **§2c/§4 complete — daemon wired into the RC cycle;
  Phase A green. §1–§4 all checked.** Cycle flow when enabled:
  daemon owns the UART from process start (shared-port time sync via
  new `read_spotter_utc_fn` param on should_transmit_now_from_schedule —
  additive, default unchanged) → pre-capture listen window → capture
  with overlaid settings → transmit with ≤1 ack per 1.0 s pacing slot
  (each ack consumes its own paced sleep; image framing byte-identical)
  → final drain → stop → early halt. New `--bench-commands` flag runs
  the daemon without image transmit (LOUD: subscribe+acks DO touch the
  bus) — built for tonight's bench work. New `rc_command_hooks.py`
  keeps the orchestrator near the line rule (675 lines, was 600
  pre-sprint; hooks extracted). `tools/mock_mote.py` PTY harness
  (raw-mode pty — tty line discipline corrupts COBS frames otherwise;
  pyserial does raw implicitly on real ports). Full-cycle integration
  tests on the coral native. Suite: 269 OK.
- 2026-07-26 (late) **Deviation note:** Nick's overnight instruction
  said "commit changes to the development branch"; per the Branching
  Model (no direct commits to development) the bm_commands BRANCH is
  pushed and deployed to bmcam003 instead. Same effect, model intact.

- 2026-07-26 **§3 `command_bindings.py` + touched-tracking + `--ev`.**
  Design point discovered while wiring the overlay: bmcam000-class units
  set manual focus via the YAML camera_controls island, so a naive
  "state always overlays" would have silently forced autofocus (default
  foc=0) on them — a real field regression. Fix: `command_state` now
  records a `touched` set (which keys were EVER commanded); only touched
  keys overlay, and commanding index 0 is an explicit "auto wins over
  YAML". A stale out-of-table stored value resets AND un-touches its
  key. Ack `st` semantics documented: index values reflect last
  commanded state, 0 = never-commanded-or-default (YAML may still hold
  a manual value — GUI should label st as command-space, not full
  camera truth). Also: `--ev` support added to process_image_v2's
  exposure island (additive; the Q2 audit listed it as the exp-command
  flag but only shutter/gain existed). Bindings tests drive the REAL
  _camera_controls_from_settings builder and pin exact rpicam flags.

- 2026-07-26 **§2b `command_daemon.py` — CommandDaemon + bm_commands
  YAML island loader.** Concurrency contract implemented exactly as
  reviewed: reader thread ONLY reads (frames → queue; rolling 4 KB raw
  buffer for the proven clock pattern-scan); every write and every
  state mutation happens on the main thread (subscribes, process_pending,
  drain_acks) — single-writer without locks. `wait_for_spotter_utc()` is
  the D11 shared-port replacement for `read_spotter_utc` (same subscribe
  frame + detection logic, byte-for-byte). start() refuses a uart with
  no read timeout (reader would block forever — fail loudly at t=0).
  Persist-failure → no ok ack (D15); ack send failure requeues. Island
  loader has a line-based fallback parser for hosts without PyYAML
  (same convention as spotter_time_sync; dev Macs lack yaml, the Pi has
  it). Suite: 241 OK.

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
