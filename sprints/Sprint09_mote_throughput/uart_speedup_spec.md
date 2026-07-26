# Camera → Mote UART Throughput Fix — Spec & Work Order (v2)

**For:** a fresh Claude Code session working in the BM_Devel_Pi repo on the Pi.
**Goal:** cut the Pi's awake-time per image from ~20 min to ~1–2 min (or better)
with zero-or-minimal firmware changes.

---

## 0. Findings from the actual code (read this first — it corrects v1)

Nick's `bm_serial.py` and the mote firmware were inspected. Facts:

1. **Pi baud is 115200**, hardcoded as the constructor fallback:
   `bm_serial.py` line 118 → `serial.Serial('/dev/ttyAMA0', 115200)`.
2. **The mote app is `serial_bridge`** (or a fork of it) — the Pi driver speaks the
   COBS-framed bm_serial protocol (`spotter/transmit-data`, `spotter/fprintf`
   topics), which is exactly what `serial_bridge` parses. Verified in
   `bm_protocol` develop: `src/apps/bm_devkit/serial_bridge/user_code/user_code.cpp`.
3. **The mote baud is hardcoded too:** `PLUART::setBaud(115200);` — `serial_bridge`
   does NOT read the `plUartBaudRate` config key. ⚠️ This kills v1's "crank the baud
   with 3 console commands" idea: **baud beyond 115200 requires a one-line firmware
   change + reflash.** (v1's `cfg usr set plUartBaudRate ...` applies to
   `serial_payload_example`-family apps only.)
4. **The link is NOT the bottleneck.** 115200 baud moves ~11.5 KB/s. Current use:
   300 B per 5 s ≈ 60 B/s → **0.5% utilization**. A 300 B frame transmits in ~26 ms;
   the code then sleeps 5000 ms. The sleep is the power budget.
5. **Chunks are satellite-sized for no reason on cellular.** 300 B chunks fit the
   Iridium cap (311 B) because `network_type` defaults to 0x01 (sat/cell fallback).
   The driver ALREADY supports `cellular_only` (0x02) via `camera_schedule.yaml` →
   `bm_serial.network_type` — cellular allows **1000 B/message**
   (`spotter_tx_max_cellular_payload_bytes 1000` in bm_core `integrations/spotter.c`).
6. **The real pacing floor is downstream:** Spotter's transmit queue is 32 messages
   deep (cell+sat combined); oversize/overflow returns errors. The 5 s pacing was
   likely protecting this queue. The test below measures the true floor.

## 1. No-recompile changes (Pi side only — the immediate win)

All in the BM_Devel_Pi repo. Do NOT modify the mote.

### 1a. Switch to cellular-only, 1000-byte chunks
- `camera_schedule.yaml`: under `bm_serial:` set `network_type: cellular_only`.
- In the image-send code (wherever images are chunked to 300 B and passed to
  `BristlemouthSerial.spotter_tx`): raise chunk size to **≤ 996 B** (leave a few
  bytes for any app-level chunk header). Result: a 70 KB image = ~72 messages
  instead of ~233.
- Note: cellular-only means NO satellite fallback for these messages. That is the
  intended trade for bulk image data. Alerts/status should stay on 0x01.

### 1b. Replace the fixed 5 s sleep with a tunable pacing constant
- Find the `time.sleep(5)` (or equivalent) between `spotter_tx` calls in the
  camera send loop. Replace with a module-level/config value
  (`bm_serial.tx_gap_ms` in `camera_schedule.yaml`, default 5000 until the test
  says otherwise). The hardware test (§3) determines the safe value.

### 1c. Make baud a config value (prep for the firmware change later)
- `bm_serial.py` `BristlemouthSerial.__init__`: when `uart is None`, read
  `bm_serial.baud` (default 115200) and `bm_serial.port` (default `/dev/ttyAMA0`)
  from `camera_schedule.yaml` instead of hardcoding. No behavior change today;
  ready for the mote-side baud bump.
- Do not change the protocol/framing code (`cobs_encode`, `crc`, headers) at all.

