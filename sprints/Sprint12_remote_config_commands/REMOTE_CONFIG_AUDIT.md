# Remote-config audit — every YAML key, classified (2026-07-31)

Nick's question mid-Sprint12: "should ALL YAML settings be configurable
from the USB terminal / remotely?" Decision (Nick, same day): NOT added to
Sprint12 — this audit is the input to a Sprint13 spec.

Classification of every key in `device_profiles/*/camera_schedule.yaml`:

## A. Already remotely commandable (after Sprint12, tables v3)

| YAML key | command |
|----------|---------|
| progressive_jpeg.crop x/y/w/h | roi |
| progressive_jpeg.max_run_time_min | win |
| progressive_jpeg.message_cap | cap |
| bm_serial.image_transmit_delay_seconds | txd |
| camera_controls focus / white_balance / exposure | foc / awb / exp |
| (source image substitution, no YAML key) | src, trg 3/4 |
| power_halt.enabled / dry_run | hlt |
| transmit_window.start / end | twn |

## B. Sprint13 candidates — add as preset commands (cheap now the
## hlt/twn pattern exists: table + overlay + tests ≈ half a day each)

| YAML key | sketch | why an operator would want it |
|----------|--------|-------------------------------|
| progressive_jpeg.quality.ladder | `qly` — a few vetted ladders | image quality vs. delivery trade in the field |
| progressive_jpeg.output_width | `owd` — 600/800/1000/1300 | biggest payload lever after quality |
| bm_serial.image_buffer_size | `chk` — 200/300/384 | Sprint09-locked; presets only |
| transmit_phase.enabled | `phs` — off/on | toggle C2 scheduling remotely |
| bm_commands.post_transmit_listen_s | `tal` — 0/60/150/300 s | energy vs. command-latency lever |

Plus the standout capability request (Nick 2026-07-31/08-01) — now
SPECCED as Sprint13 (sprints/Sprint13_console_help_readback/SPEC.md):

- **`help`** (verbose name, NOT `hlp` — Nick feedback): Spotter-help-style
  verbose console reference — commands, shorthand meanings, value tables,
  copy-paste examples (e.g. force-capture trg lines). Customer-facing.
- **`cfg`**: post-style dump of the effective RESOLVED config — human
  name / variable syntax / value / source (yaml vs command) per line.

Console-print transport (candidate: spotter/fprintf, zero cellular
quota) is Sprint13's design question T1; remote/cellular readback of
`cfg` is its O1.

## C. Provisioning-only — deliberately NEVER remote

| YAML key | why not |
|----------|---------|
| uart_port, baudrate | wrong value = no comms at all (total brick) |
| bm_commands.enabled/topic/state_path | remote lockout (disabling the daemon remotely = disabling SSH over SSH) |
| time_source, rtc.*, set_system_clock_*, allow_system_clock_fallback | clock-integrity chain; wrong source silently breaks the window gate |
| timezone / timezone_preset | wrong tz + window = schedule confusion; twn is deliberately one-variable |
| power_halt.mode / script_path | the wire must never point the halt at a different executable |
| capture_mode | flips the whole runtime path (RC vs legacy HEIC) |
| image_pipeline.* (HEIC block), image.* | legacy/rollback config |
| enforce_time_window | redundant: `twn 2` achieves the same, reversibly |
| spotter_time_timeout_seconds | tuning knob with brick-adjacent failure modes, no field use case |
| bm_serial.network_type | queue selection is a billing/provisioning decision |
| bm_commands.defer_acks_during_transmit | Sprint11 C3 experiment flag, not an operator control |
| media_gid island | wire-format flag; changing it mid-deployment corrupts backend reassembly |

Growth caveat for B: every settings command widens the ack `st` dict
(~7 bytes each). Fine through ~15 commands; past that the ack needs a
split or trim (note in the Sprint13 spec).
