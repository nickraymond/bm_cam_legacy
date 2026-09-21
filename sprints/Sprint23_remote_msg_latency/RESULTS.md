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
| 2 | 04:54:46 | `uptime` | tester UI | (pending) | | | First send outside the post-boot sync burst. |

Receive signature on v2.16.8 — unchanged from v2.16.6:

```text
[SYS] [INFO] Remote message received(16)! "uptime
id:38184
"
Uptime: 0.04 hours
Command not recognised.  Enter 'help' to view a list of available commands.
```

(`id:NNNNN` is appended by Sofar and runs as an unrecognised command. Harmless.)

## Findings so far

1. The wedged-mailbox theory held: one `clear_command_queue` and SPOT-31593C
   delivered on the next Rx check.
2. Delivery happens at a cellular sync (`[MS] Checking for Rx Messages`), same
   as v2.16.6. Whether v2.16.8 changed the sync cadence is not known yet —
   test 2 measures it.
3. **Open for Phase 2b:** at 04:50:43Z the bridge printed `Sample enabled 0`
   (Sample Duration 900 s / Interval 3600 s) and the bus sat at 23.9 V,
   0.000 A at 04:55Z. That reads as: bridge power controller disabled, bus
   always ON, nothing drawing current. The queue-until-bus-on feature can
   only be seen with the bus OFF at delivery time, so Phase 2b needs the
   power controller enabled (a `bridge cfg … commit`, which re-evaluates bus
   power immediately) — Nick's call, not done.