### 1d. Pi UART hygiene (only if on GPIO pins, skip for USB adapters)
- Confirm `/dev/ttyAMA0` is the PL011 (not mini-UART): `/boot/config.txt` should
  have `enable_uart=1` and `dtoverlay=disable-bt`. Matters at ≥460800 later.

## 2. Compile-tier changes (mote firmware — separate, later task)

Listed so they land in the right order, NOT part of this work order:
1. **Baud bump:** in `serial_bridge` `user_code.cpp`, replace
   `PLUART::setBaud(115200);` with a `plUartBaudRate` config read (copy the
   pattern from `serial_payload_example`). Then 460800/833333 becomes a console
   config. RS232 transceiver path is documented good to 833,333 bps (short runs);
   check the Pi-side RS232 adapter chip's rating (MAX3232-class ≈ 250 kbps).
2. **Burst-and-sleep buffering:** mote accepts a whole image at line rate into
   RAM/flash and drips it to the Spotter queue itself → Pi wakes for seconds,
   not minutes. This is the single biggest power feature; needs the v1 firmware
   project (fork `serial_bridge`, add buffering + queue-aware drain).

## 3. Hardware test procedure

**Script:** `test_uart_throughput.py` (place next to `bm_serial.py`; imports it).
**Precondition:** mote + Spotter powered and connected as usual; nothing else
writing to the UART (pause the camera cron/schedule during the test).

### Phase A — link integrity (uses spotter_log → SD file; burns no cellular quota)
```
python3 test_uart_throughput.py --phase log --count 200 --size 300 --gap-ms 0
```
Sends 200 sequence-numbered CRC-tagged lines via `spotter_log` to file
`uart_test.log`. Verify on the Spotter SD card: 200 lines, sequential, no gaps.
Clean at gap 0 → the 115200 link itself is fine at full rate (expected).

### Phase B — spotter_tx pacing floor (uses real cellular path; keep counts modest)
```
python3 test_uart_throughput.py --phase tx --count 30 --size 900 \
    --sweep "5000,2000,1000,500,250" --network-type cellular_only
```
For each gap: 30 × 900 B messages. Success metric = all 30 arrive in the Sofar
dashboard/backend (count them per run-id tag). The smallest gap with zero loss,
plus 25% margin, becomes `tx_gap_ms` in the YAML. Expect the floor to reflect
the 32-deep queue + cellular drain rate; also watch the mote/Spotter console for
queue-full complaints during the fast sweeps.
- Between sweep steps the script pauses 60 s to let the queue drain — don't trim.

### Phase C — end-to-end result
Re-run one real image capture+send with the new YAML (cellular_only, ~996 B
chunks, measured `tx_gap_ms`). Record wall-clock awake time and delivered image
integrity. Compare with the ~20 min baseline.

**Expected outcome:** ~72 messages × (tx time + gap). At a 1 s floor ≈ 1.3 min
(≈15× better). At 500 ms ≈ 40 s. The UART leg itself is seconds; the queue sets
the number.

### Rollback
All changes are YAML + one constant: set `network_type` back to `legacy` /
0x01, `tx_gap_ms: 5000`, chunk size 300. `bm_serial.py` framing untouched.

## 4. Explicitly out of scope for this session
- Any mote firmware modification or reflash (see §2).
- Changing the bm_serial wire protocol, headers, CRC, or COBS code.
- The satellite path (0x01 stays default for non-image traffic).

## 5. Reference facts (for the implementing session)
- bm_serial wire format: see existing `bm_serial.py` (do not modify framing).
- Cellular max payload 1000 B, Iridium 311 B, queue depth 32:
  bm_core `integrations/spotter.c`; forum thread "Spotter Cellular/Sat
  transmission queue constraints" (bristlemouth.discourse.group/t/430).
- serial_bridge parses COBS frames on LPUART1 at hardcoded 115200:
  bm_protocol develop, `src/apps/bm_devkit/serial_bridge/user_code/user_code.cpp`.
- Documented payload UART ceiling (post-firmware-change): 833,333 bps — forum
  thread "Camera payload goals" (bristlemouth.discourse.group/t/277).
