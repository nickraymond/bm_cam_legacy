# Phase E — parity run (PAR3 / PAR0), 2026-07-29

Identical matrix (200 msgs @ 1.0 / 3.0 / 4.0 s, 384 chars, 300 s drains)
run **simultaneously** on both units, 01:05–01:41Z. Requested by Nick as a
parity check before committing to the wider sweep.

Artifacts: `analysis/bursts_*.csv`, `analysis/gaps_*.csv`,
`analysis/gap_console_*.csv`, `sendlogs/`, `../../../spotter_logs/`.
Numbers below are the **settled** read (02:05Z) after a forced
`note sync`; the 01:43Z read was inside the backend lag window and is not
used. bmcam000's 4.0 s cell moved 184→192 between reads, so it is the one
number still worth re-confirming.

## Result: NOT at parity

| delay | bmcam003 / SPOT-33507C | bmcam000 / SPOT-31593C |
|---|---|---|
| 1.0 s | 198/200 — **1.0 %** | 182/200 — **9.0 %** |
| 3.0 s | 198/200 — **1.0 %** | 198/200 — **1.0 %** |
| 4.0 s | 200/200 — **0.0 %** | 192/200 — **4.0 %** |

Same code, same matrix, same wall-clock window, same bench. The units are
**not** interchangeable as measurement instruments — which is exactly why
the overlap cell in PLAN.md was added, and it has already earned its keep.

## Mechanism: confirmed, and it has TWO populations

Every gap on both units is console-confirmed as a cellular-queue overflow
(`gap_console_*.csv`, 11/11 gaps). The console emits **exactly 2 error
lines per lost message** (`Queue MS_Q_CELLULAR_ONLY is full` +
`Unable to submit`), so loss can be counted from the console alone.

Clustering every queue-full line of the night into episodes (>20 s apart
= new episode) splits them cleanly in two:

| population | duration | cost | timing |
|---|---|---|---|
| **A — periodic** | < 1 s | exactly **1 message** | starts within ~8 s of a **5-minute wall-clock boundary** |
| **B — sync session** | 6–28 s | 6–10 messages | unaligned, sporadic |

10 of 16 episodes across both units start within 15 s of a 5-min
boundary. Population A is almost certainly the bridge's
`alignmentInterval5Min: 1` behaviour (key 6 in the system partition)
driving a periodic message-service pass.

**Where the two units agree exactly — 3.0 s, 2 lost each — they lost the
same 2 messages at the same two instants: 01:15:0x and 01:20:0x.** Two
independent Spotters, same absolute clock times. Population A is
identical across units; all the disagreement is population B, and
bmcam000 simply had more of it (11 episodes vs 5).

## Why this matters for the ship value

Population A costs ~1 message **per 5-minute boundary the burst spans,
independent of pacing**. Slowing the pace lengthens the burst and crosses
*more* boundaries:

| burst | delay | window | 5-min bounds spanned | lost |
|---|---|---|---|---|
| PAR3C200D1000 | 1.0 s | 01:04:30–01:07:51 | 1 | 2 |
| PAR3C200D3000 | 3.0 s | 01:12:51–01:22:52 | 2 | 2 |
| PAR3C200D4000 | 4.0 s | 01:27:52–01:41:13 | 3 | 0 |
| PAR0C200D1000 | 1.0 s | 01:04:30–01:07:52 | 1 | 18 |
| PAR0C200D3000 | 3.0 s | 01:12:52–01:22:54 | 2 | 2 |
| PAR0C200D4000 | 4.0 s | 01:27:54–01:41:16 | 3 | 8 |

Loss is **not monotonically decreasing in delay** on either unit. That
contradicts the working model in DESIGN D16 (`lost ≈ max(0, blackout_s /
delay_s − queue_slots)`, predicting zero loss near 3.5–4.0 s): that model
has only population B in it. With population A included, a slower pace
buys less than predicted and costs more exposure, so **there may be no
zero-loss delay at all**, and the optimum could be to transmit *fast* and
span fewer boundaries — the opposite of the current direction.

**This is n=1 per cell.** It is enough to falsify "slower is strictly
better"; it is not enough to pick a ship value. The wider sweep needs
repeats (n≥3) and bursts deliberately phased against the 5-minute grid.

## Open

- bmcam000's 4.0 s cell to be re-confirmed once fully drained.
- bmcam003 went unreachable ~01:50Z on a permanently-powered bus and
  needed a bus-power blip to recover (see DEV_LOG / incident entry). Root
  cause unknown: `journalctl` is not persistent on these units, and no
  undervoltage was recorded (bus held 23.86 V, `throttled=0x0`). Its PAR3
  data was already pulled and is unaffected.
