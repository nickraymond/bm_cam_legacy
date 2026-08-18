# bmcam remote command reference (tables v5, Sprint13)

The operator-facing list of every command a bmcam unit accepts, and how to
send one. Source of truth for values: `BM_Devel_Pi/command_tables.py`
(this doc lists semantics, not the full tables — the GUI dropdowns are
generated from the tables and are always current).

**On-console since Sprint13:** the camera is self-documenting at the
Spotter USB terminal — send `help` for the full generated reference
(every command, every value, copy-paste examples) and `cfg` for a
post-style dump of the resolved settings with per-row source
(config file vs command N). Both are zero-quota console output
(`spotter/printf`), and both tolerate re-sends (dedupe: ack, no
re-print). This doc stays as the repo-side copy; when they disagree,
the on-console output is generated from the tables and wins.

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
| roi | crop preset (native coords) | concentric zoom, no pan; v5 adds 800x450 / 640x360 reef-test crops (output clamps to crop width, never upsamples) |
| foc | focus (auto / manual presets) | |
| awb | white balance | auto / daylight / cloudy only — the untested underwater preset was dropped in v5 |
| exp | exposure bias (EV compensation) | biases auto-metering darker/brighter; NOT a shutter time |
| win | cycle budget minutes | v5: 12 min is the production default (index 0); order 12/5/8/16 |
| txd | transmit pacing s/msg | Spotter queue lever, not UART |
| cap | message cap per image | effective cap = min(cap, win·60/txd) |
| src | image source (live / reference) | persistent — see trg 3/4 for one-shot |
| hlt | power-halt override (Sprint12) | see below |
| twn | transmit-window override (Sprint12) | see below |
| tmz | timezone override (Sprint13) | presets: LA / New York / UTC; window interpretation only, never the clock source |
| trg | one-shot capture/send trigger (Sprint12) | see below |
| ping | liveness (ack only) | no value |
| help | print the on-console command reference | query — no state change; `v` optional |
| cfg | print resolved settings + sources | query — matches --print-config; `v` optional |

### hlt — power-halt override

`0` yaml governs (removes override) · `1` real halt · `2` dry-run · `3` disabled.

Only `enabled`/`dry_run` are commandable; halt mode/script stay YAML.
Applies next boot; the running cycle halts with whatever it booted with,
and its ack always leaves the uplink **before** the halt fires. Know the
stranding trade (the boot log warns loudly): `hlt 1` on a constant-power
unit = dark until a physical power cycle; `hlt 3` on a battery unit =
~0.6 W continuous drain.

### twn — transmit-window override

`0` yaml governs · `1` 10:00–15:00 · `2` all day (24 h) · `3` 08:00–12:00 · `4` 11:00–14:00.

`twn 2` uses the full-circle pair `00:00–00:00` = true 24 h with no quiet
gap (start == end means all day since tables v4; `24:00` is not a valid
HH:MM). In YAML, `start: "00:00" / end: "00:00"` gives the same thing.

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

### wap — WiFi mode switch (Sprint16, tables v7)

`0` boot default (as the YAML `network.default` says: AP or Nereus HQ)
· `1` open hotspot NOW — SSID = the camera's hostname, NO password,
gallery at `http://192.168.50.1:8080` · `2` Nereus HQ WiFi NOW.

Like `trg`, applied IMMEDIATELY on command (documented exception to the
apply-next-boot doctrine). NetworkManager-backed (`network_ap.sh`,
Sprint16 D-S16-1): every runtime switch is SESSION-ONLY — a power cycle
always returns the unit to its YAML boot default. Remote flips (1/2)
arm and VERIFY a systemd one-shot revert timer BEFORE touching the
network (default 60 min, `network.ap_timeout_min`); the script refuses
to flip if the timer cannot arm. Revert target is the boot default.

While the hotspot is up: the green ACT LED blinks fast and steadily
(normal state is a faint SD-activity flicker), anyone in WiFi range can
reach the gallery/settings pages (open network — the UI shows a
banner), ssh on the WiFi interface is blocked, and the camera is off
the internet (no remote commands until revert). Recording continues
through every switch.

Bench-rehearsed end-to-end on bmcam000 (2026-08-18, attended): AP join
+ gallery + download, auto-revert (twice), customer-WiFi join via the
settings page with failed-join AP re-raise, and the power-cycle forget.
REMAINING: delivery over the real BM bus (needs a Spotter hosting the
unit) — the daemon's immediate-apply path is unit-tested but has not
carried a live `wap` frame yet.

SHIP PREP (Nick 2026-08-18): customer units must ship with
`network.default: ap` (the rc_field_template value) — flip it in the
settings GUI or YAML before shipment; the office/dev fleet runs
`nereus_hq`.

## State-file doctrine

One file survives power cycles: `bm_command_state.json` (atomic writes).
Delete it on the unit to restore stock YAML behaviour entirely; command
index 0 does the same per-setting. The YAML is never rewritten.

## Not remotely configurable (deliberate)

UART port/baud, daemon enable/topic/state path, time source/RTC/clock
chain, halt mode/script path, capture_mode, legacy HEIC pipeline.
(Timezone moved out of this list in Sprint13: `tmz` commands the
window-interpretation timezone via presets; the clock source itself
stays SSH/provisioning-owned.) Each is a lockout/brick vector; they stay SSH/provisioning-
owned. See `sprints/Sprint12_remote_config_commands/REMOTE_CONFIG_AUDIT.md`
for the full audit and the Sprint13 candidates (incl. config readback).
