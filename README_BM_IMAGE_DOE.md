# BM Image Quality DOE Tools

This patch adds two repo-tracked tools for benchmarking Bristlemouth camera image resolution, source format, HEIC quality, file size, and estimated BM buffer count.

## Files

```text
tests/bm_image_quality_doe_capture.py
tools/make_bm_image_doe_contact_sheet.py
```

## Key behavior

The capture script runs on the Raspberry Pi camera and does **not** transmit over Bristlemouth.

For each resolution, it captures one camera-processed RGB frame and saves two source references from the exact same frame:

```text
src-jpeg = controlled JPEG source, default quality 95
src-png  = lossless PNG source
```

It then compresses each source to HEIC at the requested quality values and records:

```text
source mode
source size
HEIC quality
HEIC size
base64 chars
estimated BM buffers at 300 bytes/buffer
Picamera2/libcamera metadata
```

## Recommended smoke test

```bash
ssh pi@bmcam001 'cd /home/pi/BM_Devel_Pi && /usr/bin/python3 ./doe_capture_quality_sweep.py --tag smoke_v2 --resolutions 480p --source-modes jpeg png --qualities 10 75'
```

## Recommended full DOE

```bash
ssh pi@bmcam001 'cd /home/pi/BM_Devel_Pi && /usr/bin/python3 ./doe_capture_quality_sweep.py --tag full_v2 --resolutions 480p 720p 420sq 720sq --source-modes jpeg png --qualities 10 25 40 50 65 75'
```

## Contact sheets on Mac

```bash
python3 tools/make_bm_image_doe_contact_sheet.py \
  --results-csv "$LOCAL_DIR/results.csv" \
  --export-jpeg-roundtrip \
  --jpeg-quality 95 \
  --make-source-quality-matrix \
  --matrix-qualities 10 75
```

Outputs:

```text
contact_heic_decoded.jpg
contact_jpeg_roundtrip_q095.jpg
contact_source_vs_quality_matrix_q010_q075.jpg
jpeg_roundtrip_q095/
results_with_jpeg_roundtrip_q095.csv
```
