# Sprint23 — Results (running log)

Unit: SPOT-31593C, USB console on Nick's Mac. All times UTC. Latency = console
line host timestamp (Mac clock, 1 s resolution) minus send time (Mac clock).

## Firmware

| When | Version | Source |
|---|---|---|
| 2026-07-29 | v2.16.6, bridge v0.13.11 | `~/spotter_logs/SPOT-31593C/console_20260729.log` banner |
| 2026-09-21T04:49:25Z | **v2.16.8** | banner after Nick's flash, `console_20260921.log` |

Health after flash (`post` 04:49:37Z): cellular OK, bridge OK, SD OK, bus
V/I OK. Non-OK only: `gpsErrorState NO_SIGNAL` (cleared to OK at 04:49:51Z),
`solarErrorState LOW` — both expected on an indoor bench.

Baseline on v2.16.6 was NOT taken (flash started first). "Before" reference is
July: 21–66 min on SPOT-33507C; SPOT-31593C 0/16 delivered, ever
(`runs/remote_cmd_diagnosis_20260731/REPORT.md`).

## Deliveries

| # | Sent | Command | Via | Received | Latency | Sofar id | Notes |
|---|---|---|---|---|---|---|---|
| 1 | 04:50:09 | `uptime` + `clear_command_queue` | CLI `sofar_send_command.py` | 04:50:55 | **46 s** | 38184 | First remote delivery this Spotter has ever had. Spotter uptime 0.04 h — it was in its post-boot burst of syncs (Rx checks 04:49:36, 04:50:21, 04:51:01), so this is NOT a steady-state number. |
| 2 | 04:54:46 | `uptime` | tester UI | 05:06:47 | **721 s (12.0 min)** | — | No sync happened 04:51→05:06. Delivered at the sync that the 05:05:34Z `bridge cfg commit` caused (network-config uplink → `Checking for Rx` 05:06:08 → message 40 s later). First live end-to-end catch by the tester. |
| 3 | 05:08:04 | `bm pub bmcam/s23test {"id":3,"via":"remote"} 1 1` | tester UI | 05:30:24 | **1340 s (22.3 min)** | 38186 | INVALID as a bus-off test: it landed inside the 120 s bus-ON re-init window caused by my 05:29:43Z schedule commit (which also produced the uplink that pulled it down). Ran straight through, silent — matches the bus-on USB template. |
| 4 | 05:31:55 | `bm pub bmcam/s23test {"id":4,"via":"remote"} 1 1` | tester API | (pending) | | | Bus OFF at send. `note sync` over USB at 05:32:11 did NOT cause a Spotter mailbox check. Waiting on a natural uplink. |

Receive signature on v2.16.8 — unchanged from v2.16.6:

```text
[SYS] [INFO] Remote message received(16)! "uptime
id:38184
"
Uptime: 0.04 hours
Command not recognised.  Enter 'help' to view a list of available commands.
```

(`id:NNNNN` is appended by Sofar and runs as an unrecognised command. Harmless.)

## USB reference outputs (v2.16.8) — the template for remote commands

Typed over the USB console via `cmd.txt`, 05:04–05:08Z. A remote message runs
as the same console line, so these are the expected outputs.

| Command | Bus | Console output |
|---|---|---|
| `uptime` | on | `Uptime: 0.26 hours` |
| `bm topo` | on | `Bristlemouth toplogy: 0e582dd12c1e1480` (bridge only — no camera attached) |
| `bm info 0e582dd12c1e1480` | on | `Successfully sent info request` + `Neighbor information:` block (Node ID, GIT SHA 1595F804, `bridge@v0.13.11`) |
| `bm pub bmcam/s23test {"id":1} 1 1` | **on** | **nothing** — silent on success |
| `bm pub bmcam/s23test {"id":2,"via":"usb"} 1 1` | **off** (0.2 V) | `[BRIDGE] [INFO] Queuing serial command: bm pub bmcam/s23test {"id":2,"via":"usb"} 1 1` |
| `bridge cfg status 0e582dd12c1e1480 s` | on | 16 keys; `bridgePowerControllerEnabled` = 0 before the change |

**Queue signature found:** `[BRIDGE] [INFO] Queuing serial command: <line>`.
It fires for a USB-typed `bm` command too, not only for remote messages.
Release signature: pending the 06:00Z bus-on.

## Bench configuration change (reversible)

05:05:26Z, over USB, Nick-approved:

```text
bridge cfg set 0e582dd12c1e1480 s u bridgePowerControllerEnabled 1
bridge cfg commit 0e582dd12c1e1480 s
```

Was 0 (bus always on). Interval 3,600,000 ms / duration 900,000 ms unchanged.
Effect seen: bridge re-init, bus on 120 s, then `Sample enabled 1`,
`Bridge bus power: 0`, `power off for: 3152000` at 05:07:36Z.
Restore: same two lines with `0`. No node was on the bus (`bm topo`).

05:29:27Z, Nick-approved, to shorten test cycles: `sampleIntervalMs` 3600000 →
**600000**, `sampleDurationMs` 900000 → **120000**, then commit. Read back in
`Bridge network config`. Observed: `Sample Duration: 120 s / Interval: 600 s`,
bus off 05:31:45Z, `power off for: 502000` → next on ~05:40:08Z.
Restore: set 3600000 / 900000 and commit.

Side effects of ANY `bridge cfg commit`: bridge re-init, bus forced ON 120 s,
and a network-config uplink → cellular sync → mailbox check. Do not commit
while a bus-off test is in flight.

## Findings so far

1. The wedged-mailbox theory held: one `clear_command_queue` and SPOT-31593C
   delivered on the next Rx check.
2. Delivery happens at a cellular sync (`[MS] Checking for Rx Messages`), same
   as v2.16.6, and lands ~35–40 s after that line. A sync happens when the
   Spotter has something to uplink: test 2 sat 12 min with no sync at all,
   then rode the uplink my config commit produced. Implication: latency to
   the Ebox ≈ time until the Spotter's next uplink. A camera that transmits
   often will pull its own commands down quickly; an idle Spotter will not.
   The idle sync period is still unmeasured.
3. `note sync` (USB) makes the Notecard sync but does not make the Spotter run
   `[MS] Checking for Rx Messages`. It is not a way to force delivery.
4. Release of a held command printed NOTHING: USB id 2 was queued at
   05:07:45Z (`Queuing serial command`), the bus came on at 05:29:45Z, and no
   line mentioned it. Either release is silent, or the bridge re-init dropped
   the queue. Undecidable from the console alone — needs a listener on the
   bus (bmcam000) or Sofar's answer.
5. **(Resolved 05:05Z — controller enabled.)** Original note: at 04:50:43Z the bridge printed `Sample enabled 0`
   (Sample Duration 900 s / Interval 3600 s) and the bus sat at 23.9 V,
   0.000 A at 04:55Z. That reads as: bridge power controller disabled, bus
   always ON, nothing drawing current. The queue-until-bus-on feature can
   only be seen with the bus OFF at delivery time, so Phase 2b needs the
   power controller enabled (a `bridge cfg … commit`, which re-evaluates bus
   power immediately) — Nick's call, not done.
