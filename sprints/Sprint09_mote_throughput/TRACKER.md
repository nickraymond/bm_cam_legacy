# SPRINT 09 — TRACKER

Checklist only. Rationale lives in SPEC.md; decisions in DESIGN.md;
open questions and bugs in DEV_LOG.md.

## 0. Setup
- [x] Create feature branch `sprint-09-uart-throughput` (2026-07-26, from origin/main c6afba8)
- [x] Answer/triage open questions in DEV_LOG.md with Nick (Q1–Q3 gate Phase A/B)
      — Q1–Q3 pre-sprint; Q4 + Q6 answered 2026-07-26 (see DEV_LOG); Q5
      default stands (keep cap, observe Phase C)
- [x] Confirm bench unit identity + which `device_profiles/` YAML it runs
      — bmcam003 (100.103.35.24) + SPOT-33507C; deployed YAML derived from
      `rc_field_template` (no bmcam003 profile dir yet); backend/website
      registration deferred until local hardware testing is done

## 1. Config plumbing (no hardware)
- [x] `BristlemouthSerial.__init__`: read `uart_port`/`baudrate` from YAML
      when `uart is None` (defaults unchanged; framing untouched)
      — `load_uart_config()` + `tests/test_bm_serial_uart_config.py`
- [x] Sanity-check `rc_time_budget.py` math with ~980-char chunks
      (message_cap 195 / max_run_time interaction — SPEC "Interaction" note)
      — no code change needed; numbers table in DEV_LOG 2026-07-26
- [x] Mirror schema into `device_profiles/*/camera_schedule.yaml` + field template
      — nothing to mirror: no new keys this sprint; `uart_port`/`baudrate`
      already present in all 4 profiles + main YAML (pinned by new tests)
