# Sprint10 Phase B — bench hardware results (bmcam003, 2026-07-27, overnight)

Operator: Claude session (Nick-authorized overnight run, local-only).
Bench: bmcam003 (100.103.35.24) + Spotter SPOT-33507C on Mac USB
(`/dev/cu.usbmodemSPOT_33507C1`). Cron disabled (Sprint09 backup),
power_halt disabled+dry_run throughout. Code: branch `bm_commands`
@ 812c825 deployed via `tools/deploy_rc_runtime.sh`. Config: live
YAML + `bm_commands` island (enabled, topic bmcam/cmd, listen 90 s;
backup `/home/pi/camera_schedule_backup_sprint10_20260727T062014Z.yaml`).

Injection path: `bm pub bmcam/cmd <compact-json> 1 1` on the Spotter
USB console. Bench cycles: `rc_progressive_jpeg.py --bench-commands
--skip-time-window` (camera + encode run; NO image transmit; acks are
real uplink messages — 40 sent total).

## Headline finding (wire format — code changed mid-session)

Mote→Pi frames arrive **RAW** (no COBS, no 0x00 delimiter, no payload
length; frame end recoverable only by CRC16 scan). Pi→mote remains
COBS+delimited. The inbound decoder was rewritten (`RawPubScanner`),
captured bytes pinned as test vectors (`uart_dump_01.log` is the raw
capture). DEV_LOG Q1 corrected.

## Test matrix

| # | Test | Result | Replications / evidence |
|---|------|--------|--------------------------|
| 1 | `bm pub` console → Pi UART transport | **PASS** | 40/40 frames decoded across runs 2–6 (`sig_hits`==`matched`, 0 CRC-scan failures) |
| 2 | `roi` applied to THIS cycle's capture | **PASS** | run2: roi=2 → sidecar crop (768,432,3072,1728); run6: roi=3 applied; resets in runs 5/6 → default crop in sidecar |
| 3 | `foc` manual + auto flags reach rpicam | **PASS** | run3 capture cmdline: `--autofocus-mode manual --lens-position 0.5`; runs 5/6: `--autofocus-mode auto` |
| 4 | `awb` preset + custom gains | **PASS** | run3: `--awb custom --awbgains 1.8,1.2`; runs 5/6: `--awb auto` |
| 5 | `exp` EV compensation | **PASS** | run3: `--ev 1`; runs 5/6: no `--ev` (auto) |
| 6 | `win` duration (next-cycle budget) | **PASS** | run3 win=1 → run4 started with budget 720 s; reset run5 → run6 960 s |
| 7 | `ping` ack-only | **PASS** | 17 pings, all acked, settings untouched |
| 8 | Invalid value rejected + error ack | **PASS** | run2 awb=42 → `{"id":202,"ok":0,"e":"val",...}`, settings kept |
| 9 | Duplicate id not re-applied (D4) | **PASS** | run2: id 201 re-sent with different v → acked ok=1, value unchanged |
| 10 | Acks reach the Spotter (uplink) | **PASS** | 40/40 acks written; Spotter console logged `[BM_TX] Submitted spotter/transmit-data ... cell-only queue, Len: 65` |
| 11 | Burst delivery in order | **PASS** | run6: 12 commands at ~150 ms spacing → 12/12 applied in exact send order |
| 12 | Settings persist across process restarts | **PASS** | every run is a fresh process (Q10 model); run3 booted with run2's roi=2 overlay; state file survives with dedupe ids (last-32 eviction observed working) |
| 13 | Factory reset (all-zeros sequence) | **PASS** | runs 5 & 6: state all-zero, capture back to default crop/flags |
| 14 | Shared-port Spotter time sync (D11) | **PASS** | `wait_for_spotter_utc` decoded UTC in 9.8 s on hardware |
| 15 | Camera pipeline coexistence | **PASS** | 5 full capture+encode cycles (q80–q90, 115–119 msgs planned) with daemon running; zero capture errors |

## Latency (Spotter console send → Pi applied; both hosts NTP-synced)

n=40: **min 45 / median 159 / mean 156 / p90 249 / max 262 ms**
(includes the daemon's 0.2 s main-loop poll quantization; wire arrival
is faster). Repeatable across 5 separate cycles. `latency_stats.json`
has per-command values.

## Not tested (deferred)

- **Hard power cycle → settings retained** (§5): remote reboot blocked
  by session permissions; process-restart persistence is proven, and
  the state file is written with fsync — needs Nick's physical pull or
  approval.
- **Acks in transmit pacing slots on hardware** (--transmit with a real
  image send): skipped to avoid cellular image spend per Nick's
  instruction; covered off-device by tests/test_command_integration.py.
- ~~Cloud/backend visibility of acks~~ **DONE (post-report):** backend
  reconciliation at ~06:56Z found **38/40** acks at `api/sensor-data`
  (both id-201 acks present) — the 2 missing (605, 616) were mid-burst
  sends, i.e. **silent Spotter-queue drops on the ack path**, the exact
  Sprint09 failure mode. Fix landed + hardware-verified: `drain_acks`
  now paces at 1.0 s (run 7: 12-command rapid burst → 12 acks at
  exactly 1.0 s gaps, all flushed before halt; run07 log +
  latency_stats untouched). Backend check for the 12 retest acks
  (ids 701–716) ran after this file was frozen — see DEV_LOG.
- Cron-armed (@reboot) flow with the daemon; full 12/16-min window soak
  with transmit.

## End state of bmcam003

Disarmed as found (cron disabled, power_halt off/dry_run). Branch
bm_commands @ 812c825 deployed. bm_commands island ENABLED in live YAML
(backup noted above). Command state file at factory zeros, dedupe list
holds the last 32 test ids. No stray processes. 15 bench images under
/home/pi/images_sprint10bench/.
