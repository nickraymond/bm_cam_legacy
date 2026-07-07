# BM Camera Image Quality / ROI Test Tools

This folder contains helper scripts for testing Bristlemouth/Raspberry Pi camera image capture, ROI behavior, spatial sampling density, and reference-card detectability.

These scripts are intended for Mac-side development. Most of them SSH into a remote Pi camera, run a capture or probe, download the results, and generate local cut sheets.

---

## 1. Native Full-Resolution Reference Capture

Script:

```bash
capture_native_full_reference.sh
```

Purpose:

Capture a full native-resolution JPEG from the remote Pi using the efficient Raspberry Pi camera-app path:

```bash
libcamera-still -n -t 500 --quality 95 -o native_full_q95.jpg
```

or `rpicam-still` if available.

This is useful for creating a high-quality baseline image before doing Mac-side crop, downsample, HEIC compression, and reference-card analysis.

### Basic usage

```bash
cd tools

chmod +x capture_native_full_reference.sh

HOST=bmcam002 ./capture_native_full_reference.sh
```

Optional:

```bash
HOST=bmcam002 QUALITY=95 TIMEOUT_MS=500 ./capture_native_full_reference.sh
```

Output is downloaded to:

```bash
~/Downloads/bm_native_reference_captures/<run_tag>/<remote_folder>/
```

Expected files:

```text
native_full_q95.jpg
native_full_q95.stdout.log
native_full_q95.stderr.log
capture_summary.json
optional native_full_q95.metadata.json
```

### What the script does

1. SSHes into the Pi.
2. Backs up and temporarily disables the user crontab.
3. Stops any active camera-owning processes such as `main_pi_camera.py --transmit`.
4. Runs native full-resolution capture with `rpicam-still` or `libcamera-still`.
5. Saves logs and summary metadata.
6. Restores the crontab.
7. Downloads the result folder to the Mac.

### CMA / memory note

Full native IMX708 capture may fail if the Pi has only 64 MB CMA memory.

Check:

```bash
ssh pi@bmcam002 'grep -E "MemAvailable|CmaTotal|CmaFree" /proc/meminfo'
```

If `CmaTotal` is only `65536 kB` and full native capture fails with:

```text
Unable to request buffers: Cannot allocate memory
```

set CMA to 128 MB:

```bash
ssh pi@bmcam002 'bash -s' <<'REMOTE'
set -euo pipefail

if [ -f /boot/firmware/cmdline.txt ]; then
  CMDLINE=/boot/firmware/cmdline.txt
else
  CMDLINE=/boot/cmdline.txt
fi

sudo cp "$CMDLINE" "${CMDLINE}.bak.$(date -u +%Y%m%dT%H%M%SZ)"

if grep -qw "cma=[0-9][0-9]*[MG]" "$CMDLINE"; then
  sudo sed -i -E "s/\bcma=[0-9]+[MG]\b/cma=128M/" "$CMDLINE"
else
  sudo sed -i "s/$/ cma=128M/" "$CMDLINE"
fi

sudo reboot
REMOTE
```

After reboot:

```bash
ssh pi@bmcam002 'grep -E "MemAvailable|CmaTotal|CmaFree" /proc/meminfo'
```

Expected:

```text
CmaTotal: 131072 kB
```

---

## 2. Production ROI Probe

Script:

```bash
run_bm_production_roi_probe_v4.sh
```

Purpose:

Use the actual production capture pathway:

```python
process_image_v2.capture_image(resolution_key=key, directory_path=...)
```

This confirms what the production code does for each resolution key, including actual output size, JPEG size, and `ScalerCrop` metadata.

### Basic usage

```bash
chmod +x run_bm_production_roi_probe_v4.sh

HOST=bmcam002 \
KEYS="1296p 1080p 720p 480p 360p XGA SVGA VGA" \
./run_bm_production_roi_probe_v4.sh
```

Outputs:

```text
summary.csv
cut_sheets/production_roi_contact_sheet.jpg
cut_sheets/production_roi_scalercrop_overlay.jpg
cut_sheets/production_roi_metadata_table.jpg
```

Important finding from prior testing:

```text
1296p / 1080p:
  ScalerCrop = [0, 0, 4608, 2592]

720p / 480p / 360p:
  ScalerCrop ≈ [768, 432, 3072, 1728]

XGA / SVGA / VGA:
  ScalerCrop = [1152, 432, 2304, 1728]
```

So production `720p` is not just full-frame downsampled. It uses a tighter centered 16:9 crop.

---

## 3. Controlled Crop ROI Probe

Script:

```bash
run_bm_controlled_crop_roi_probe.sh
```

Purpose:

Hold output resolution constant and vary `ScalerCrop` to visualize possible 16:9 ROIs.

Default output:

```text
2304×1296
```

Default crop ladder:

```text
full_16x9       [0, 0, 4608, 2592]
crop_75         [576, 324, 3456, 1944]
crop_67_prod720 [768, 432, 3072, 1728]
crop_58         [960, 540, 2688, 1512]
crop_50         [1152, 648, 2304, 1296]
```

### Basic usage

```bash
chmod +x run_bm_controlled_crop_roi_probe.sh

HOST=bmcam002 ./run_bm_controlled_crop_roi_probe.sh
```

Outputs:

```text
summary.csv
cut_sheets/controlled_crop_contact_sheet.jpg
cut_sheets/controlled_crop_scalercrop_overlay.jpg
cut_sheets/controlled_crop_metadata_table.jpg
```

Use this to pick the crop that keeps the target/reference card in frame while avoiding distorted image edges.

---

## 4. Fixed Crop Resolution Sweep

Script:

