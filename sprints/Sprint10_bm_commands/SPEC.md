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

- `roi`: centered **zoom** presets (Q3 answered 2026-07-26 — no pan in
  v1). Concentric 16:9 crops in native 4608×2592 coords, all downsampled
  to the same 1000 px output (constant transmission budget):
  0=default 1600×900, 1=full-frame 4608×2592 (widest), 2=3072×1728,
  3=2304×1296, 4=1000×562 (max detail; floor avoids upsampling).
  *(rect values placeholder except 0 — finalize before field deployment)*
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

## Operator GUI (v1 scope — added by Nick 2026-07-27)

A local operator web GUI (Mac-served; NOT the future customer website) is
the human sending surface for commands. MVP, boring, no framework
ceremony. Requirements (Nick's definition of done, items 1–4):

1. **Target selection:** pick a registered SPOT-ID from a known list, and
   the expected BM node id — so ACKs can be verified as coming from the
   intended node, not just any node.
2. **Preset-only inputs:** every configurable value is a dropdown whose
   options are generated from `command_tables.py`. No free-form input
   anywhere.
3. **Send feedback:** visible confirmation that the command was accepted
   by the Sofar cloud API, and a visible pending state — the operator
   must be able to see that a command is in flight so they don't keep
   re-sending and stuff the queue (Sprint09: drops are silent, cloud
   queues while the node is off).
4. **ACK verification:** when the ack arrives (via `api/sensor-data`
   polling — same proven path as Sprint09), the GUI shows it matched:
   correct node id, correct command id, and the full `st` settings state
   so the operator sees the values actually applied. Mismatches are
   displayed loudly, not swallowed.

See DESIGN D9/D10 for architecture bounds.

## Testing strategy (phased, hardware-light)

1. **Phase A — no hardware:** mock mote on a PTY pair (`socat`); unit tests
   for framing, dedupe, table lookup, state persistence, malformed input.
2. **Phase B — bench, local serial:** issue commands over the Spotter USB
   CLI with dev kit + camera on the bench; verify end-to-end apply + ack.
3. **Phase C — remote API:** same commands via Sofar cloud API,
   including queue-while-off and burst-on-wake behavior.
4. **Phase D — automated permutation test (gate before Nick's final
   test):** the session runs an automated end-to-end sequence of 3–5
   different command permutations (e.g. zoom change + focus lock; awb +
   exposure; window change + ping; factory-reset sequence). For each
   permutation: command sent via the cloud path → ack received and
   verified (node id + values) → a capture cycle runs → the image lands
   in the backend reflecting the applied settings. All permutations must
   pass before Nick sits down for the final acceptance test.
5. **Final acceptance (Nick, via the GUI):** Nick drives the GUI
   end-to-end against the bench unit — the five "definition of done"
   items below, verified by the operator, not the session.

## Definition of done (Nick, 2026-07-27)

1. A GUI Nick can access; select a known registered SPOT-ID (and the
   node id) to command, so return ACKs can be checked against the
   correct node.
2. Every configurable value adjustable via preset dropdowns — no free
   user input.
3. Visible acknowledgement that a command was sent + an in-flight
   indicator, so the operator knows not to keep sending messages that
   would fill the queue.
4. Visible confirmation that the intended device ACKed with the correct
   values.
5. Before the final manual test, the automated Phase D permutation run
   (3–5 command combinations) passes end-to-end: ACKs verified and the
   resulting image hits the backend.

## Success criteria (engineering, feeds the definition of done)

- All six commands apply correctly via Phase B, with acks observed.
- Duplicate and malformed commands are safely ignored/rejected (tested).
- Settings survive a hard power cycle.
- Daemon runs the full active window without missing a command injected at
  minute 1 and at minute 19.
- Zero regressions to the existing capture/compression pipeline.
- GUI meets definition-of-done items 1–4; Phase D automation passes 3–5
  permutations end-to-end (item 5).
