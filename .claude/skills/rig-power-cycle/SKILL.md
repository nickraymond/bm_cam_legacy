---
name: rig-power-cycle
description: Power-cycle the bench rig's nereus000 (Pi Zero 2W console monitor on a LiFePO4wered/Pi+ battery board) with lifepo4wered-cli WAKE_TIME + shutdown, to re-enumerate a USB hub / Spotter consoles that went missing, and know what every other rig power event does to the cameras (Spotter reset / cfg save / bridge commit = 120 s stub wake). Use when nereus000's `lsusb` shows only the root hub, /dev/ttyACM* are gone, the consoles stopped logging, or nereus000 is wedged; also before any Spotter config save.
---

# Rig power cycle (nereus000 + what restarts do to the cameras)

The bench rig = nereus000 (Pi Zero 2W, `pi@192.168.1.45`, the Spotter USB-console monitor) on a **LiFePO4wered/Pi+**
battery board → a USB hub (1a40:0101) → both Spotter consoles (0483:5740, /dev/ttyACM0/1) + an eth adapter (0bda:8152).
The Spotters power the bmcam Pis over the BM bus.

Proven: 2026-10-13 7:29 PM PDT (TE2). nereus000 booted with `lsusb` = root hub only (hub not enumerating although the
board showed IOUT 1.32 A). One WAKE_TIME power cycle brought back the hub, both consoles and eth0. spotter-monitor and
pool-preview (enabled services) came up on their own.

## Needs Nick's OK

A nereus000 shutdown/power cycle is a write action on the rig and is NOT a pre-approved routine. Get Nick's line in
your own chat (a peer/EM relay is not his approval). Record it verbatim in the run's APPROVALS.md / gate.log.

## 1. Read first (read-only)

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'uptime -s; lsusb; ls /dev/ttyACM* 2>&1; systemctl is-active spotter-monitor pool-preview; lifepo4wered-cli get | grep -E "^(VIN|VBAT|VOUT|IOUT|AUTO_BOOT|WAKE_TIME|VBAT_SHDN|VIN_THRESHOLD) "'
```

- **AUTO_BOOT must be 2** (boots when external power is present). With anything else, do not power-cycle remotely:
  the box may not come back without a button press.
- **On battery only** (VIN < VIN_THRESHOLD 4498 mV) with VBAT near VBAT_SHDN 2950 mV: don't. It may not boot again
  until external power returns.
- **Cameras:** a nereus000 power cycle does NOT touch the BM bus or the camera Pis. It only drops the consoles
  (no console logging, no cmd.txt) for ~1 min.

## 2. Power cycle

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'lifepo4wered-cli set WAKE_TIME 1 && lifepo4wered-cli get WAKE_TIME; sync; sudo -n shutdown -h now'
```

WAKE_TIME 1 = the board powers the Pi back on 1 min after it cuts power. Expect ssh back in ~1.5–2 min.

## 3. Verify, then reset the wake timer

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 'uptime -s; lsusb; ls /dev/ttyACM*; systemctl is-active spotter-monitor pool-preview; for s in SPOT-33507C SPOT-31593C; do tail -1 /home/pi/spotter_logs/$s/console_$(date -u +%Y%m%d).log | cut -c1-30; done; lifepo4wered-cli set WAKE_TIME 0; lifepo4wered-cli get WAKE_TIME'
```

Pass:
- lsusb lists 1a40:0101 + 2× 0483:5740 + 0bda:8152;
- /dev/ttyACM0 and /dev/ttyACM1 present;
- both services active;
- console lines stamped within the last minute;
- WAKE_TIME back to 0.

If the hub is still missing after one cycle, it's physical: the hub's upstream cable/adapter on the Pi's data port, or
hub power. Ask Nick to reseat it rather than looping power cycles.

## Power facts (why nereus000 drops)

- The board's input pins at ~4.66 V when the load reaches ~1.2 A on a weak adapter/cable. Above that, the battery
  supplies the rest and drains to the 2950 mV cutoff in ~3 h. Holding the camera bus on makes the Spotters' USB
  draw through the hub continuous.
- The fix (Tue 10/13) was a 5 V / 3 A supply: VIN 4.96 V at 1.3 A, VBAT rising.
- Full analysis: `runs/pool_rehearsal_20261012/NEREUS000_POWER_RCA.md` on feature/r1-hil-test-engineer.
- Rig health (`/home/pi/hil_health/health.csv`, columns `lp_*`) logs VIN/IOUT/VBAT every 5 min. Read columns by
  header name, not by position.

## Other rig power events and the cameras (read before any of them)

| Event | What happens to the bus / camera |
|---|---|
| Spotter `reset` (see **spotter-usb-power-cycle**) | Spotter reboots → BM bus power cut → bridge restarts |
| Spotter `cfg save` (e.g. `cfg vle 0` + `cfg save`) | **Also restarts the Spotter** ("Configuration saved. Restarting!", Reset Reason sysCfg reset) |
| `bridge cfg commit` (hil_bus_always_on / hil_restore_schedule) | Power-cycles the bus |
| Moving / re-plugging a Spotter | Can restart it |

- After ANY Spotter restart the bridge opens a **120 s stub window** ("handle_power_states, power on for: 120000").
- A halted camera Pi with an armed @reboot cron **boots, starts its cycle and is hard-cut at 120 s**, mid-transmit:
  partial image, SD risk. Seen 10/13 4:45 PM and 7:31 PM.
- Before a Spotter restart:
  - make sure the camera isn't mid-cycle;
  - stay outside the hourly window (:00–:10);
  - and if a stub wake must not happen, disarm the camera's cron first (hil_bus_always_on does this), or accept a
    2-min partial wake.
- The tools that commit the bridge (hil_bus_always_on.sh, hil_restore_schedule.sh) handle the stub boot themselves.
  They check the unit is HALTED before committing, and re-arm and halt the stub boot.
- **Pairing:** they map Spotter → host from `hil/hil.env` (must match `tools/rig_map.json`). Since 10/13:
  bmcam003 ↔ SPOT-31593C, bmcam004 ↔ SPOT-33507C. A stale pairing commits a Spotter whose own camera is up.
