# SPRINT 09 — TRACKER

Checklist only. Rationale lives in SPEC.md; decisions in DESIGN.md;
open questions and bugs in DEV_LOG.md.

## 0. Setup
- [x] Create feature branch `sprint-09-uart-throughput` (2026-07-26, from origin/main c6afba8)
- [ ] Answer/triage open questions in DEV_LOG.md with Nick (Q1–Q3 gate Phase A/B)
      — Q1–Q3 answered pre-sprint; Q6 answered in §1 (finding needs Nick's call);
      Q4 still open, gates §2
- [ ] Confirm bench unit identity + which `device_profiles/` YAML it runs

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
- [ ] Deploy `test_UART_throughput.py` next to `bm_serial.py` on the Pi
- [ ] Back up crontab; disable `@reboot` RC cron for test window
- [ ] Confirm `power_halt` is `enabled: false` / `dry_run: true` on bench unit
- [ ] Confirm nothing else holds the UART (no camera process running)
- [ ] Pi UART hygiene: PL011 not mini-UART (`enable_uart=1`, `dtoverlay=disable-bt`)

## 3. Phase A — link integrity (no quota)
- [ ] Run: `--phase log --count 200 --size 300 --gap-ms 0`
- [ ] Pull `uart_test.log` via Spotter CLI (`cat` + terminal logging, or
      `sd usb`) — see docs/spotter_cli_reference.md
- [ ] Verify 200/200 sequential, CRC-clean; record result in DEV_LOG

## 4. Phase B — payload ceiling + pacing floor (cellular quota)
- [ ] Review Nick's Sofar API tooling; document count query
      (`api/raw-messages`, `EA`-header msgs per run-id) in DEV_LOG
- [ ] B1 size probe: 5 msgs each at 900/1000/1100/1200 B, gap 5000 ms;
      largest 100%-delivery size = chunk ceiling
- [ ] `post` check between B1 steps (cellular error states both "OK")
- [ ] B2 sweep: `--phase tx --count 30 --size <B1 winner> --sweep "5000,2000,1000,500,250"`
- [ ] Count arrivals per run-id in Sofar backend; log per-gap table in DEV_LOG
- [ ] Watch Spotter/mote console for queue-full errors during fast steps
- [ ] Compute floor + 25% margin → proposed `image_transmit_delay_seconds`
- [ ] Log total quota spend per run in DEV_LOG

## 5. Phase C — end-to-end
- [ ] Set new YAML values (chunk from B1, delay from B2) on bench unit
- [ ] One real RC capture+send; record awake time, message count, image integrity
- [ ] Compare vs ~16 min baseline; PASS/FAIL against SPEC success criteria

## 6. Lock-in + wrap
- [ ] Land measured values in `BM_Devel_Pi/camera_schedule.yaml` + device profiles
- [ ] Deploy via `tools/deploy_rc_runtime.sh`; update manifest if needed
- [ ] Restore crontab; verify unit back in known-good state
- [ ] Rollback commands documented in DEV_LOG
- [ ] Update DEV_LOG with findings, bugs, deferred items (incl. firmware tier)
- [ ] PR with test evidence for Nick review
