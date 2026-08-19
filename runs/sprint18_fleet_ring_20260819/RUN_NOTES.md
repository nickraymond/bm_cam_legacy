# Fleet overnight HIL — ring buffer under three video settings
Started 2026-08-19 06:00Z. Evaluate in the AM.

## What is running

All three units carry the Sprint18 build (`feature/sprint18-video-ui`,
PR #45) and are recording continuously in video mode, 15 fps, 5-minute
clips. The single deliberate variable is the quality point:

| Unit | Preset | Bitrate | Recorded size | scale |
|---|---|---|---|---|
| bmcam000 | `wide_720p_lean` | 4 Mbps | 1280x720 | 0.556x |
| bmcam004 | `wide_1080p_lean` | 6 Mbps | 1920x1080 | 0.833x |
| bmcam003 | `wide_1080p` | 9.3 Mbps | 1920x1080 | 0.833x |

Everything else is held equal: fps 15, clip_minutes 5,
session continuous, `max_used_pct: 60`, `min_free_gb: 10`,
`ring_dry_run: false`.

## Why the cap is 60% and why there is ballast

At the shipped 75% cap none of these units would have reached the ring
overnight — bmcam000 needed +70 GB. A ring test that never triggers the
ring proves nothing, so:

- `max_used_pct` was lowered to **60%** on all three;
- each unit carries a **ballast file** (`/home/pi/ringtest_ballast.bin`)
  allocated with `fallocate` — instant, no SD wear, and it lives OUTSIDE
  `videos/` so the ring never treats it as a clip. It puts each unit at
  58.5% so recording crosses the cap within a couple of hours.

Safety check before starting: if every clip were pruned, used% would
fall to 47.4 / 26.9 / 34.6% (000/003/004) — all far below the cap, so
the "pause rather than brick" path cannot trigger spuriously.

**bmcam004 additionally carries `/home/pi/ringtest_nudge.bin` (2.5 GB)**,
which pushed it over the cap immediately to prove the ring engages
rather than assuming it would.

### CLEAN UP AFTER THE EVALUATION

    ssh pi@bmcamNNN 'rm -f /home/pi/ringtest_ballast.bin /home/pi/ringtest_nudge.bin'

and restore `max_used_pct` to 75 (GUI or `patch_yaml`). Until that is
done these units are running with an artificially small footage window.

## Evidence already captured (06:11:40Z, bmcam004)

The ring engaged at the first boundary after crossing:

    [RING] deleted 2026-08-18T05-39-29Z_video_1000x562_15fps (66329498 B, 3 files)
    ... 19 clips, oldest-first ...
    [VID] clip start: 2026-08-19T06-11-21Z_video_1920x1080_15fps

300 -> 282 clips, three files per clip (mp4 + thumb + sidecar), used%
settled at exactly 60.0%, recording continued without interruption.

## Pre-run archive

The ring deletes the whole clip triple, sidecar included, so the
historical record would be destroyed by the very test that exercises it.
All sidecars were archived first:

- on each unit: `/home/pi/sidecar_archive/` (283 / 287 / 299 files)
- on the Mac: `scratchpad/prerun_sidecars/bmcam00{0,3,4}.tgz`

## What to look for in the AM

- **Ring correctness:** deletions strictly oldest-first; exactly 3 files
  per clip; never a `.part`/`.tmp`; `manifest.json` count always equal
  to the mp4 count; used% holding just under 60% rather than sawtoothing
  wildly.
- **No pause:** `paused=True` should never appear — there is ample
  prunable footage on all three.
- **Ring vs daylight:** clip sizes track scene complexity, not just the
  bitrate ceiling (Sprint16 finding). Expect prune rate to jump around
  13:20Z when the office lights come up, and the three units to diverge
  by their bitrate.
- **Mux dead time at the new presets:** boundary_s versus file size, per
  quality point — the TODO-BM-013 cost.
- **UI under churn:** the gallery is now reading a manifest that is being
  actively pruned. Day grouping, paging and the per-day counts should
  stay consistent, and `/clip/<stem>.json` should 404 cleanly for a clip
  deleted between list and tap rather than erroring.
- **Storage panel honesty:** the measured retention figure should track
  the actual burn as it changes.
