---
name: spotter-health-check
description: Check a Sofar Spotter's system health over the USB console with `post` and `sensors` — send the commands through the serial monitor, read every field, and separate real hardware faults from expected bench conditions (red system LED triage). Use when a Spotter shows a red/odd LED, when deciding whether a delivery problem is Spotter-side or Pi-side, or as a pre-flight before a bench run.
---

# Spotter health check (`post` + `sensors`)

Two console commands give a full health picture in ~10 s. Run them FIRST
when anything looks wrong — they instantly split "the Spotter is unhappy"
from "the camera unit is unhappy", which are debugged in entirely
different places.

Proven: 2026-07-30T03:07Z, SPOT-33507C red-LED triage (Sprint11).

## Sending the commands

Through the serial monitor's FIFO (do not open the port twice):

```bash
printf 'post\n'    > ~/spotter_logs/<SPOT-ID>/cmd.txt
sleep 5
printf 'sensors\n' > ~/spotter_logs/<SPOT-ID>/cmd.txt
```

Output lands in `~/spotter_logs/<SPOT-ID>/console_<YYYYMMDD>.log` between
`post` … `post end` and `sensors` … `sensors end` markers. The power-log
chatter interleaves; filter with
`grep -vE ", power \| tick|BRIDGE_CFG"` when reading.

## `post` — subsystem error states

Every subsystem reports one of `OK`, `N/A` (not fitted / not sampled), or
an error. A red system LED means at least one line is in an error state —
read them all before concluding anything.

**Expected on an INDOOR BENCH unit (not faults):**

| Field | Bench state | Why |
|---|---|---|
| `gpsErrorState` | `NO_SIGNAL` | no sky view |
| `solarErrorState` | `LOW` | room light only |
| `cellularSignalErrorState`, `micErrorState`, `sstErrorState` | `N/A` | not sampled / not fitted |

**Anything else non-OK is a real finding.** Ones that matter most for
camera work:

| Field | Watches | If not OK |
|---|---|---|
| `bridgeErrorState` | the BM bridge = the Pi's power + data path | camera unit is cut off — Spotter-side problem, stop debugging the Pi |
| `busV/busI/busMonErrorState` | the 24 V BM bus rail | power delivery to nodes is suspect |
| `cellularErrorState` | Notecard path | image chunks cannot leave — explains "nothing at the backend" with a perfect console |
| `battErrorState` / `chargerErrorState` | supply | brownouts corrupt everything downstream |
| `sdInitErrorState` / `sdQueueErrorState` | Spotter SD | the D9 energy record is at risk |
| `baroErrorState` etc. `INIT_ERR` | that sensor's init | annoying, lights the LED, harmless to camera work — but log it |

Measured example (SPOT-33507C, 2026-07-30): red LED, `post` showed
`baroErrorState: INIT_ERR` + indoor GPS/solar states, all power and comms
OK → LED explained, bench usable, barometer noted for Sofar.

## `sensors` — live analog readings

```
BATT_V / BATT_I / BATT_T    battery volts / amps (+ = charging) / temp
SOLAR_V / SOLAR_I           solar input
BUS_V / BUS_I               Spotter INTERNAL rail — NOT the 24 V BM bus
Internal Temperature/Humidity, Pressure, SST
```

Healthy bench numbers: BATT ~4.0–4.2 V, small positive BATT_I, internal
temp ~20–30 °C. `Pressure: N/A` pairs with `baroErrorState: INIT_ERR`.

**BUS_V here is ~4 V and is the Spotter's internal bus.** The 24 V BM bus
that powers the camera unit is read from the BRIDGE node's `power |`
telemetry lines (addr 65, `voltage: 23.9…`), not from `sensors`.

## Why this belongs in every debug session

- **Fault domain split in one command.** Console shows perfect transmits
  but nothing reaches the backend? `cellularErrorState` answers whether to
  blame the Spotter before touching the Pi.
- **Red LED ≠ emergency.** Bench units routinely show error LEDs from
  GPS/solar conditions; `post` names the actual culprit.
- **Trend anchor.** Run at bench-session start and file the output in the
  run dir — a later comparison shows what changed.
- Related: `uptime` (spontaneous-reboot checks — note the firmware logs
  `Reboot limit reached, ignoring` when resets come too fast, e.g. after
  several manual power cycles), `error debug` (raw error values),
  `memfault print` (crash metrics). Reference:
  `docs/spotter_cli_reference.md`.

Bench IDs: SPOT-33507C (bmcam003) · SPOT-31593C (bmcam000).
