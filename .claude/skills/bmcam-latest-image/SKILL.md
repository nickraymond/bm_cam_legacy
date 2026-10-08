---
name: bmcam-latest-image
description: Pull the NEWEST image a bmcam unit already captured (its own display JPEG, or the native full / DNG) over Wi-Fi during the unit's bus window, instead of waiting for the compressed image to transmit over the Spotter. Read-only, never triggers a capture, never touches the cycle on an armed unit. Use when Nick asks "can you see X in the scene / is it in focus" or any fast visual feedback on what a bench unit just shot. For a fresh capture on an idle unit use bmcam-photo-check instead.
---

# bmcam latest image (fast feedback over Wi-Fi)

Purpose: show what an ARMED bench unit (bmcam003/004-style: wakes at :00, halts
~:06–:08) captured at its last wake within a minute or two, without waiting
1–4 h for the Spotter transmit + heal. Proven by the Test Engineer on bmcam004
2026-10-08 11:04 AM (card-placement check for Nick).

## Golden rules

- **Read-only.** Only `ls` and `scp`. Never run the cycle script, never touch
  crontab, YAML, overlays, `sent/`, or `/dev/shm`. Never `sudo`.
- **Not during a counted test wake.** If the unit is inside a gated bench test
  (ask the Test Engineer / check the board), skip it or wait for a free window.
  Reading files is harmless, but an ssh session during a scored wake is a
  variable the test did not plan for.
- **Inside the bus window only.** The unit is dark ~50 min per hour. Start at
  **≥ HH:01:00** (capture is written ~HH:00:35, the display JPEG ~HH:00:41) and
  finish before the halt (~HH:06–08). Retry ssh until the unit answers; bound
  every ssh (`-o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3`).
- Keep the Mac awake (`caffeinate -i`) if you are waiting for a window.

## Where the images are

`/home/pi/BM_Devel_Pi/images/` on the unit. Per capture, stem `<UTC>_image_…`:

| file | what | size |
|---|---|---|
| `<stem>_compressed.jpg` | the unit's own display JPEG (1000 px, progressive); written even on JPEG XL (nrjxl) units, ~7 s after capture | ~50 KB |
| `<stem>_compressed.nrjxl` | the transmitted JPEG XL container (needs the backend decoder) | ~50 KB |
| `<stem>_native_full.jpg` | full-sensor JPEG | ~2.7 MB |
| `<stem>*.dng` | RAW (when `--raw` capture is on) | large |
| `<stem>*.json` | metadata sidecars (exposure, gain, config hash, GPS) | small |

## Steps

1. List the newest files (first line = newest):
   ```bash
   ssh -o BatchMode=yes -o ConnectTimeout=3 pi@bmcam004 'cd /home/pi/BM_Devel_Pi && ls -lt --time-style=+%FT%TZ images | head -12'
   ```
2. Pick the first `*_compressed.jpg` (≤ 1 MB). For more detail take
   `<stem>_native_full.jpg`; for RAW work take the newest `*.dng`.
3. Copy it:
   ```bash
   scp -o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3 pi@bmcam004:/home/pi/BM_Devel_Pi/images/<file> runs/card_check_<date>/
   ```
4. Units mount the camera upside down: rotate 180° before showing
   (`display_rotation_deg: 180` is what the dashboard applies).
   Downscale a native full for viewing: `sips -Z 2304 in.jpg --out out.jpg`.
5. Show it (Read the file in the session, or SendUserFile) and say what you see
   and what you can't judge (a 1000 px progressive JPEG is fine for framing and
   exposure; judge fine focus on the native full).

## Host lookup

Tailnet name first (`pi@bmcam004`), then LAN `pi@bmcam004.local`; see
`bmcam-photo-check` step 1 for the sweep and host-key recovery.

## Known limitations

- A unit that is halted (bus off) cannot be reached; wait for the next window.
- JPEG XL partials on the dashboard show a placeholder until every chunk
  arrives; this skill is the way to see such a frame before the heal lands.
- The unit's display JPEG is already colour-processed by the camera pipeline;
  for colour-card measurement use the DNG / nrjxl, not the display JPEG.

Reference run: `runs/card_check_20261008/pull_latest_004_18z.sh` (Test
Engineer, 2026-10-08).
