# P0 rpicam limits probe — bmcam004, 2026-10-02 05:47Z (LADDER.md PART 1)

**P0: DONE.** 77 probes, every output file present. Run by the Test Engineer with
`hil/tools/hil_p0_probe.sh`, while the unit's runtime was stopped and cron disarmed (takeover,
`runs/r1_takeover_20261001/gate.log`). Restored after: cron re-armed, rebooted.

Unit: bmcam004, IMX708 wide, rpicam-apps v1.12.0 (12-05-2026), libcamera v0.7.1+rpt20260609,
kernel 6.18.34. CMA 256 MB (CmaFree 74 MB idle before the probe). Files: `00_env.txt`,
`01_help_*.txt`, `02_picamera2_controls.json`, `probes.csv`, `probe_logs/`, `still_meta/`, `SUMMARY.md`.

## Ranges (source: Picamera2 `camera_controls` [min, max, default], `02_picamera2_controls.json`)

| key | rpicam option | range / values | default | source |
|---|---|---|---|---|
| sharpness | `--sharpness` | 0.0 – 16.0 | 1.0 | Picamera2 `Sharpness` |
| contrast | `--contrast` | 0.0 – 32.0 | 1.0 | Picamera2 `Contrast` |
| saturation | `--saturation` | 0.0 – 32.0 | 1.0 | Picamera2 `Saturation` |
| brightness | `--brightness` | −1.0 – 1.0 | 0.0 | Picamera2 `Brightness` |
| denoise | `--denoise` | `auto off cdn_off cdn_fast cdn_hq` | (auto) | each ran (exit 0) on rpicam-vid AND rpicam-still; `bogus` → exit 255 "Invalid denoise mode" |
| hdr | `--hdr` | `off auto sensor single-exp` | off | each ran on rpicam-still, and on rpicam-vid at BOTH `--mode 2304:1296:10:P` and `4608:2592:10:P` (output 1000x562); `bogus` → exit 255 "Invalid HDR option" |

## Findings that matter for the build

1. **Out-of-range floats are NOT refused by rpicam**: sharpness −1.61 / 17.61, contrast and
   saturation −3.21 / 35.21, brightness −1.21 / 1.21 all exit 0 with normal output on both apps.
   libcamera presumably clamps silently (the still metadata does not report these controls, so the
   clamp is not proven from the files). ⇒ the range check must live in the backend + the unit's
   `check_value`; rpicam is not a safety net.
2. **Duplicate options fail at run time**: `--denoise cdn_off --denoise cdn_fast` and
   `--sharpness 1.0 --sharpness 2.0` → exit 255 "option '--…' cannot be specified more than once",
   0 bytes. This is the IP6 trigger (video retry without controls) if the argv builder can ever emit
   a duplicate (e.g. `video.record.encoder.denoise` AND `camera.image_processing.denoise`).
3. HDR: no mode × value combination failed at run time here (2 s clips; frame rate / quality under HDR
   not measured). Whether HDR is wanted on video is a product question, not a limit.
4. Picamera2 also lists `NoiseReductionMode` [0, 4] and `HdrMode` [0, 4] (integer enums); the rpicam
   string names above are what the runtime passes.

Not measured: whether a clamped value equals the boundary (no per-frame metadata for these controls);
visual effect per value; HDR timing.
