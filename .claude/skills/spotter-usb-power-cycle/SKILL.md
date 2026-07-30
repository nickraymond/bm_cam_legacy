---
name: spotter-usb-power-cycle
description: Power-cycle a Sofar Spotter (and thereby its BM-bus nodes / the bmcam Pi) over the USB console — the `reset` command, how to send it through the serial monitor's cmd.txt, what a healthy recovery looks like on the console, and the hard-cut SD-corruption risk to weigh first. Use when a Pi is unreachable/wedged and needs remote power recovery, or when the Spotter itself must be rebooted.
---

# Spotter USB power cycle

Reboot a Spotter over its USB console. The Spotter reboot **kills BM-bus
power to every node** and restores it per the committed power config — so
this is also the remote way to power-cycle a wedged bmcam Pi.

Proven: 2026-07-30T02:18Z, SPOT-33507C (Sprint11; recovered bus control of a
wedged bmcam003).

## Decide first — the two risks

1. **Hard power cut.** If the Pi is mid-cycle (or otherwise alive and
   writing), cutting bus power can corrupt its SD card. Sprint11 incident:
   a bus cut mid-transmit left bmcam003 in a boot loop that TWO further
   power cycles did not fix — card pull required. If the Pi is reachable,
   `sudo halt` it first, or wait for its cycle to end. Power-cycle a LIVE
   Pi only when it is already unreachable (that being the point).
2. **`reset` is on the danger-zone list** (docs/spotter_cli_reference.md).
   Field units: explicit plan + rollback per CLAUDE.md. Bench units with a
   human in the loop: normal recovery tool.

## Sending the command

The Spotter console is owned by `tools/spotter_serial_monitor.py` (one
process per Mac; it reads both bench Spotters). Inject commands through its
FIFO file — do NOT open the port a second time:

```bash
printf 'reset\n' > ~/spotter_logs/<SPOT-ID>/cmd.txt
```

`reset` and `debug reset` are equivalent. If no monitor is running, start
one first (`python3 tools/spotter_serial_monitor.py --log-root
~/spotter_logs`) or talk to `/dev/cu.usbmodem<SPOT-ID>1` directly at 115200.

## What healthy recovery looks like (console, within ~30 s)

```
[BRIDGE_SYS] Bridge bus power: 1
[BRIDGE]     handle_power_states, power on for: 4294967295   <- always-on cfg
[BRIDGE_SYS] Neighbor <camera-node-id> added                 <- mote re-joined
<bridge-node>, power | ... current: 0.05-0.10                <- Pi boot surge
```

- `power on for:` reflects the committed schedule: `4294967295` = power
  controller disabled (always-on); a millisecond value = scheduled window.
  The committed config SURVIVES the reset — no need to re-apply it.
- Pi boot current signature on the bridge addr-65 trace: idle mote ~0.018 A,
  boot surge 0.05–0.10 A settling to >0.045 A (~1.1 W+) when up, SSH ~40 s
  after `Neighbor added`.

## Failure signature — corrupted SD

Surge to ~0.09 A then a steady ~0.028 A (~0.66 W) with no Tailscale/SSH:
the kernel started and the boot stalled (filesystem recovery). A repeat
power cycle that reproduces the same trace means the card needs physical
attention (fsck on a Mac, or reflash + the `bmcam-provision` skill). Do
not keep power-cycling past two attempts — each cut risks making the
card worse.

## Related bus-power control without a full reset

Power scheduling lives on the BRIDGE node config (values survive reset):

```
bridge cfg set <bridge_node_id> s u bridgePowerControllerEnabled 0  # bus always on
bridge cfg set <bridge_node_id> s u bridgePowerControllerEnabled 1  # scheduled
bridge cfg set <bridge_node_id> s u sampleIntervalMs 1800000        # 30 min period
bridge cfg set <bridge_node_id> s u sampleDurationMs 900000         # 15 min on
bridge cfg commit <bridge_node_id> s                                # applies + re-inits bridge
```

ALWAYS read back (`bridge cfg status <node> s` or the `Bridge network
config:` line after commit) — plain `bm cfg` fails silently. Note the
commit itself re-inits the bridge and re-evaluates power immediately, so a
commit can start or cut bus power on the spot.

Bench IDs: SPOT-33507C bridge c3c564b91856226c (bmcam003) · SPOT-31593C
bridge 0e582dd12c1e1480 (bmcam000).
