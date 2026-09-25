---
name: bm-mote-dfu
description: Flash new firmware onto ONE specific Bristlemouth mote (e.g. a bmcam camera's serial-bridge / nereus_cam mote) from a .dfu.bin file on the Spotter's SD card, over the Spotter USB console via nereus000 — find the mote's node ID, confirm the file on the SD, make sure the bus is powered, protect the Pi from the mid-DFU power cut, run `bridge dfu`, and prove the new version with `bm info`. Use when Nick (or Matt/Sofar) hands over a mote .dfu.bin to try on a bench unit. For Spotter reboots use spotter-usb-power-cycle; for console health use spotter-health-check.
---

# Flash one BM mote over the Spotter console

The Spotter can push a `.dfu.bin` from its own SD card to any node on its
BM bus: `bridge dfu <file> 0x<node_id> <timeoutMs> force`. Everything goes
through the Spotter USB console, which on the bench is owned by nereus000's
`spotter-monitor` service (see memory `project-nereus000-monitor-pi`).

Proven: 2026-09-25T18:09Z, SPOT-31593C, bmcam004's mote e6fe83ea6b4a2b7f,
`bm_mote_bristleback_v1_0-nereus_cam-dbg.elf.dfu.bin` (244,744 B):
`serial_bridge@ENG-v0.13.11-6-g54aff0a3` -> `nereus_cam@ENG-v0.13.12-6-gfcbcc11a`.
Transfer 28 s, whole DFU ~41 s.

## 0. Sending commands and reading output

Never open the ttyACM port directly while the monitor runs. Write the
command to `cmd.txt` (the monitor empties it once sent) and read the log
from the line count you recorded before sending:

```bash
ssh pi@nereus000.local 'D=/home/pi/spotter_logs/<SPOT-ID>; L=$D/console_$(date -u +%Y%m%d).log; N=$(wc -l < $L); printf "<command>\n" > $D/cmd.txt; sleep 6; tail -n +$N $L | grep -vE "power \| tick|Notecard is"'
```

Check `systemctl is-active spotter-monitor` first. `bm-heal-driver` may
also write to the same `cmd.txt` (bm pub heals); it does not conflict
with the DFU, but don't write while a file is still non-empty.

## 1. Find the mote's node ID

The node ID is the 16-hex-digit address. Sources, in order:

- `bm topo` on the console (bus must be powered).
- The `Bridge network config:` log line lists every node in **decimal** with
  its app name (`"serial_bridge"`, `"nereus_cam"`, `"bridge"`). Convert:
  `printf '%x\n' 16644886315653540735` -> `e6fe83ea6b4a2b7f`.
- `Neighbor <id> added/lost` lines, and the sender column of payload
  lines (`<epoch> <id>, <payload>`).

Known bench IDs (2026-09-25): SPOT-31593C bridge `0e582dd12c1e1480`,
bmcam004 mote `e6fe83ea6b4a2b7f`. The IDs in Nick's example commands
(`2f58e75e9f6554b5`, `e18896e0cced7ca2`) are other motes, so check
before reusing them.

## 2. Confirm the file is on the SD card

```text
ls
```

Expect the exact filename and a plausible size. Ignore the macOS
`._<name>` 4096-byte AppleDouble twins. A Spotter reboot with
`[SYS] [ERROR] SD Reset` in the log usually means someone just swapped
the card to load the file.

## 3. Make sure the bus is powered

The mote must be powered and a neighbor. Check the latest
`Bridge bus power:` line and `Neighbor <id> added`. If the Spotter runs a
bus schedule (`bridgePowerControllerEnabled 1`), either:

- **wait for the window**, and send the DFU ~30 s after
  `Neighbor <id> added` (a 10-min window easily fits a ~1-min DFU), or
- **force it on** (changes Spotter config, so Nick decides):
  ```text
  bridge cfg set <bridge_id> s u bridgePowerControllerEnabled 0
  bridge cfg commit <bridge_id> s
  ```
  Expect `handle_power_states, power on for: 4294967295`. The mote
  rejoins ~1 s after the commit.

**Every `bridge cfg commit` power-cycles the bus.** A commit reboots the
bridge, and the mote and Pi reboot with it (`Neighbor <id> added` right
after the commit). A commit with `bridgePowerControllerEnabled 1` also
opens a **120 s stub window** (`power on for: 120000`) before the schedule
takes over. 2026-09-25: it happened on 3 commits in a row. Each time the Pi
started booting into a hard cut and had to be halted over ssh inside 2 min.
So: **halt the Pi first**, then commit, and be ready to halt it again
~30 s after the commit (it boots on the new bus power).

## 4. Protect the Pi from the power cut

**The camera Pi is powered through its mote, so the DFU's mote reboot
hard-cuts the Pi.** On 2026-09-25 bmcam004 had booted at bus-on (18:08:22)
and rebooted again at 18:09:48, in the middle of the DFU. It is only
~1.5 min into the boot, but it is still an unplanned hard cut. The journal
is volatile, so it cannot show the earlier boot. The timing is strong
evidence but not proof.

Better: DFU while the Pi is halted or not booted yet. Examples: right
at bus-on, before the Pi's ~38 s boot finishes. Or after the Pi's
power_halt, with the bus still on. Or `ssh pi@bmcamNNN sudo halt` first on
a disarmed unit. Never DFU in the middle of a transmit burst.

## 5. Record the version before the flash

```text
bm info <node_id>
```

Save `GIT SHA`, `Version`, `VersionStr`.

## 6. Flash

```text
bridge dfu <file.dfu.bin> 0x<node_id> 300000 force
```

Healthy console sequence (2026-09-25):

```text
[BM_DFU] Queueing update for node: <id> with filename: <file>
DFU started!
[BM_DFU] File size: 244744
[BM_DFU] Transfer complete!                 (+28 s)
[BM_DFU] File transferred, entering update phase.
[BRIDGE_SYS] Neighbor <id> added            (+39 s, mote rebooted)
[BM_DFU] Node <id> update status: 1, 0
[BM_DFU] Transitioning to state: idle       (+41 s)
```

Don't stop waiting at "Transfer complete". The flash is done only at
`state: idle`. Wait for it with
`grep -E "update status|state: idle|\[BM_DFU\].*ERROR"`. What `update
status: 1, 0` means is not documented here, so the proof is step 7.

## 7. Check the new version

```text
bm info <node_id>
```

PASS = `VersionStr` / `GIT SHA` changed to the new build. The app name in
the next `Bridge network config:` line also changes (e.g.
`serial_bridge` -> `nereus_cam`). The config CRC change makes the Spotter
queue a LEGACY config/topology report (2 msgs), which is expected.

## 8. Restore the bus schedule

If you forced the bus on, set it back **only after the Pi has halted
itself** (the Pi log shows the power_halt, and ssh times out). Committing
while the Pi runs gives a ~2 min power window and then a hard cut
(memory `project-bridge-commit-short-first-window`):

```text
bridge cfg set <bridge_id> s u bridgePowerControllerEnabled 1
bridge cfg commit <bridge_id> s
```

Check that the `Bridge network config:` line shows
`"bridgePowerControllerEnabled": 1` and the old sample interval.

## Firmware notes

- `nereus_cam` (Matt, 2026-09-25): buffers messages published to
  `bmcam/cmd` until `cmdWaitMs` after the mote boots (default 60000),
  then sends them to the camera. After that, it forwards them as they
  arrive. `bm cfg get <id> s cmdWaitMs` returned
  `Failed to get cfg` on a freshly flashed mote. The key is probably
  unset until someone sets it (the default applies). This is not verified.
