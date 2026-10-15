# Card d26: Spotter HDR OFF (`hrpm 0`) on SPOT-31593C + SPOT-33507C (TE2, Wed 10/14 ~5:30 PM PDT)

- **Status:** prepared, NOT executed. Nick decided ~9:30 PM tonight per Matt; TE2 runs it only after Nick's own typed line in the TE2 chat.
- **Scope:** the two bench Spotters only. Never SPOT-33361C (field).

## Facts this card rests on

- **The key:** console `cfg` shows `hrpm = 1 - "" (0,6)` on both Spotters (runs/remote_reset_20261007/cfg_*.txt): current 1, range 0–6. Matt: 0 = HDR off.
- **Today, with hrpm 1:** each Spotter queues a ~6.1 KB HDR every 300 s (12/h, at the 5-min marks).
  - At the report it holds one of the 2 cell-queue slots for 34–308 s (memory: HDR slot hold 10/6).
- **Unknown (Matt):** whether `hrpm` takes effect without `cfg save`. The card assumes set + `cfg save`, so the value persists.
- **`cfg save` RESTARTS the Spotter** ("Configuration saved. Restarting!", proven 10/13 with `cfg vle`). Two consequences:
  1. A 120 s stub window: the camera on that Spotter boots, runs its armed production cycle, and is HARD-CUT at 120 s. One partial image (~70 chunks; self-heal fills it), plus an SD hard-cut risk (EXT4 orphan cleanup seen twice, both recovered).
  2. The Spotter's report minute re-rolls (boot-anchored to the next 5-min mark after the post-boot report).
- **This can't be avoided tonight:** the cameras are underwater in pool production. No Wi-Fi means no ssh, so cron can't be disarmed first or the stub boot caught. Only a Spotter restart while the camera sits dry on Wi-Fi avoids the cut (e.g. at Friday pickup).

## Timing window

- Between wakes, **:15–:50**: never inside :00–:10, and away from the :05 / :10 report marks.
- **One Spotter at a time, ≥ 10 min apart:** SPOT-33507C (bmcam004) first, then SPOT-31593C (bmcam003).
- Must land before the Thursday-evening freeze. Tonight ~9:30 PM → e.g. 9:20 and 9:35 PM (or 9:30 / 9:45).

## Commands (console via nereus000; hil tools, HIL_MONITOR = pi@100.82.38.22 Tailscale)

Per Spotter `S`:
```
hil/tools/hil_console.sh S "cfg hrpm" 4        # PRE read-back: expect hrpm = 1
hil/tools/hil_console.sh S "cfg hrpm 0" 4      # set
hil/tools/hil_console.sh S "cfg save" 4        # persist -> Spotter restarts (stub wake starts)
# wait ~3 min (restart + 120 s stub window ends, the camera is cut)
hil/tools/hil_console.sh S "cfg hrpm" 4        # POST read-back: expect hrpm = 0
```

## Checks (pass bars set now)

1. **Read-back:** `hrpm = 0` after the restart, on both.
2. **HDR really off:** in the console log, NO `[HDR] [INFO] HDR Message … added to queue (len: 6129)` lines in the 20 min after the restart. Before: 4 expected per 20 min.
3. **Report still sent:** the Spotter's report (LEGACY) still appears.
   - Record the NEW report minute per Spotter: the first report after the restart, rounded to its 5-min mark.
   - Compare with today's (SPOT-31593C :05, SPOT-33507C :10 as of 10/6; re-rolled since by resets).
4. **Cameras:** the next scheduled wake (:00) on each runs normally (bus on, ~190 submits). The stub-wake partial shows up in the media table as a new partial image, healing over 1–2 wakes.
5. **Benefit check, 2 wakes after:**
   - `Queue MS_Q_CELLULAR_ONLY is full` count per wake vs before;
   - no 34–308 s slot hold at the report minute.

## Rollback

- Per Spotter: `cfg hrpm 1` + `cfg save` (same window rules; another restart → another stub wake + report-minute re-roll).
- Check: read-back `hrpm = 1`, and HDR lines return every 5 min.

## Approval line Nick pastes in TE2's chat

> OK: set hrpm 0 + cfg save on SPOT-33507C and SPOT-31593C (bench only, never SPOT-33361C) tonight between wakes, one at a time; accept one stub-wake partial image per camera; rollback = cfg hrpm 1 + cfg save.
