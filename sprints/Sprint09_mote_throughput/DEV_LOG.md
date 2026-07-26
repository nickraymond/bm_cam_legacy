# SPRINT 09 — DEV_LOG

Running record: open questions, decisions taken mid-sprint, bugs, and
incidental findings. Newest entries at top within each section.

## Open questions (for Nick — raised 2026-07-26, pre-sprint)

- **Q1 — Spotter SD access procedure.** ✅ ANSWERED 2026-07-26 (Nick):
  the Spotter USB terminal has a filesystem CLI — `ls`/`cd`/`cat`/`head`/
  `tail`, plus `sd usb` to mount the SD on the Mac read-only. Full command
  list: [docs/spotter_cli_reference.md](../../docs/spotter_cli_reference.md).
  Phase A: `cat uart_test.log` with terminal logging, or `sd usb` + copy.
- **Q2 — Sofar backend count method.** ✅ ANSWERED 2026-07-26 (Nick):
  Nick has tooling that wraps the Sofar API for raw message viewing.
  Decision: review his endpoints at §4 prep (Phase B), not now — not a
  §1–§3 blocker. Forum t/575: cell-only messages appear at the
  `api/raw-messages` endpoint with header `EA` (legacy = `DE`), so the
  count target is EA-header messages per run-id.
- **Q3 — Phase B payload-size / throughput targets.** ✅ REFRAMED
  2026-07-26 (Nick): 300 B/msg cellular-only is known-good; goal is
  pushing to the ~1000–1200 B/msg the forum discusses
  (bristlemouth.discourse.group/t/575). The true cellular throughput cap
  is unknown — answer empirically. Phase B therefore gets a payload-size
  probe (step sizes 900 → 1000 → 1100 → 1200 at a safe gap, small counts)
  before the pacing sweep, to find the accept/reject boundary. Known
  reference: bm_core pins `spotter_tx_max_cellular_payload_bytes 1000`;
  forum staff say "~1000 bytes". Expect ≥1000 to fail; probe proves it.
  No hard quota cap set — keep counts modest, log spend per run.
- **Q4 — Bench unit identity.** ✅ ANSWERED 2026-07-26 (Nick): bmcam003
  (100.103.35.24) on the bench, talking to Spotter SPOT-33507C (Mac USB
  port `usbmodemSPOT_33507C1`). No `device_profiles/bmcam003/` dir exists —
  deployed YAML came from `rc_field_template` at provision (verified
  on-Pi: bm_serial block + uart keys match template). Spotter + camera
  backend/website registration deliberately deferred until local hardware
  testing is complete (Nick 2026-07-26).
- **Q5 — message_cap / ladder retune.** With ~980-char chunks the quality
  selector will land much higher quality for the same 195-message cap.
  Keep cap at 195 and let quality float up, or retarget? *Default: keep
  cap, observe Phase C, retune next sprint.*
