# SPRINT 09 — Camera → Mote UART Throughput (SPEC)

> **OUTCOME (sprint closed 2026-07-27): PASS — 8.3× faster, and the
> spec's premise inverted.** Locked production values:
> `image_buffer_size: 384`, `image_transmit_delay_seconds: 1.0`,
> `network_type: 0x02`. Result: 1.96 min awake @ q90 vs 16.3 min @
> q9–15 baseline. Measurement killed the "bigger chunks win" premise
> below: the cellular per-message ceiling is 1000 B exactly, but
> payloads ≤ ~400 B ride a fast drain path to the Notecard while larger
> ones wait a ~10.8 s batch cycle with only **2** queue slots — and
> drops are **silent** to the Pi. Small-chunks-fast won. The ~980-char
> target in the body below is preserved as written history; it was
> wrong. Full data: DEV_LOG decisions + `runs/`. One open item: bench
> unit deliberately left disarmed (TRACKER §6).

> Supersedes `uart_speedup_spec.md` (v2) in this folder. That doc's link
> analysis and test design are carried forward; this SPEC corrects it
> against the actual repo state as of 2026-07-26 (several §1 items were
> already landed by Sprint08). Keep v2 for reference; this file is the
> source of truth.

## What we are building

Cut the Pi's awake-time per image from ~16–20 min toward ~1–2 min by
raising chunk size and lowering inter-message pacing on the existing
Pi → mote → Spotter uplink. **Pi-side config and code only — zero mote
firmware changes.** A hardware test (Phases A–C) measures the true pacing
floor before any production value changes.

## Why

The 115200 UART link runs at ~0.5% utilization: ~300 B every 5 s. The 5 s
sleep between messages, not the wire, is the power budget. The Spotter's
32-deep transmit queue is the suspected real floor — we measure it instead
of guessing.

## Corrected repo facts (verified 2026-07-26, this repo)

What the v2 spec asked for vs. what already exists:

| v2 spec item | Status in repo |
|---|---|
| §1a `network_type: cellular_only` in YAML | **Landed.** `bm_serial.network_type: 0x02` in [BM_Devel_Pi/camera_schedule.yaml]; parsed by `bm_serial.py` (`load_network_type_from_config`, aliases incl. `cellular_only`). |
| §1a raise chunk size | **Not landed.** `bm_serial.image_buffer_size: 300` — value change still needed. See units warning below. |
| §1b tunable pacing | **Landed as config; value unchanged.** `bm_serial.image_transmit_delay_seconds: 5` read by `rc_progressive_jpeg.py`. No `time.sleep(5)` hunt needed — only the value changes after Phase B. |
| §1c baud/port from YAML | **Half landed.** Top-level `uart_port`/`baudrate` YAML keys exist and are used by `spotter_time_sync.py`, but `BristlemouthSerial.__init__` still hardcodes `serial.Serial('/dev/ttyAMA0', 115200)` (bm_serial.py:118). Constructor should read the same keys. No behavior change today. |
| §1d PL011 vs mini-UART check | Still needed (on-Pi check). |