- [x] Smoke test: RC cycle dry-path with unchanged values (no regression)
      — full off-device suite 137 passed (was 130); on-Pi UART open via the
      new path still needs §2 deploy verification (can't open serial on Mac)

## 2. Test prep (bench)
- [x] Deploy `test_UART_throughput.py` next to `bm_serial.py` on the Pi
      (2026-07-26; updated §1 bm_serial.py deployed alongside — constructor
      verified opening /dev/ttyAMA0 @ 115200 via YAML on-Pi)
- [x] Back up crontab; disable `@reboot` RC cron for test window
      — backup `/home/pi/crontab_backup_sprint09_20260726T162224.txt`
- [x] Confirm `power_halt` is `enabled: false` / `dry_run: true` on bench unit
      — flipped from field values (enabled/real); backup
      `/home/pi/camera_schedule_backup_sprint09_20260726T162224.yaml`
- [x] Confirm nothing else holds the UART (no camera process running)
      — pgrep NONE, serial-getty disabled, kernel console removed
- [x] Pi UART hygiene: PL011 not mini-UART (`enable_uart=1`, `dtoverlay=disable-bt`)
      — was BROKEN (no /dev/ttyAMA0; fresh-provision gap, see DEV_LOG bug);
      fixed 2026-07-26 with boot backups in `/home/pi/boot_backups_sprint09/`

## 3. Phase A — link integrity (no quota)
- [x] Run: `--phase log --count 200 --size 300 --gap-ms 0` (run S09A1,
      2026-07-26: 200 msgs / 60 kB in 6.23 s, ~9.6 kB/s effective)
- [x] Pull `uart_test.log` via Spotter CLI (`cat` + terminal logging, or
      `sd usb`) — file lands at `/bm/<mote node id>/NNNN_uart_test.log`,
      NOT SD root; pulled via scripted `cat` over the USB CLI
- [x] Verify 200/200 sequential, CRC-clean; record result in DEV_LOG
      — PASS; artifacts in `runs/20260726_phaseA_S09A1/`

## 4. Phase B — payload ceiling + pacing floor (cellular quota)
- [x] Review Nick's Sofar API tooling; document count query in DEV_LOG
      — CORRECTED per Nick 2026-07-26: proven path is `api/sensor-data`
      (not `api/raw-messages`/EA); counter script `count_phase_b.py` in
      this folder; details in DEV_LOG Q2 update
- [x] B1 size probe: 5 msgs each at 900/1000/1100/1200 B, gap 5000 ms;
      largest 100%-delivery size = chunk ceiling
      — ceiling = 1000 B exactly (1100+ rejected pre-queue); NOTE gap 5000
      was itself lossy at ~1 kB (4/5) — see B2
- [x] `post` check between B1 steps (cellular error states both "OK")
- [x] B2 sweep — RE-SCOPED (DEV_LOG spec correction): size×gap matrix
      instead of single-size sweep, because drain behavior is
      size-dependent. Zero-loss: 1000B@6s, 300B@500ms, 400B@625ms (B2b);
      lossy: 500B@2s (40%), 300B@250ms (80%)
- [x] Count arrivals per run-id in Sofar backend; log per-gap table in DEV_LOG
      — RECONCILED 2026-07-27: backend counts match console submit counts
      exactly on all 15 bursts, 0 CRC failures (103 rows);
      `runs/20260726_phaseB/backend_reconciliation.json`. Console accept/drop
      is a validated delivery proxy.
- [x] Watch Spotter/mote console for queue-full errors during fast steps
      — console capture WAS the primary metric (Nick's suggestion); queue
      holds 2 pending, drops are silent to the Pi
- [x] Compute floor + 25% margin → proposed `image_transmit_delay_seconds`
      — PROPOSAL: `image_buffer_size: 384` + `image_transmit_delay_seconds:
      0.625` (392 B payload on the <=400 B fast path; 500 ms floor + 25%).
      ~8× payload throughput. Fallback 300/1.0 s. Phase C validates sustained.
- [x] Log total quota spend per run in DEV_LOG — ~52 kB submitted total
      (B0+B1+B2+B2b); per-run numbers in runs/20260726_phaseB/

## 5. Phase C — end-to-end
- [x] Set new YAML values (chunk from B1, delay from B2) on bench unit
      — 384 chars; delay iterated 0.625 → 1.0 s across C1/C2/C3 (see DEV_LOG)
- [x] One real RC capture+send; record awake time, message count, image integrity
      — C3 (deciding run): 117.7 s awake, q90, 31,478 B, 110 msgs,
      113/113 accepted, queue depth never >1, zero drops
- [x] Compare vs ~16 min baseline; PASS/FAIL against SPEC success criteria
      — **PASS**: 1.96 min vs 16.3 min (8.3×), quality q90 vs q9–15,
      no queue errors on the deciding run; C1 (625 ms) documented as the
      sustained-rate failure case that set the 1.0 s floor

## 6. Lock-in + wrap
- [x] Land measured values in `BM_Devel_Pi/camera_schedule.yaml` + device profiles
      — 384 / 1.0 in main YAML + rc_field_template + bmcam000 profile;
      legacy bmcam001/002 untouched; 137 tests pass
- [x] Deploy via `tools/deploy_rc_runtime.sh`; update manifest if needed
      — bmcam003 formally deployed at 43f4248 via new
      `tools/rc_field_update.sh` (live end-to-end test of the tool,
      2026-07-27); manifest unchanged (no file-set change). bmcam000
      update is next session's work (needs PR #10 → development merge)
- [ ] Restore crontab; verify unit back in known-good state
      — DELIBERATELY LEFT DISARMED per Nick 2026-07-26/27 (dev-friendly
      state for continued bench work: cron off, halt off). Restore
      commands in DEV_LOG §2 record; run them before any soak/field use
- [x] Rollback commands documented in DEV_LOG (§2 record + three-value
      YAML rollback: 300 / 5 / 0x02-stays)
- [x] Update DEV_LOG with findings, bugs, deferred items (incl. firmware tier)
- [x] PR with test evidence for Nick review — PR #10 (approved pre-Phase-B;
      Phase B/C evidence pushed + summarized in PR comment)