- **Q6 — Alert/status traffic on 0x01.** ✅ CHECKED 2026-07-26 (§1), with a
  finding for Nick: WS wake-status telemetry in the RC path
  (`send_wake_status` → `process_image_v2.send_compact_text_message` →
  `spotter_tx` with no per-call override) rides the instance default
  network_type — **0x02 cellular-only** on RC config, not the 0x01 that
  DESIGN D5 says status traffic should use. It delivered fine during the
  Sprint08 soak, so no urgent bug — but it contradicts D5's stated intent.
  `spotter_tx(data, network_type=...)` already supports a per-call
  override, so forcing WS to 0x01 is a one-line change in
  `send_compact_text_message` if wanted. **DECIDED 2026-07-26 (Nick): keep
  0x02 cellular-only for everything on RC config, WS included — working
  and tested; no change.** Nothing else in the RC path sends via
  spotter_tx (callers: RC image chunks/START/END/a=inc via the tx
  callable, WS telemetry, and the HEIC path's own sends).

## Known constraints (carried in)

- Spotter transmit queue 32 deep (cell+sat combined); overflow errors.
  Cellular 1000 B/msg, Iridium 311 B (bm_core `integrations/spotter.c`).
- Mote `serial_bridge` hardcodes 115200; baud gains need reflash (out of
  scope — compile-tier list in `uart_speedup_spec.md` §2).
- Bench cadence: Spotter power-cycles the node; `@reboot` cron runs one RC
  cycle then (in soak config) halts. Cron must be disabled during manual
  UART tests and restored after (CLAUDE.md field-ops rules).
- `image_buffer_size` is base64 CHARS not raw bytes: 300 chars = 225 raw
  bytes. Wire message = `<I{i}>` + chunk.

## Decisions taken mid-sprint

- 2026-07-26 **Phase A PASS (run S09A1).** 200 msgs × 300 B at gap 0:
  200/200 on the Spotter SD, exact order, zero CRC8 failures. Sender: 60 kB
  in 6.23 s (~9.6 kB/s effective ≈ 83% of the 115200 wire incl. framing).
  Spotter-side arrival spacing ~31 ms/line. The link is NOT the bottleneck —
  exactly as SPEC predicted; the pacing floor question is all Spotter-queue
  (Phase B). Artifacts: `runs/20260726_phaseA_S09A1/` (manifest, raw
  captures, verify JSON). Two verification gotchas for the record:
  (1) `spotter/fprintf` files land at `/bm/<bridging mote node id>/` with a
  boot-count prefix (`0139_uart_test.log`), not SD root, and not under our
  Python-side node_id (c0ffeeeef0cacc1a); bench mote = 53171fa3d81a8e6f.
  (2) A 65 kB `cat` over the USB CLI can drop bytes Mac-side — first pull
  "lost" seq 74, second pull was 200/200 clean. Re-pull before believing a
  gap.
- 2026-07-26 **Branching model adopted (Nick):** `main` = released code;
  new `development` branch = integration target for all feature/sprint
  PRs; bench Pi runs the active sprint branch during testing,
  `development` otherwise; `bm_commands` will be the Sprint10 feature
  branch off `development`. `development` created at origin/main f25e23d.
  Rule recorded in CLAUDE.md "Branching Model"; Sprint09 DESIGN PR gate
  now targets `development`.
- 2026-07-26 **§2 bench prep complete on bmcam003 — full record + rollback.**
  Sequence (SSH as pi@100.103.35.24; all backups timestamped
  `20260726T162224`):
  1. Crontab backed up → `/home/pi/crontab_backup_sprint09_20260726T162224.txt`;
     `@reboot` RC line commented out. In-flight boot cycle killed via
     SIGTERM before its finally-halt could run.
  2. Deployed YAML backed up →
     `/home/pi/camera_schedule_backup_sprint09_20260726T162224.yaml`;
     `power_halt` flipped to `enabled: false` / `dry_run: true` (was field
     values enabled/real — bench-safe dev state per DESIGN).
  3. UART boot fix (Nick-approved; see Bugs): backups →
     `/home/pi/boot_backups_sprint09/{config.txt,cmdline.txt}.20260726T162224`;
     appended `dtoverlay=disable-bt` to config.txt, removed
     `console=serial0,115200` from cmdline.txt, hciuart n/a, rebooted.
  4. Verified post-reboot: `/dev/serial0 → ttyAMA0`, no serial console, no
     camera processes, serial-getty disabled, CmaTotal 262144 kB, and
     `BristlemouthSerial()` opens `/dev/ttyAMA0` @ 115200 through the new
     YAML path (network 0x02) — closes §1's untested on-Pi gap.
  ROLLBACK (restore field state): `crontab
  /home/pi/crontab_backup_sprint09_20260726T162224.txt`; `cp
  /home/pi/camera_schedule_backup_sprint09_20260726T162224.yaml
  /home/pi/BM_Devel_Pi/camera_schedule.yaml`; `sudo cp
  /home/pi/boot_backups_sprint09/config.txt.20260726T162224
  /boot/firmware/config.txt` + same for cmdline.txt; `sudo reboot`.
  (Note: boot-config rollback would re-break the BM link — the UART fix
  should become permanent fleet config, not be rolled back.)
- 2026-07-26 **§1 landed; TRACKER §1 all checked.** `bm_serial.py` gains
  `load_uart_config()` (top-level `uart_port`/`baudrate`, same keys as
  `spotter_time_sync.py`); constructor uses it when `uart is None`. Any
  missing/invalid config falls back to the old hardcoded `/dev/ttyAMA0` @
  115200, so worst case is exactly the pre-change behavior. Framing
  (header/CRC/COBS) untouched. Pinned by `tests/test_bm_serial_uart_config.py`
  (all 4 device profiles + repo YAML resolve to the committed defaults).
  Off-device suite: 137 passed. NOT tested: real UART open through the new
  path — needs the Pi at §2 deploy.
- 2026-07-26 **rc_time_budget sanity check (SPEC "Interaction" note):
  no code change needed.** `CycleBudget` is pure `n × seconds_per_message`
  math, chunk-size-agnostic. Numbers (cap 195 + START/END, budget 16 min):
  - delay 5 s (today): budget binds at ~190 msgs, just under the cap —
    unchanged from S07's measured 16.38 min. Max image at cap with
    980-char chunks: ~140 KB raw (vs ~43 KB at 300 chars).
  - delay < ~4.9 s: the 195 cap becomes the binding constraint; quality
    floats up (the point of the sprint). Full 197-msg send: 8.2 min @2.5 s,
    2.05 min @625 ms, 1.03 min @312.5 ms → SPEC's 1–2 min awake target
    implies a Phase B floor ≤ ~600 ms.
  - `max_run_time_min: 16` goes slack once delay drops — keep as safety
    backstop (Q5 default stands: keep cap, observe Phase C).
  - 980 % 4 = 0 → chunk boundaries stay b64-aligned; 70 KB JPEG = 98 msgs
    @980 (matches SPEC's ~96–98 estimate).

## Bugs / issues

- 2026-07-26 **bmcam003 provisioned without BM UART boot config — transmit
  never worked on this unit.** Fresh SD flash + `bmcam-provision` skill +
  `deploy_rc_runtime.sh` leave OS defaults: no `dtoverlay=disable-bt`, so
  PL011 stays on Bluetooth, `/dev/ttyAMA0` doesn't exist, `/dev/serial0 →
  ttyS0` (mini-UART), and `console=serial0,115200` sprays kernel messages
  on the header pins. Every `BristlemouthSerial()` open raises
  SerialException; the unit's only 2 RC cron cycles (both 2026-07-26) died
  before transmit, and the provision validation ladder never exercised
  transmit, so it passed silently. NOT a regression — the unit never had
  the working config; known-good units (bmcam000) have it. **Fixed on
  bmcam003 2026-07-26** (see §2 record below). **Root-cause fix**: add the
  UART boot step + a transmit check to the provision skill/validation
  ladder (spawned as a separate task, 2026-07-26).

## Scratch / incidental findings

- 2026-07-26 **Spotter Mac terminal connection (from Nick, screenshot):**
  port `usbmodemSPOT_33507C1` (→ `/dev/cu.usbmodemSPOT_33507C1`), 115200
  8N1, no HW flow control, XON/XOFF software flow control on, DTR/RTS on
  at open. Port name carries the Spotter ID suffix `33507C1`. Phase A log
  pull: use the terminal app's File Capture while `cat uart_test.log`.
- 2026-07-26 `device_profiles/bmcam001` and `bmcam002` have **no
  `bm_serial:` block** (legacy HEIC profiles; pre-existing). They fall back
  to code defaults: network_type 0x01 fallback queue. Out of Sprint09
  scope — do not add the block to field-unit profiles without Nick.

- 2026-07-26 **network_type numbering discrepancy — flag, do not "fix".**
  Forum t/575 (Sofar staff) describes network types as **0** = cell/Iridium
  fallback and **1** = cellular-only, and says "type 2 does not exist and
  causes transmission failures." Our repo uses **0x01** = fallback and
  **0x02** = cellular-only (`bm_serial.py`, comments say "observed as
  MS_Q_CELLULAR_ONLY"), and 0x02 was delivering during the Sprint08 soak.
  Possible off-by-one in naming (wire byte vs. "type" label) or firmware
  version difference. **Trust the repo's validated bytes; Phase B delivery
  counts settle it empirically.** If Phase B shows silent loss at 0x02,
  test 0x01/0x00 before blaming pacing.
- 2026-07-26 `post` on the Spotter CLI shows `cellularSignalErrorState` /
  `cellularErrorState` — both "OK" means data is reaching Sofar (t/575).
  Use as a live check during Phase B sweeps.
- 2026-07-26 Spotter CLI has `bridge baudrate <57600|115200|1M>` — whether
  this governs the mote↔Pi payload UART is UNVERIFIED; investigate when
  the firmware-tier baud work starts (could shrink that project).

- 2026-07-26 (pre-sprint audit): v2 spec's §1a/§1b were already landed by
  Sprint08 — `network_type: 0x02`, `image_buffer_size`, and
  `image_transmit_delay_seconds` are live YAML keys read by
  `rc_progressive_jpeg.py`. Remaining code work is only the
  `BristlemouthSerial` port/baud constructor read. See SPEC "Corrected
  repo facts" table.
- Script filename is `test_UART_throughput.py` (capital UART) — v2 spec
  says `test_uart_throughput.py`. Docs here use the actual filename.
