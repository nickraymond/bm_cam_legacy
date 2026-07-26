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
- **Q4 — Bench unit identity.** Which bmcam is on the bench (bmcam000?),
  and does its `device_profiles/` YAML match the deployed
  `/home/pi/BM_Devel_Pi/camera_schedule.yaml`? *Verify before editing.*
- **Q5 — message_cap / ladder retune.** With ~980-char chunks the quality
  selector will land much higher quality for the same 195-message cap.
  Keep cap at 195 and let quality float up, or retarget? *Default: keep
  cap, observe Phase C, retune next sprint.*
- **Q6 — Alert/status traffic on 0x01.** SPEC says non-image traffic
  stays sat/cell fallback. Confirm nothing else in the RC path sends via
  `spotter_tx` with the image network_type. *Check during §1.*

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

*(empty — append as they happen, with date + one-line reason)*

## Bugs / issues

*(empty — append with repro steps; move to tracker if they block)*

## Scratch / incidental findings

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