```bash
run_bm_fixed_crop_resolution_sweep.sh
```

Purpose:

Hold one fixed `ScalerCrop` and vary output resolution. This tests spatial sampling density / pixels-on-target.

Default crop:

```text
ScalerCrop = [768, 432, 3072, 1728]
```

This is the crop production selects for `720p`.

### Recommended Pi-safe run

```bash
chmod +x run_bm_fixed_crop_resolution_sweep.sh

HOST=bmcam002 \
OUTPUT_SPECS="2304x1296 1920x1080 1600x900 1280x720 1024x576 854x480 640x360" \
./run_bm_fixed_crop_resolution_sweep.sh
```

Prior testing showed these output sizes work reliably. Larger requests such as `2688x1512` and `3072x1728` may fail with Pi Zero 2W memory limits in the Picamera2 path.

Outputs:

```text
summary.csv
cut_sheets/fixed_crop_resolution_contact_sheet_fit.jpg
cut_sheets/fixed_crop_resolution_center_1to1_sheet.jpg
cut_sheets/fixed_crop_resolution_metadata_table.jpg
```

Notes:

- The fit overview sheet keeps the same ROI visually aligned.
- The 1:1 sheet shows fixed-size output-pixel crops, so lower-resolution images appear to cover more scene area. This is useful for pixel inspection, but not for same-scene normalized comparison.

---

## 5. Reference Card Quality Analyzer

Script:

```bash
bm_reference_card_quality_v2.py
```

Purpose:

Detect the reference card using AprilTags, quantify tag/card quality, rectify the card crop, compare against a reference, and generate a quality cut sheet.

Install dependencies:

```bash
python3 -m pip install opencv-contrib-python pillow numpy
```

Basic usage:

```bash
python3 bm_reference_card_quality_v2.py \
  --input "$HOME/Downloads/bm_fixed_crop_resolution_sweep/<run_folder>" \
  --output "$HOME/Downloads/bm_card_quality_results"
```

Outputs:

```text
reference_card_quality_results.csv
cut_sheets/reference_card_quality_sheet.jpg
annotated/
rectified_cards/
json/
reference_card_rectified.jpg
```

Default AprilTag corner mapping:

```text
top-left:     inferred
top-right:    AprilTag ID 1
bottom-left:  AprilTag ID 2
bottom-right: AprilTag ID 3
```

Override if needed:

```bash
python3 bm_reference_card_quality_v2.py \
  --input <image_or_folder> \
  --output <output_folder> \
  --corner-map tl:0,tr:1,bl:2,br:3
```

Key metrics:

```text
tag_count
tag_side_px_min
tag_side_px_mean
tag_laplacian_var_mean
tag_tenengrad_mean
tag_contrast_mean
card_laplacian_var
card_tenengrad
ref_psnr_rgb
ref_laplacian_corr
quality_status
```

Suggested interpretation:

```text
PASS:
  >= 3 tags detected
  min tag side >= 18 px

WARN:
  >= 3 tags detected
  min tag side 10–18 px

FAIL:
  fewer than 3 tags detected
  or min tag side < 10 px
```

This script is the main tool for finding the lowest image size that still supports automatic card detection and color-correction workflow.

---

## 6. HEIC / Downsample Screening

Script:

```bash
bm_mac_heic_downsample_screen_oneshot.py
```

Purpose:

Capture/download or process a source image, crop/downsample on the Mac, run HEIC quality sweeps, decode back to JPEG, and generate a contact sheet and CSV.

Typical use after a native baseline:

```bash
python3 bm_mac_heic_downsample_screen_oneshot.py \
  --input ~/Downloads/native_full_q95.jpg \
  --output ~/Downloads/bm_heic_screen_v1 \
  --sizes 2304 1920 1600 1280 1024 854 640 \
  --qualities 5 10 15 20 25 35 45
```

Use this after selecting:

```text
1. fixed crop
2. spatial sampling density / output resolution
3. card-quality threshold
```

Then run `bm_reference_card_quality_v2.py` on the decoded JPEG outputs to quantify HEIC degradation.

---

## Recommended workflow

### Step 1 — Capture gold baseline

```bash
HOST=bmcam002 ./capture_native_full_reference.sh
```

### Step 2 — Crop/downsample on Mac

Use the native full image as the clean source and crop to the selected ROI, usually:

```text
production-720 crop = [768, 432, 3072, 1728]
```

### Step 3 — Run spatial sampling sweep

```bash
HOST=bmcam002 \
OUTPUT_SPECS="2304x1296 1920x1080 1600x900 1280x720 1024x576 854x480 640x360" \
./run_bm_fixed_crop_resolution_sweep.sh
```

### Step 4 — Quantify reference-card quality

```bash
python3 bm_reference_card_quality_v2.py \
  --input <resolution_sweep_folder> \
  --output ~/Downloads/bm_card_quality_resolution_sweep
```

### Step 5 — Pick PASS/WARN boundary

Choose the lowest output size where the reference card still has acceptable:

```text
tag detectability
min tag side pixels
tag sharpness
rectified card sharpness
reference similarity
```

### Step 6 — Run HEIC compression DOE

Sweep HEIC quality around the viable resolution band.

### Step 7 — Re-run card quality analyzer

Use the same card-quality metrics to find the lowest acceptable HEIC size for transmission.

---

## Terminology

Use these terms consistently:

```text
ROI / ScalerCrop:
  what part of the sensor is used

Output resolution:
  final saved image dimensions

Spatial sampling density:
  how many output pixels represent the selected ROI

Pixels-on-target / pixels-on-card:
  practical metric for whether the reference card is usable

HEIC quality:
  compression setting that affects final transmitted file size and artifacts
```
