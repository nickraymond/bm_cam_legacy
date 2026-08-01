# bmcam remote command reference (tables v3, Sprint12)

The operator-facing list of every command a bmcam unit accepts, and how to
send one. Source of truth for values: `BM_Devel_Pi/command_tables.py`
(this doc lists semantics, not the full tables — the GUI dropdowns are
generated from the tables and are always current).

## How to send a command

**Bench / USB (Spotter console, via `tools/spotter_serial_monitor.py`):**

```
bm pub bmcam/cmd {"id":123,"c":"twn","v":2} 1 1
```

Compact JSON, NO spaces inside the payload. `1 1` is type/version, fixed.
The Pi hears it only while a cycle is running (the daemon owns the UART
during cycles). Ack appears on the console within seconds:
`{"id":123,"ok":1,"st":{...}}`.

**Remote (Sofar Command API cloud mailbox):**

```bash
python3 tools/sofar_send_command.py --spotter-id SPOT-33507C --id 123 --cmd twn --value 2
```

or use `tools/bm_command_gui` (retry engine, one pending command per
Spotter, un-wedge button). Delivery doctrine: **re-send until acked**
(device-side dedupe makes re-sends free). Latency is HOURS, not minutes —
inbound mail drains only at the Spotter's ~hourly [MS] sync, and on
duty-cycled units most drains land while the bus is off. Backend ack lag
13–30 min is normal and never proof of non-delivery.

**Command ids:** any uint32 you pick; never reuse one of your last ~32
(the dedupe store acks duplicates without re-applying). Counting up works.

## The commands

Settings commands persist in `bm_command_state.json` and apply from cached
state on the **next boot** (Sprint11 D2 — never the in-flight cycle).
Index 0 = production default; the all-zero sequence is the factory reset.

| cmd | what it controls | notes |
|-----|------------------|-------|
| roi | crop preset (native coords) | concentric zoom, no pan |
| foc | focus (auto / manual presets) | |
| awb | white balance | index 3 = underwater gains |
| exp | EV compensation | |
| win | cycle budget minutes | |
| txd | transmit pacing s/msg | Spotter queue lever, not UART |
| cap | message cap per image | effective cap = min(cap, win·60/txd) |
| src | image source (live / reference) | persistent — see trg 3/4 for one-shot |
| hlt | power-halt override (Sprint12) | see below |
| twn | transmit-window override (Sprint12) | see below |
| trg | one-shot capture/send trigger (Sprint12) | see below |
| ping | liveness (ack only) | no value |

### hlt — power-halt override

`0` yaml governs (removes override) · `1` real halt · `2` dry-run · `3` disabled.

Only `enabled`/`dry_run` are commandable; halt mode/script stay YAML.
Applies next boot; the running cycle halts with whatever it booted with,
and its ack always leaves the uplink **before** the halt fires. Know the
stranding trade (the boot log warns loudly): `hlt 1` on a constant-power
unit = dark until a physical power cycle; `hlt 3` on a battery unit =
~0.6 W continuous drain.

### twn — transmit-window override

`0` yaml governs · `1` 10:00–15:00 · `2` 00:01–23:59 (wide) · `3` 08:00–12:00 · `4` 11:00–14:00.

Times in the unit's OWN configured timezone (twn never changes timezone).
`twn 2` is the remote un-brick for "window misconfigured, unit never
transmits" — and the remote equivalent of the `--skip-time-window` bench
flag. Presets only, by design: a single garbled int can at worst pick a
vetted window.

### trg — one-shot trigger

`0` cancel pending · `1` capture to SD only · `2` capture + send ·
`3` send reef reference (camera skipped) · `4` send reference card
(camera skipped).

NOT a setting: arms a pending trigger that the **next `--transmit` boot
consumes exactly once**, then behaviour returns to stock. The trigger
boot **always bypasses the transmit window** (that is the point — an
on-demand image regardless of schedule). `trg 3`/`trg 4` push a committed
reference image through the full encode+transmit+backend chain with the
camera skipped: verifies the link independent of optics/light (dim-room
bench testing), without touching the persisted `src` setting.

The ack means **armed**, not captured — proof of execution is the image
arriving (2/3/4) or the wake status + SD artifact (1). Realistic latency
on a duty-cycled unit: arms during one cycle's listen tail, fires next
boot (~one duty cycle later).

## State-file doctrine

One file survives power cycles: `bm_command_state.json` (atomic writes).
Delete it on the unit to restore stock YAML behaviour entirely; command
index 0 does the same per-setting. The YAML is never rewritten.

## Not remotely configurable (deliberate)

UART port/baud, daemon enable/topic/state path, time source/RTC/clock
chain, timezone, halt mode/script path, capture_mode, legacy HEIC
pipeline. Each is a lockout/brick vector; they stay SSH/provisioning-
owned. See `sprints/Sprint12_remote_config_commands/REMOTE_CONFIG_AUDIT.md`
for the full audit and the Sprint13 candidates (incl. config readback).
