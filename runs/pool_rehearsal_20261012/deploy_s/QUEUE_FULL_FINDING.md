# bmcam003 "runs of exactly 10" loss = Spotter cell-queue stalls, not #146/#147 (TE2, Tue 10/13 11:30 PM PDT)

**Question (EM 11:28 PM):** bmcam003, now on SPOT-31593C, loses chunks in runs of exactly 10, ~63–74 chunks apart. Is this a regression from tonight's deploy?

**Answer: NO.** The same mechanism runs on BOTH Spotters in every log since 10/10. That covers:
- the weekend soak;
- before the 10/13 camera swap;
- before tonight's deploy.

## Evidence (read-only)

1. **The losses never reached the Spotter's queue.** For the 11 PM wake, bmcam003 image 58481 (key 0eq3d0) is missing chunks 43–52, 106–115, 128, 174 and 180–189:
   - SPOT-31593C logged 166 transmit-data submits for ~199 messages sent;
   - the submit timeline has exactly three 11.1 s gaps, before submit #45, #98 and #160, which map to chunks 43–52, 106–115 and 180–189.
2. **Inside each gap** the console shows `[MS] [ERROR] Queue MS_Q_CELLULAR_ONLY is full` → `[BM_TX] [ERROR] Unable to submit message to cell-only queue`, once per arriving chunk (~1.02 s apart).
   - It lasts until the next `[MS] Queuing message … to Notecard`, ~10.8 s after the previous hand-off.
   - The cell-only queue holds 2, so a 10.8 s Notecard hand-off stall at 1.0 s/msg pacing = 10 rejected chunks.
3. **Pre-deploy images (9ec4cb7, settings off) show the same runs:**
   - 58467 (bmcam003, 8 PM): missing 14–23, 86–95, 143–152;
   - 58469 (bmcam003, 9 PM): missing 88–97, 159–168.
4. **The per-wake queue-full history is in `qfull_by_wake_20261010_14.txt`** (script `qfull.py`, run on nereus000 over the console logs):
   - SPOT-31593C (bmcam004's Spotter until 10/13 1:50 PM): runs of 10 in most wakes since 10/10.
   - SPOT-33507C (bmcam003's Spotter until the swap): runs of 10 in most wakes, plus long runs of 25–51 on 10/10–10/12.

| Wake (UTC) | SPOT-31593C → bmcam003 (full, runs ≥ 5) | SPOT-33507C → bmcam004 |
|---|---|---|
| 10-14 03Z (8 PM, pre-deploy) | 31, [7, 10, 10] | 11, [10] |
| 10-14 05Z (10 PM, switched on) | 11, [7] | 2, [] |
| 10-14 06Z (11 PM, switched on) | 32, [10, 10, 10] | 1, [] |

Both post-deploy wakes sit inside each Spotter's own pre-deploy range. The camera-side timing barely moved: the -p encode takes 30–32 s against 29–31 s, and the burst starts ~43 s after the WS message either way.

## What it means

- The fixed "1 chunk per stall" model (Sprint25: 3.4–3.7 s stalls) is out of date. Stalls of ~10.8 s recur every ~60–80 s on some hours, and each one costs 10 chunks at 1.0 s/msg pacing.
- **Self-heal recovers them** (keyed rsd). With heal_order=after, the heal chunks also cross this queue. So the 12 AM–3 AM logs must show heal chunks accepted vs rejected; the overnight collector + the console cover it.
- The units run `uplink.msg_interval_s` 1.0. The device profile says 1.3, and deploy_s reports the mismatch only.
  - At 1.3 s/msg a 10.8 s stall would cost ~8 chunks, not 10. Pacing alone doesn't remove it.
  - A real fix needs Spotter/Notecard-side insight (why the hand-off stalls ~10.8 s), or a camera-side back-off on rejected submits.
  - Neither is for tonight.