**Units warning (corrects v2's arithmetic):** `image_buffer_size` is in
**base64 characters**, not raw bytes. The wire message is
`<I{i}>` + b64 chunk (`rc_transmit.split_base64_chunks`). 300 b64 chars =
225 raw image bytes. A 70 KB JPEG → ~93 KB base64 → ~311 messages at
current settings, ~96 messages at ~980 chars/message — not the "~72" in
v2. Working target: **~980 b64 chars** (message under the documented
1000 B cellular cap including `<I{i}>` framing). Final value = Phase B1's
measured per-message ceiling minus framing headroom — Nick wants the
1000–1200 B range probed empirically before we commit.

**Interaction to re-check after the change:** `progressive_jpeg.message_cap:
195` and `max_run_time_min: 16` were tuned for 300-char chunks. At ~980
chars, a full ladder image may fit well under the cap — the quality
selector will pick higher quality for the same message count. This is the
point, but the budget math in `rc_time_budget.py` must be sanity-checked
with the new values (Phase C).

## Work items

### 1. Config plumbing (no hardware)
- `bm_serial.py` `BristlemouthSerial.__init__`: when `uart is None`, read
  `uart_port` / `baudrate` from `camera_schedule.yaml` (same keys
  `spotter_time_sync.py` uses) instead of hardcoding. Defaults unchanged
  (`/dev/ttyAMA0`, 115200). **Do not touch framing** (`cobs_encode`,
  `crc`, `get_pub_header`, packet layout).
- Decide chunk-size target (~980 b64 chars) but **do not flip production
  YAML values until Phase B reports the floor.**
- Mirror any YAML schema additions into `device_profiles/*/camera_schedule.yaml`
  and `device_profiles/rc_field_template/`.

### 2. Hardware test (bench: Spotter ebox on Mac USB, camera node on BM bus)

Script: `test_UART_throughput.py` (this folder — already written; speaks
real `bm_serial` protocol by import). Deploy next to `bm_serial.py` on the
Pi. Field-ops guard per CLAUDE.md: back up crontab, disable the `@reboot`
RC cron during testing, restore after.

**Phase A — link integrity (no cellular quota; writes to Spotter SD):**
```
python3 test_UART_throughput.py --phase log --count 200 --size 300 --gap-ms 0
```
Pass: 200 sequential CRC-clean lines in `uart_test.log` on the Spotter SD.
Clean at gap 0 → the 115200 link is fine at full rate (expected).
Verification: Spotter USB terminal — `ls` / `cat uart_test.log` with
terminal session logging, or `sd usb` to mount the SD on the Mac. Full CLI
reference: `docs/spotter_cli_reference.md`.

**Phase B1 — payload-size probe (real cellular path, small counts):**
Find the true per-message cellular cap empirically. bm_core pins
`spotter_tx_max_cellular_payload_bytes 1000` and forum t/575 says
"~1000 bytes", but Nick wants the 1000–1200 range probed. At a safe gap
(5000 ms), send a small count (e.g. 5) at each size 900 → 1000 → 1100 →
1200; the largest size with 100% delivery is the chunk ceiling. Run
`post` on the Spotter CLI between steps (`cellularErrorState` /
`cellularSignalErrorState` both "OK" = data reaching Sofar).

**Phase B2 — spotter_tx pacing floor (real cellular path):**
```
python3 test_UART_throughput.py --phase tx --count 30 --size <B1 winner> \
    --sweep "5000,2000,1000,500,250" --network-type cellular_only
```
Pass metric: all 30 messages per gap step arrive in the Sofar backend —
count `EA`-header messages per run-id via `api/raw-messages` (Nick's API
tooling; review endpoints at Phase B prep). Smallest zero-loss gap + 25%
margin becomes `image_transmit_delay_seconds`. Watch Spotter/mote console
for queue-full complaints on fast steps. The 60 s inter-step drain pause
is load-bearing — don't trim it. No hard quota cap set; keep counts
modest and log spend per run (full sweep ≈ 135 KB minimum).

**Phase C — end-to-end:** one real RC capture+send with new YAML values
(cellular_only, ~980-char chunks, measured delay). Record wall-clock awake
time, delivered image integrity, and message count vs. `message_cap`.
Compare against the ~16 min baseline.

### 3. Lock-in
- Write measured values into `BM_Devel_Pi/camera_schedule.yaml` +
  device profiles; deploy via `tools/deploy_rc_runtime.sh`.
- Update `tools/rc_runtime_manifest.txt` if file set changes.

## Explicitly out of scope
- Mote firmware changes / reflash (baud bump, burst-and-sleep buffering —
  that is the separate compile-tier project; see v2 §2 for the ordered list).
- Any change to bm_serial framing, CRC, COBS, or headers.
- Satellite path: non-image traffic stays on 0x01.

## Success criteria
- Phase A: 200/200 lines, sequential, CRC-clean, at gap 0.
- Phase B: a measured zero-loss pacing floor with 25% margin, documented
  per gap step (sent/arrived counts in DEV_LOG).
- Phase C: one real image delivered intact with awake time ≤ 5 min
  (target ~1–2 min), no queue errors, no regression in image integrity.
- Rollback documented and tested-by-inspection: restore
  `image_buffer_size: 300`, `image_transmit_delay_seconds: 5`,
  `network_type: 0x01` — framing untouched.
- Crontab restored; no field unit left in a disabled state.

## Reference facts
- Cellular max payload 1000 B, Iridium 311 B, queue depth 32: bm_core
  `integrations/spotter.c`; forum thread bristlemouth.discourse.group/t/430.
- Mote app `serial_bridge` parses COBS at hardcoded 115200: bm_protocol
  develop, `src/apps/bm_devkit/serial_bridge/user_code/user_code.cpp`.
- Post-firmware UART ceiling 833,333 bps: forum thread t/277.
