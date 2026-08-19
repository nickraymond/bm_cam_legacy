# Sprint17 sensor-mode probe — bmcam000, 2026-08-18 19:26-19:33Z

Tool: `tools/probe_video_sensor_modes.sh` (two matrices: `modes` 6 s
discovery, `shortlist` 20 s candidates). Unit: bmcam000, dev mode,
rpicam-apps v1.12.0, libcamera v0.7.1+rpt20260609, CmaTotal 256 MB.

Recording cycle was stopped for the probe (crontab backed up to
`/home/pi/crontab.before_sprint17_probe_20260818T192325Z.txt`, never
edited) and restarted afterwards; crontab intact, gallery serving,
clips rolling again at 19:37Z. Raw `.h264`/`.mp4` stay on the unit
(`/home/pi/sprint17_probe_*`); logs + CSV + manifests are here.

## Coordinate systems (manifesto rule 12)

- **native px** — 4608x2592 IMX708 sensor-equivalent, what `crop_xywh` uses
- **roi fractions** — 0..1, what `--roi` takes
- **mode FOV** — the native-coordinate rectangle a given sensor mode reads
- **mode px** — the readout the pipeline delivers before the ROI zoom
- **available px** — mode px inside the ROI = the real detail behind the output
- **output px** — encoded `--width/--height`

## Finding 1 — production video is 1.88x UPSCALED (the headline)

`rpicam-vid` picks the sensor mode from `--width/--height` **alone**;
`--roi` is a digital zoom applied *after*, on the already-chosen mode.
Today's shipped argv (`--width 1000 --height 562 --roi 0.326389,...`)
selects mode **1536x864**, leaving **533x299** real pixels behind a
1000x562 output — a **1.88x ISP upscale**. No bitrate fixes this.

Evidence (`modes/prod_today.log`):

```
Mode selection for 1000:562:12:P(15)
    SRGGB10_CSI2P,1536x864/120.135 - Score: 1214.24     <- chosen (lowest)
    SRGGB10_CSI2P,2304x1296/56.0255 - Score: 1514.24
    SRGGB10_CSI2P,4608x2592/14.3536 - Score: 3707.07
Selected sensor format: 1536x864-SBGGR10_1X10/RAW
Using crop (main) (1770, 996)/1066x599
```

The settings-GUI option "1600 px wide (sharpest, 1:1 with crop)" is
worse, not better: same mode, same 533x299 of real detail, **3.00x**
upscale (`modes/prod_crop_1to1.log`).

## Finding 2 — `--roi` is relative to the MODE's field, not the sensor

D-S15-3 states `--roi` fractions are "fractions of the 4608x2592
sensor". They are not. The 1536x864 mode reads only the **center
3072x1728** of the array (ScalerCrop range `(768,432)/3072x1728`), so
the intended stills crop `(1504,846,1600,900)` actually lands as
**`(1770,996,1066,599)`** — tighter and offset. **Video FOV has never
matched stills FOV**, silently, since Sprint15.

Modes 2304x1296 and 4608x2592 both read the **full** 4608x2592 field,
so the mapping is correct there — forcing `--mode 4608:2592:10:P`
lands the ROI at exactly `(1504,846)/1599x899`, 1.00x
(`modes/prod_crop_1to1_fullmode.log`). **A preset must never leave the
sensor mode to auto-selection.**

## Finding 3 — 1920x1080 is a hard ceiling

2304x1296 output fails at pipeline start:
`ERROR: *** failed to start output streaming ***`
(`modes/wide_full_1440_wide.log`, rc=255). The VC4 H.264 encoder tops
out at 1080p. There is no "1440p" tier to design for.

## Finding 4 — two honest 1080p routes, with different costs

| route | sensor mode | FOV | avail px | scale | achieved fps | CmaFree during |
|---|---|---|---|---|---|---|
| full FOV, binned | 2304x1296 | whole sensor | 2304x1296 | 0.83x **down** | 14.6 @15 req | 50 MB |
| 1:1 native crop | 4608x2592 | 1920x1080 box | 1920x1080 | **1.00x** | 12.1 @14 req | **292 kB** |

The binned route is the cheap one: full field of view, a genuine
downscale, comfortable CMA. The 1:1 route buys true per-pixel detail
but costs field of view, caps at the mode's **14.35 fps** readout
limit (12.1 measured at 14 requested), and runs the CMA pool to
**292 kB free** — the wall. 0 dropped frames in both.

Half-FOV at the binned mode (`s_half_1080p15`) is 1.67x upscaled —
the same trap as production, one mode up. Any preset must be checked,
not assumed.

## Finding 5 — 1080p30 crashed on teardown (rc=134)

`s_wide_1080p30` produced 570 good frames then aborted:
`double free or corruption (fasttop)`. Recording was fine; the crash
is in the exit path — but `record_one_clip` treats a nonzero encoder
rc as a failed clip and **drops the whole file**, so at 1080p30 every
clip would be lost. ONE observation, not yet reproduced; must be
re-tested before 30 fps is offered at 1080p. 15 fps (the overnight
run's recommended ship point) is unaffected.

## Finding 6 — bitrate was never the problem

At 0.3 bits/pixel/frame (Hero8 quality class), 1000x562@15 wants
2.5 Mbps; the shipped setting is 2.0 Mbps = **0.237 bits/px/frame**,
already near class. The footage is soft because only 533x299 real
pixels sit behind it. Fix the geometry first; then re-tune bitrate.

## Probe bug to fix

The `verdict` column flags `avail_w == out_w - 1` as UPSCALED
(libcamera rounds the applied crop down by a pixel: 1599 vs 1600).
`upscale_factor` is the trustworthy column. Fix: allow a 2 px
tolerance before calling a case upscaled.
