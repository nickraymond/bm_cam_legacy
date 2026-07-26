# SPRINT 10 — Remote Camera Control Daemon (SPEC)

> **Note for Claude Code:** Sprint number unverified — confirm 10 is the next
> logical sprint in the repo before branching, and renumber these docs if not.
> Also check whether a prior daemon spec exists in the repo; if so, reconcile
> it with this one and note differences in DEV_LOG.md.

## What we are building

A single Python daemon on the Pi that owns the UART link to the BM mote and
provides **bi-directional message flow**:

- **Inbound:** listens for remote commands (relayed Sofar cloud → Spotter →
  BM bus → mote → UART) and applies them to the camera pipeline.
- **Outbound:** sends acks, telemetry, and (existing behavior) image data
  toward the mote for transmission.

This extends the daemon pattern we already have working. We are **not**
adopting Sofar's `bm_sbc` — it is send-only from Python and would require a
C-side receive bridge; our own daemon already has bi-directional flow.

## Why

Active field test support. Once deployed, the camera cannot be physically
adjusted. Remote control is the only recourse if the field reveals problems
with framing, focus, or power budget. Guiding principle: **dead simple,
minimal moving parts, nothing fragile.**

## v1 command set (priority order)

| # | Command | Why it matters in the field |
|---|---------|------------------------------|
| 1 | **ROI / crop** | If camera position doesn't frame the target, adjust the captured region without moving hardware. |
| 2 | **Focus mode + position** | If autofocus chases bubbles/particulates, lock manual focus at a set distance. |
| 3 | **AWB mode** | Correct color casts underwater. |
| 4 | **Exposure mode / compensation** | Correct over/under-exposure. |
| 5 | **Active-window duration** | Currently 16 min for capture+compression; field power data may force it down. |
| 6 | **Ack / status report** | Every applied command reports back so we know it landed. |

Anything beyond these six is out of scope for this sprint.

## Command message contract

Compact enum payloads (satellite bytes are expensive; a garbled raw value
must not be able to brick the capture loop):

```json
{"id": 417, "c": "roi", "v": 2}
```

- `id` — unique command ID. Daemon persists last-N applied IDs; duplicate
  IDs are acked but not re-applied (idempotency).
- `c` — command enum: `roi | foc | awb | exp | win | ping`
- `v` — value index into a **fixed resolution table** (below). Raw values
  are not accepted in v1. Invalid `v` → reject, keep current setting,
  ack with error code.

Value tables live in one module (`command_tables.py`) and are the single
source of truth, versioned in git:

- `roi`: 0=full, 1=center-75%, 2=center-50%, 3=top-half, 4=bottom-half
  *(placeholder — confirm against field framing needs, Q3 in DEV_LOG)*
- `foc`: 0=auto, 1..N=manual positions at fixed distances
- `awb`: 0=auto, 1=daylight, 2=cloudy, 3=custom-underwater preset
- `exp`: 0=auto, 1..N=EV compensation steps
- `win`: 0=16min, 1=12min, 2=8min, 3=5min
- `ping`: no value; ack-only (link liveness test)

## Ack contract

Every processed command (applied, duplicate, or rejected) produces an ack
on the outbound path:

```json
{"id": 417, "ok": 1, "st": {"roi": 2, "foc": 0, "awb": 0, "exp": 0, "win": 0}}
```

`st` is the full current settings state — small enough to always include,
and it doubles as a status report.

## Timing model (from earlier analysis this project)

- Node duty cycle: ~20 min on / 40 min off. Cloud → Spotter latency is the
  dominant, non-deterministic delay; commands queue in Sofar's cloud while
  the bus is down.
- Daemon **listens for the entire active window** (bus init + neighbor
  discovery can eat the first minutes; a short fixed listen window will
  miss commands). Camera work runs concurrently.
- Commands apply on arrival **between captures**, never mid-capture.
  If that proves fragile, fall back to apply-at-next-window (Q8).

## Settings persistence

Applied settings are written to a JSON state file on the Pi after each
change and reloaded on boot, so a power cycle does not silently revert a
field fix. A `roi=0, foc=0, awb=0, exp=0, win=0` command sequence is the
factory reset. *(Open: dead-man's revert — Q7.)*

## Testing strategy (phased, hardware-light)

1. **Phase A — no hardware:** mock mote on a PTY pair (`socat`); unit tests
   for framing, dedupe, table lookup, state persistence, malformed input.
2. **Phase B — bench, local serial:** issue commands over the Spotter USB
   CLI with dev kit + camera on the bench; verify end-to-end apply + ack.
3. **Phase C — remote API (last):** same commands via Sofar cloud API,
   including queue-while-off and burst-on-wake behavior.

## Success criteria

- All six commands apply correctly via Phase B, with acks observed.
- Duplicate and malformed commands are safely ignored/rejected (tested).
- Settings survive a hard power cycle.
- Daemon runs the full active window without missing a command injected at
  minute 1 and at minute 19.
- Zero regressions to the existing capture/compression pipeline.
