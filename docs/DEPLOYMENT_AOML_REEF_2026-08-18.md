# AOML Reef Test — Deployment Settings (bmcam001 / bmcam002)

**Set 2026-08-18. Config-only change; runtime stays at `main` `0d03a62`.**

These are the settings both units carry for the AOML reef 48-hour test. If you
are asking "what is deployed on the reef units right now", this is the answer.

## Canonical settings

Both units are identical apart from comment text.

| Setting | Value | Notes |
|---|---|---|
| Runtime SHA | `0d03a62cf565c761795d0380a412029f66866ef7` | `main`; `software_sha.txt` = `0d03a62cf565`. NOT rolled forward this session. |
| `capture_mode` | `progressive_jpeg` | Sprint08 RC path (`rc_progressive_jpeg.py`) |
| `time_source` | `spotter_utc` | **Changed 2026-08-18** on bmcam001 (was `rtc`) |
| `set_system_clock_from_spotter` | `true` | **Changed 2026-08-18** on bmcam001 (was `false`) |
| `enforce_time_window` | `true` | |
| `transmit_window` | `10:00`–`15:00` America/New_York | Daytime only — see "Known behaviors" |
| `progressive_jpeg.max_run_time_min` | `8` | **Changed 2026-08-18** (was `16`) |
| `progressive_jpeg.message_cap` | `195` | unchanged |
| `power_halt.enabled` | `true` | **Changed 2026-08-18** (was `false`) |
| `power_halt.dry_run` | `false` | **Changed 2026-08-18** (was `true`) |
| `power_halt.mode` | `halt` | wraps `tools/power/tuned_halt.sh` |
| `bm_serial.network_type` | `0x02` | cellular-only queue |
| `bm_serial.image_buffer_size` | `384` | chars/msg |
| `bm_serial.image_transmit_delay_seconds` | `1.0` | s/msg |

Per-unit identifiers:

| | bmcam001 | bmcam002 |
|---|---|---|
| Spotter | `SPOT-33361C` ("Cheeca Rocks") | intentionally blank — see note |
| Sofar API token | `SOFAR_API_TOKEN_AOML` | n/a |
| Tailscale IP | `100.92.225.73` | `100.118.150.44` |

bmcam002's Spotter/bridge pairing is deliberately not recorded here: as of
2026-08-18 it was not known which Spotter it bridges to, and no `hn=bmcam002`
traffic appeared on any of the 7 Spotters visible across both API tokens.
Nick verifies bmcam002 delivery manually; do not guess an ID into this table.

## Measured behavior (2026-08-18 live runs, this configuration)

| | bmcam001 | bmcam002 |
|---|---|---|
| Cycle wall clock | 3.13 min (177.5 s UART, 173 msgs @ q70) | 2.90 min (163.3 s UART, 159 msgs @ q70) |
| Capture | 4.65 s | 4.65 s |
| Quality selected | q70 after q90/q80 exceeded the 195-msg cap | same |
| Halt | fired, box went dark | fired, box went dark |

Final confirmation runs, both units in the exact shipped configuration
(armed cron + `spotter_utc` + 8 min ceiling + live halt), 2026-08-18 18:50Z:

| | bmcam001 | bmcam002 |
|---|---|---|
| Trigger | forced `--transmit` after arming | its own `@reboot` cron cycle |
| Result | `sent=130/130 complete=True`, uart 133.6 s | `sent=169/169 complete=True`, uart 173.4 s |
| Wall clock | 2.48 min | 3.17 min |
| Quality | q90 (fit first attempt) | q80 (2 attempts) |
| Halt | executed, box dark | executed, box dark |

Cycle length varies roughly 2.4–3.2 min because quality selection tracks scene
brightness — a darker frame compresses smaller and fits at a higher quality.

## Known behaviors — learned the hard way, do not re-derive

**The 8-minute ceiling does not bind.** Effective message cap is
`min(message_cap, floor(max_run_time_min*60 / image_transmit_delay_seconds))`
= `min(195, floor(8*60/1.0))` = `195`. The message cap binds first, so 8 min is a
safety ceiling for a cycle that goes wrong, not a quality knob. Confirmed in the
logs: rejected quality steps all showed `over_cap=True, budget_fit=True`.

**`@reboot` is the ONLY cron trigger.** There is no periodic entry. The external
power cycle IS the scheduler — one power-on produces exactly one image. On
continuous power a unit takes one picture and then sits idle forever.

**The halt is unconditional at cycle end.** It runs in `rc_progressive_jpeg.py`'s
`finally`, on success AND on budget exhaustion. There is no "only if early"
branch; `power_halt.enabled` is the only switch. Recovery from a real halt is a
power cycle only — nothing wakes the board from software.

**Armed vs disarmed** is one crontab line:
`@reboot /bin/bash /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh`.
Armed = production (auto-capture on every boot). Disarmed = the line is commented,
unit boots to idle and stays reachable. Disarm is the only safe way to do
maintenance on a unit whose halt is live, because an armed unit is reachable for
only ~3 min per power cycle.

**Catching an armed unit requires a watcher.** Poll SSH every 2–5 s and do the
crontab backup + disarm + `pkill -TERM` in ONE session. `SIGTERM` kills Python
without running `finally`, so no halt fires. This caught both units at ~0 min
uptime. See `.claude/skills/bmcam-field-update`.

**Outside the transmit window nothing is captured.** The gate returns early
(`rc_progressive_jpeg.py`, `if not allowed: ... return summary`), emits a
`skip_win` heartbeat, and the `finally` halts the box. With a 10:00–15:00 window
an overnight run produces skip heartbeats and no imagery. Over a 48-hour test
this still yields two full daytime capture windows.

**Spotter UTC works without a GPS fix.** bmcam001 read Spotter UTC and set its
system clock with no GPS signal, which also corrected a ~64 s RTC drift. The
earlier concern that this unit's bridge firmware could not receive
`spotter/utc-time` was not borne out. Note the failure mode if it ever does fail:
`allow_system_clock_fallback: false` means the gate fails closed and the cycle
transmits nothing (`spotter_time_sync.py`, "Spotter time unavailable and fallback
disabled"). The `rtc:` block is retained in both profiles so reverting is a
two-line change.

**Sofar API tokens are split across two accounts.** `SOFAR_API_TOKEN_AOML` covers
`SPOT-33361C` (bmcam001) and the other reef buoys; `SOFAR_API_TOKEN_BM_REEF`
covers only `SPOT-33507C` and `SPOT-31593C` (bench units). Querying with the wrong
token returns HTTP 400 `Device not found`, which looks like a bad Spotter ID but
is not. Also: macOS system `python3` fails these queries with
`SSL: CERTIFICATE_VERIFY_FAILED` — pass an `ssl` context built from `certifi`, or
use `curl`.

**Delivery to Sofar is near real-time, not lagged.** A 165-message burst appeared
at the backend over the same ~3 min the transmit took. So an empty query well
after a burst means the messages did not arrive, not that they are still in
flight. (During this session the units were indoors without signal, so delivery
was not verifiable end to end.)

**`/tmp` is cleared on reboot.** Scripts staged there for a maintenance session
must be re-staged after any power cycle.

## Rollback

On-unit, timestamped backups (both units):

```
cp /home/pi/BM_Devel_Pi/camera_schedule.yaml.before_devmode_<TS>     .../camera_schedule.yaml   # 8 min + halt
cp /home/pi/BM_Devel_Pi/camera_schedule.yaml.before_spotterutc_<TS>  .../camera_schedule.yaml   # bmcam001 time_source
crontab /home/pi/crontab_backup_devmode_<TS>.txt                                                # re-arm
```
