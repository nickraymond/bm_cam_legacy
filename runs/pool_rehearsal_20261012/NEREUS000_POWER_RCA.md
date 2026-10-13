# nereus000 dropouts: root cause (TE2, Tue 10/13 ~12:20 PM PDT, desk pass, PRELIMINARY)

**Question (Nick):** why did nereus000 drop 3 times (Mon 12:14 AM, Mon ~4 PM, Tue ~11:58 AM), and what changed compared with the stable weeks?

**Evidence so far:**
- `health.csv` saved Mon 8:15 AM, which holds the rig-health record from 10/3 to Mon 8:15 AM. Columns: `lp_*` = the power board on nereus000; VBAT, VIN, IOUT and the external-power flag every 5 min.
- Tue 9:20 AM live readings.
- dmesg from Mon.
- The rest is read-only on nereus000 once it is back: journal boots, throttled bits, health.csv from Mon 8 AM to Tue 12 PM.

## Timeline (PDT)

| When | Load out (IOUT) | VIN | VBAT | What happened |
|---|---|---|---|---|
| 10/3 → Mon 12:14 AM, the stable weeks | **~0.5 A**, with a **~1.2 A spike each hour at :00–:05** (the 10-min bus window: the Spotters power their camera Pi and draw it through nereus000's USB hub) | 4.66 V in the spike, ~4.9 V otherwise | 3.35–3.40 V flat (LiFePO4 plateau), dips in each spike, recovers between | Net-balanced: 10 min of overload per hour, refilled during the other 50 |
| **Mon 12:14:53 AM** | 1.22 A → **0.21 A** | 4.66 → 5.02 V | 3.35 → **3.63 V** (charging) | USB hub 1-1 disconnect (dmesg). Spotters, ttyACM and eth0 gone. **Cause of the disconnect itself: unknown so far.** It wasn't in a bus window (the 12:00 window had ended). |
| Mon 10:21 AM | – | – | – | Nick power-cycles. The CSI ribbon was found loose after the box fell. Hub back; both Spotters start charging after their 10 h battery drain |
| **Mon 1:10 PM → Tue** | **continuous ~1.2–1.3 A** (needs the post-8 AM rows to confirm) | ~4.66 V | falling | **Bus held ON on both Spotters** (T-LAND prep, T-ENDURE, the overnight plan-E fallback). The 10-min/h spike became **24/7**, plus the Spotters recharging |
| **Mon ~4:00–4:30 PM** | – | – | → 2.95 V (cutoff) | nereus000 off: the battery covered the deficit for ~3 h until shutdown |
| Tue 9:14 AM | **1.255 A** | 4.663 V (threshold **4.498 V**) | **3.217 V, ~18 %** | Nick power-cycles. Rig health WARN "VBAT on external power (input not covering the load)". The bus is still held |
| **Tue ~11:58 AM** | – | – | → cutoff | nereus000 off again, **~2.7 h** after starting from 3.22 V |

## Most likely cause

The **bus-hold regime, not new hardware damage.** nereus000's power board feeds the Pi and the USB hub. The hub charges and powers both Spotters, and the Spotters power the camera Pis while the bus is on.
- On the hourly schedule, the 1.2 A peak lasted 10 min/h and the battery refilled in between. That's why the weeks looked stable, although the input already sagged to 4.66 V in every peak.
- Since Mon 1:10 PM the bus has been held on, so the load is a **continuous ~1.25 A**. The adapter/cable sags to ~4.66 V, just above the board's 4.498 V input threshold. The board can't take more from the input, so the **battery supplies the deficit** and runs down to its 2.95 V cutoff: ~3 h from a part-full battery Mon, ~2.7 h from 3.22 V Tue.
- The Mon 12:14 AM hub drop is a separate event, cause **not yet known**. It may be a USB brownout or connector issue, and could relate to the box fall Nick found Mon AM. It needs nereus000's logs.

## Fix (in order)

1. **Stronger supply now:** a 5 V / 3 A adapter with a short, thick USB-C cable. VIN should then stay ≥ 4.9 V at 1.3 A.
2. **Or take the Spotter load off nereus000:** a powered USB hub on its own adapter, so the Spotters' USB power doesn't go through nereus000's battery board.
3. **Operational stopgap:** don't hold the bus for hours while the Spotters hang off nereus000's hub, or restore the hourly schedule between sessions (plan E).

## How to verify (≤ 30 min, bus held so the load is the worst case)

- Rig health every 5 min:
  - IOUT ≥ 1.2 A with **VIN ≥ 4.9 V**;
  - **VBAT rising** on external power;
  - no "input not covering the load" WARN;
  - throttled = 0x0.
- Pass: VBAT up ≥ 30 mV in 30 min. Fail: flat or falling, i.e. the adapter is still too weak.

## Still to collect (read-only, when nereus000 is back)

- `journalctl --list-boots` + previous-boot tails;
- `vcgencmd get_throttled`;
- the health.csv rows Mon 8 AM → Tue 12 PM (to confirm continuous IOUT ~1.25 A and the VBAT slope before each cutoff);
- the power-board log, if it has one;
- which adapter and cable it's on now.
