# bmcam000 libcamera crop/HEIC upload test patch

## Purpose

Development-only patch for bmcam000 to test the new production-like image path:

```text
rpicam-still/libcamera-still native full JPEG
  -> native-coordinate fixed crop
  -> optional LANCZOS spatial downsample
  -> unchanged HEIC compression
  -> unchanged 300-byte message chunk/transmit path
```

## First test setting

```text
source:       4608x2592 JPEG q95
crop:         x=768, y=432, w=3072, h=1728
output:       3072x1728
HEIC quality: 20
BM network:   0x02 cellular-only
chunk size:   300 bytes
```

## Files changed

```text
main_pi_camera.py
process_image_v2.py
spotter_time_sync.py
camera_schedule.yaml
```

`run_capture_cycle.sh`, `bm_serial.py`, `split_image_heic()`, `send_buffers()`, and message chunking behavior were intentionally not changed.

## Regression-size check

All edited files became larger, as expected for additive code/config changes.

```text
main_pi_camera.py      12,566 -> 20,064 bytes    +7,498
process_image_v2.py    30,740 -> 40,483 bytes    +9,743
spotter_time_sync.py   19,729 -> 25,036 bytes    +5,307
camera_schedule.yaml      930 ->  2,086 bytes    +1,156
run_capture_cycle.sh    1,804 ->  1,804 bytes        +0 unchanged
```

Line counts:

```text
main_pi_camera.py        373 ->  567 lines
process_image_v2.py      912 -> 1139 lines
spotter_time_sync.py     580 ->  686 lines
camera_schedule.yaml      32 ->   80 lines
run_capture_cycle.sh      59 ->   59 lines unchanged
```

## Smoke checks run before handoff

```bash
python3 -m py_compile main_pi_camera.py process_image_v2.py spotter_time_sync.py
```

Also smoke-tested the YAML parser with a stubbed serial module:

```text
image_pipeline.enabled=True
capture_backend=rpicam
crop_x=768
output_width=3072
heic_quality=20
validate_schedule() ok
```

## Known limitations

- This patch does not tune camera exposure, white balance, focus, denoise, or other camera controls yet.
- rpicam/libcamera metadata is not fully recovered yet. The sidecar records command/crop/output pipeline metadata and logs.
- Pi-side HEIC Q20 may differ from Mac-side HEIC Q20; validate actual output sizes and image quality on bmcam000.
- Real buffer count is the production count after base64 chunking, not the Mac-side raw HEIC-byte estimate.
