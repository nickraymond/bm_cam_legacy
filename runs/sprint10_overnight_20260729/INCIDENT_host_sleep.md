# Incident — host sleep stalled the bench, not the units (2026-07-29)

## Timeline (UTC)

| Time | Event |
|---|---|
| 07:49 | Overnight A/B started; first cycles acked on both units |
| 08:41:31/34 | **Both** Spotter consoles stop mid-line, within 3 s of each other |
| 08:42 (01:41 PDT) | Mac: `Entering Sleep state due to 'Maintenance Sleep'` |
| ~09:26 (02:26 PDT) | Mac wakes; orchestrator immediately sees 45 min of silence and **aborts** (correct behaviour) |
| 09:28 | `caffeinate -dimsu` applied; monitor restarted (with a broken patch — see below) |
| 09:33 | Monitor patch corrected; **both consoles resume within 60 s** |
| 09:35 | Orchestrator restarted, sweep resumes |

## Root cause

The **bench Mac went to sleep**, not the hardware. Two consoles stopping
within 3 s of each other was the tell — two independent Spotters do not
fail simultaneously. `pmset -g assertions` confirmed no sleep-prevention
assertion was held.

This is the **same failure already recorded** in the 07-27/28 soak's
tooling-bug list ("Mac App-Nap stalling orchestration (caffeinate)"). The
fix was known and I did not apply it. That is the actual lesson here.

## Impact: small, and NOT to the experiment

The camera units are autonomous — Spotter cycles bus power, `@reboot` cron
runs a cycle, the cycle halts the box. None of that depends on the Mac.
Verified after recovery: bmcam003 up with **51** cron cycle logs, bmcam000
up with **98**, both imaging. Backend over the same window:

| Spotter | Arm | complete images (START/END) | chunks |
|---|---|---|---|
| SPOT-33507C | B, 1.0 s | 5 / 5 | 847 |
| SPOT-31593C | A, 5.0 s | 5 / 4 | 808 |

**Lost:** ~45 min (≈1.5 cycles) of Spotter console capture, and `roi`
injection over that span — the units simply held their last commanded
`roi=2`. The primary A/B measurement (delivery vs energy at 1.0 s vs
5.0 s) is unaffected, because it is measured from the backend and the SD
power logs, not from the console.

## Second, self-inflicted fault (caught before it cost anything)

The first version of the silent-port watchdog left the console
line-processing block **inside the `elif`, after the `raise`** — dead
code. The monitor would have logged zero lines even with data flowing,
and I briefly mis-read that as "the USB link is dead post-sleep".

It parsed cleanly, which is the point: `ast.parse` is not a test. Caught
by checking the actual structure (does line-processing precede the
raise?) and then empirically — consoles resumed within 60 s of the real
fix.

## Fixes landed (branch `claude/sprint10-txd-cap-src`, commit eb88f49)

1. `caffeinate -dimsu` held for the run — the known fix, now applied.
2. `spotter_serial_monitor.py`: silent-port watchdog forces a reconnect
   after 120 s with no data. A healthy Spotter publishes `power |` every
   10 s, so it cannot false-fire.
3. Watchdog structure corrected so line processing is reachable.

## What should change next time

- **`caffeinate` belongs in the runbook, not in someone's memory.** Any
  unattended bench session driven from the Mac should start with it, the
  same way console capture is started.
- The stall detector should distinguish *host slept* from *unit died*: a
  large wall-clock jump with all ports silent is a host event, and should
  pause-and-resume rather than abort the run.
- Prefer the monitoring Pi over the Mac for overnight console capture —
  the Pi has no sleep policy. `tools/spotter_serial_monitor.py` already
  supports it (`--log-root`, systemd unit in its docstring).
