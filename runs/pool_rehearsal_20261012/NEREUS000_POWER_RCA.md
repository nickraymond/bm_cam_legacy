# nereus000 dropouts: root cause (TE2, Tue 10/13 12:40 PM PDT)

**Question (Nick):** why did nereus000 drop 3 times (Mon 12:14 AM, Mon ~4:18 PM, Tue ~11:49 AM), and what changed compared with the weeks it was stable?

**Evidence:** `nereus000_forensics/`, captured read-only Tue 12:18 PM:
- full rig-health `health_full.csv` (10/3 → Tue 12:15 PM, every 5 min). Columns: `lp_*` = nereus000's battery/power board, IOUT = what it supplies to the Pi + USB hub;
- `ALERTS.log`, `journalctl --list-boots`, `vcgencmd get_throttled`, dmesg.

## Timeline (PDT)

| When | IOUT | VIN | VBAT (LiFePO4; cutoff 2950 mV) | Event |
|---|---|---|---|---|
| 10/3 → Mon 12:14 AM (stable weeks) | ~0.5 A, **~1.2 A at :00–:05 each hour** | ~4.9 V; **4.66 V in each spike** | 3.35–3.40 V, net flat | The hourly 10-min bus window: both Spotters power their camera Pi and draw it through nereus000's USB hub. The battery covers the spike and refills during the other 50 min |
| **Mon 12:14:53 AM** | 0.53 → 0.21 A | → 5.0 V | → 3.63 V (charging) | dmesg: USB hub 1-1 disconnect. **Not a power event:** load was low, outside the bus window, and throttled = 0x0. Likely mechanical (cable/hub disturbed; Nick later found the box had fallen) |
| Mon 10:21 AM | 0.9–1.36 A | 4.66 V | 3.53 → 3.25 V | Nick power-cycles; hub back. Both Spotters recharge after 10 h on battery |
| Mon 1:10 PM → 4:18 PM | **1.13–1.21 A continuous** | **4.66 V, pinned** | 3.38 → 3.12 → **2.95 V** | **Bus held on both** (T-LAND prep / T-ENDURE). ALERTS: "VBAT on external power (input not covering the load)", 2966 → 2950 → 2896 mV. Shutdown ~4:18 PM |
| Tue 9:15 → 11:49 AM | **1.25–1.29 A continuous** | **4.66 V, pinned** | 3.26 → 3.08 V (−1.3 mV/min, faster near the end) | Nick power-cycles; bus still held (overnight hold, then T-FOCUS). Shutdown ~11:49 AM, then **4 brownout reboots in 2 min** (journal boots −3…−1, 0–2 min each) |
| Tue 12:09 PM | 1.25 A | 4.66 V | 3.14 V | Back up. **12:19–12:25 plan-E restore removed the bus hold.** Shutdown again ~12:33 (no ssh), most likely from the battery already at ~3.1 V |

## Most likely cause

**A load-regime change, not a hardware failure.**
- nereus000's power board feeds the Pi and the USB hub. The hub powers both Spotters, and the Spotters power the camera Pis whenever the bus is on.
- Its input tops out at ~1.2 A: VIN pins at 4.66 V, just above the board's 4.498 V threshold, every time the load reaches 1.2 A. Above that, the battery supplies the rest.
- **On the hourly schedule** that overload lasted 10 min/h and the battery refilled, so it looked stable for weeks.
- **Since Mon 1:10 PM the bus has been held** for the pool tests, so the overload became continuous (1.15–1.29 A), and the battery runs to cutoff in ~3 h from mid-charge.
- Mon's 12:14 AM hub drop is a separate, non-electrical event (low load, no throttling). Treat it as a cable/mechanical issue and secure the hub.

## Fix

1. **Separate supply for the Spotters' USB power:** a powered USB hub on its own 5 V / 3 A adapter. The camera load then never flows through nereus000's battery board. This is the best fix.
2. **And/or a stronger nereus000 input:** a 5 V / 3 A adapter with a short, thick USB-C cable, so VIN stays ≥ 4.9 V at 1.3 A.
3. **Operational rule until then:** don't hold the bus with the Spotters on nereus000's hub for more than ~1 h, and restore the hourly schedule between sessions (plan E).
4. Secure the hub and its cable mechanically (strain relief), against a repeat of Mon 12:14 AM.

## Verify (30 min, worst case = bus held on both)

- Rig health every 5 min:
  - IOUT ≥ 1.2 A with **VIN ≥ 4.9 V**;
  - **VBAT rising ≥ 30 mV / 30 min**;
  - no "input not covering the load" WARN;
  - throttled = 0x0.
- With fix 1, IOUT should instead fall to ~0.2–0.5 A during the hold.
