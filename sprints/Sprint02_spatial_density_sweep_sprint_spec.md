# Sprint Handoff Spec: Reference Card Spatial Density Sweep

**Project:** Underwater Reef Monitoring Cameras / Bristlemouth Camera MVP  
**Date:** 2026-07-06  
**Sprint type:** MVP field-shipment support  
**Urgency:** Shipping today; prioritize useful, repeatable, engineering-rigorous results over academic perfection.

---

## 0. Context and Intent

We are preparing an MVP reef-monitoring camera workflow for a paying customer. The field camera needs to transmit images over a constrained cellular/Bristlemouth/Sofar path. The transmitted images must remain good enough to automatically detect a printed reference card using AprilTags, then use that card later for color correction and coral-bleaching analysis.

This sprint is **not** a PhD-level camera-characterization experiment. It should be rigorous enough to make a confident shipping decision today:

> Select a practical ROI crop and spatial sampling density that preserves automatic reference-card detection before we move into HEIC compression testing.

We already have tools/scripts for:
- native full-resolution capture from the Pi using `libcamera-still` / `rpicam-still`
- reference-card AprilTag detection and quality scoring
- ROI/crop and resolution cut sheets
- HEIC/downsample screening

This sprint creates the next structured test pipeline using the full-native reference image as the gold JPEG baseline.

---

## 1. End Goal

Create a Mac-side test pipeline that takes a **native full-resolution JPEG** from the camera and produces:

1. A fixed 16:9 crop using the crop discovered from production `720p` behavior.
2. A secondary auto-card-centered crop for learning/comparison.
3. A downsample ladder of 16:9 images from each crop.
4. Reference-card quality metrics for each downsampled image.
5. Two visual cut sheets:
   - same-ROI normalized full-image comparison
   - rectified reference-card-only comparison with metrics
6. A CSV that identifies the transition from **PASS → WARN → FAIL** as spatial sampling density decreases.

The output should answer:

> What is the lowest 16:9 image size where the reference card is still reliably detectable using all four AprilTags?

This result will feed the next sprint: HEIC compression sweep.

---

## 2. Definitions

Use these terms consistently:

```text
Native full image:
  Full camera JPEG captured by libcamera/rpicam, expected 4608×2592 for IMX708.

ROI / ScalerCrop:
  The region of the native image used for analysis.

Fixed ROI:
  The production-720-like crop:
  [x=768, y=432, w=3072, h=1728]

Spatial sampling density:
  How many output pixels represent the selected ROI.
  In practical terms: pixels-on-card / pixels-on-AprilTag.

Output resolution:
  Final dimensions after downsampling the selected ROI.

Gold JPEG baseline:
  The native full JPEG is high-quality but still JPEG, not RAW.
  This is the MVP baseline, not a raw-sensor scientific reference.

HEIC quality:
  Compression quality setting to be tested later, after spatial-density threshold is known.
```

---

## 3. Existing Scripts to Reuse

The new sprint should reuse existing tools where possible.

### 3.1 Native Full Capture

```bash
capture_native_full_reference.sh
```

Purpose:
- Captures full-native JPEG from remote Pi using the camera-app path.
- Uses `rpicam-still` if available, otherwise `libcamera-still`.
- Temporarily disables cron and stops production camera jobs so the camera is available.
- Restores cron afterward.
- Downloads the result folder to Mac.

Typical command:

```bash
HOST=bmcam002 ./capture_native_full_reference.sh
```

Expected local output:

```text
~/Downloads/bm_native_reference_captures/<run_tag>/<remote_folder>/
  native_full_q95.jpg
  capture_summary.json
  native_full_q95.stderr.log
  native_full_q95.stdout.log
```

### 3.2 Reference Card Quality Analyzer

```bash
bm_reference_card_quality_v2.py
```

Purpose:
- Detects AprilTags.
- Estimates the reference-card crop.
- Rectifies the card.
- Computes tag/card clarity metrics.
- Produces CSV and cut sheet.

Install dependencies:

```bash
python3 -m pip install opencv-contrib-python pillow numpy
```

Typical command:

```bash
python3 bm_reference_card_quality_v2.py \
  --input <image_or_folder> \
  --output <output_folder> \
  --corner-map tl:0,tr:1,bl:2,br:3
```

This script should be used as a module/subprocess rather than rewriting AprilTag scoring logic from scratch unless necessary.

---

## 4. Reference Card AprilTag Layout

Use this corner map:

```text
top-left:     ID 0
top-right:    ID 1
bottom-left:  ID 2
bottom-right: ID 3
```

CLI form:

```bash
--corner-map tl:0,tr:1,bl:2,br:3
```

This is the expected/working map for the current Nereus Vision Reef Reference Card V1.

The printed card includes:
- four AprilTags
- grayscale row
- coral/color-correction patches
- 400 mm scale bar
- title: `Nereus Vision - Reef Reference Card V1`

---

## 5. Primary ROI: Fixed Production-720 Crop

The primary analysis path must use this fixed 16:9 crop from the native full image:

```text
x = 768
y = 432
w = 3072
h = 1728
```

This crop was discovered from production-path metadata for the `720p` key and avoids more of the wide-angle lens/barrel-distorted image edges.

Name this mode:

```text
fixed
```

Output image before downsampling:

```text
fixed_3072x1728.jpg
```

This image is the **reference benchmark** for the fixed-crop path.

---

## 6. Secondary ROI: Auto-Card-Centered Crop

Also implement a secondary learning/comparison path:

```text
auto
```

Goal:
- Detect the reference card in the native full image.
- Estimate the card center from all four AprilTags.
- Build a 16:9 crop of the same size as the fixed crop:
  `3072×1728`
- Center that crop on the detected card center where possible.
- Clamp to native image bounds.

This path is **not** the primary production recommendation. It is for understanding what an automated card-centered ROI might do.

Output image before downsampling:

```text
auto_3072x1728.jpg
```

If card detection fails on the native full image:
- skip the auto path
- record failure in `run_manifest.json`
- continue with fixed path

---

## 7. Downsample Ladder

For each crop mode (`fixed`, and `auto` if successful), create the following 16:9 downsampled images:

```text
3072×1728
2688×1512
2304×1296
1920×1080
1600×900
1280×720
1024×576
854×480
640×360
```

Use high-quality downsampling:

```text
Pillow Image.Resampling.LANCZOS
```

or equivalent high-quality area/Lanczos resampling.

Important:
- This is Mac-side processing from the full-native JPEG.
- Include `3072×1728` and `2688×1512` even though Pi/Picamera2 production could not directly capture them.
- This tests theoretical spatial sampling density before production implementation decisions.

Output examples:

```text
downsampled/fixed/fixed_3072x1728.jpg
downsampled/fixed/fixed_2688x1512.jpg
downsampled/fixed/fixed_2304x1296.jpg
...
downsampled/auto/auto_3072x1728.jpg
downsampled/auto/auto_2688x1512.jpg
...
```

---

## 8. Quality Criteria

Use stricter criteria than earlier tests.

### 8.1 PASS

```text
all 4 AprilTags detected
min tag side >= 18 px
```

### 8.2 WARN

```text
all 4 AprilTags detected
min tag side >= 10 px and < 18 px
```

### 8.3 FAIL

```text
fewer than 4 AprilTags detected
or min tag side < 10 px
```

Label these as:

```text
provisional engineering thresholds
```

They are good enough for MVP decision-making but should not be treated as final scientific validation thresholds.

---

## 9. Metrics to Preserve

The output CSV must include at minimum:

```text
crop_mode
output_width
output_height
source_roi_x
source_roi_y
source_roi_w
source_roi_h
image_path
image_size_bytes
image_size_kb

tag_count
tag_ids
tag_side_px_min
tag_side_px_mean
tag_laplacian_var_mean
tag_tenengrad_mean
tag_contrast_mean
fiducial_geometry_residual_px

card_laplacian_var
card_tenengrad
card_contrast_p95_p05
card_clipped_dark_frac
card_clipped_bright_frac

ref_psnr_rgb
ref_laplacian_corr
quality_status
```

Recommended derived fields:

```text
relative_output_scale_vs_3072
output_pixels_total
approx_pixels_per_card_width
approx_pixels_per_tag_side
```

If `bm_reference_card_quality_v2.py` does not currently produce all crop-mode fields, the wrapper script should join its output with manifest data.

---

## 10. Visual Outputs

Create two primary cut sheets.

### 10.1 Same-ROI Normalized Display Sheet

Purpose:
- Human visual inspection of the same ROI at different spatial sampling densities.

Requirements:
- Same ROI/framing for each tile.
- Display every downsampled image at the same visual size.
- Lower-resolution images are upsampled for display.
- Clearly state this in the title/subtitle.

Name:

```text
cut_sheets/test_same_roi_normalized_display_fixed.jpg
cut_sheets/test_same_roi_normalized_display_auto.jpg
```

Each tile should show:

```text
mode
output size
JPEG KB
quality_status
tag_count
min_tag_px
```

Do **not** call this a 1:1 sheet.

### 10.2 Rectified Reference Card Metrics Sheet

Purpose:
- Engineering view of how the card itself degrades with spatial sampling density.

Requirements:
- Use AprilTags to detect and rectify the card.
- Show normalized card crop for each resolution.
- Print metrics under each tile.

Name:

```text
cut_sheets/test_reference_card_metrics_fixed.jpg
cut_sheets/test_reference_card_metrics_auto.jpg
```

Each tile should show:

```text
mode
output size
status
tag_count
min_tag_px
tag_sharpness
card_sharpness
PSNR vs 3072×1728 reference
JPEG KB
```

If card detection fails:
- show the annotated full image or a placeholder
- include the failure reason

---

## 11. CSV Outputs

Create:

```text
results/reference_card_quality_fixed.csv
results/reference_card_quality_auto.csv
results/threshold_summary.csv
```

### 11.1 `reference_card_quality_fixed.csv`

All raw rows for fixed crop.

### 11.2 `reference_card_quality_auto.csv`

All raw rows for auto-card-centered crop if it exists.

### 11.3 `threshold_summary.csv`

A compact summary with one row per crop mode:

```text
crop_mode
highest_resolution
lowest_pass_resolution
first_warn_resolution
first_fail_resolution
recommended_min_resolution
notes
```

Recommendation logic for MVP:

```text
recommended_min_resolution = lowest PASS resolution before WARN/FAIL
```

If multiple PASS rows exist and file size matters:
- choose the lowest output size that still PASSes.
- If the lowest PASS is right at the threshold, recommend one step higher for margin.

---

## 12. Directory Structure

Each run should be self-contained and timestamped.

Recommended output folder:

```text
~/Downloads/bm_spatial_density_sweep/test_<YYYYMMDDTHHMMSSZ>/
```

Inside:

```text
run_manifest.json

source/
  native_full_q95.jpg

roi/
  fixed_3072x1728.jpg
  auto_3072x1728.jpg        # if successful

downsampled/
  fixed/
    fixed_3072x1728.jpg
    fixed_2688x1512.jpg
    fixed_2304x1296.jpg
    fixed_1920x1080.jpg
    fixed_1600x900.jpg
    fixed_1280x720.jpg
    fixed_1024x576.jpg
    fixed_854x480.jpg
    fixed_640x360.jpg
  auto/
    auto_3072x1728.jpg
    auto_2688x1512.jpg
    ...

quality/
  fixed/
    reference_card_quality_results.csv
    cut_sheets/reference_card_quality_sheet.jpg
    annotated/
    rectified_cards/
    json/
  auto/
    ...

cut_sheets/
  test_same_roi_normalized_display_fixed.jpg
  test_reference_card_metrics_fixed.jpg
  test_same_roi_normalized_display_auto.jpg
  test_reference_card_metrics_auto.jpg
  test_threshold_summary.jpg

results/
  reference_card_quality_fixed.csv
  reference_card_quality_auto.csv
  threshold_summary.csv
```

---

## 13. Proposed Script Name

Use:

```text
bm_reference_card_spatial_density_sweep.py
```

This is a Mac-side Python script.

Recommended CLI:

```bash
python3 bm_reference_card_spatial_density_sweep.py \
  --input ~/Downloads/bm_native_reference_captures/<run>/native_full_q95.jpg \
  --output ~/Downloads/bm_spatial_density_sweep/test_$(date -u +%Y%m%dT%H%M%SZ) \
  --fixed-crop 768,432,3072,1728 \
  --corner-map tl:0,tr:1,bl:2,br:3 \
  --include-auto-centered \
  --quality-script ./bm_reference_card_quality_v2.py
```

Recommended defaults:

```text
fixed_crop = 768,432,3072,1728
downsample_ladder = 3072x1728 2688x1512 2304x1296 1920x1080 1600x900 1280x720 1024x576 854x480 640x360
corner_map = tl:0,tr:1,bl:2,br:3
prefix = test
```

---

## 14. Implementation Notes

### 14.1 Do not mix in HEIC yet

This sprint is spatial sampling only.

No HEIC compression should be applied in this step.

HEIC testing comes next after the PASS/WARN/FAIL threshold is understood.

### 14.2 Use native full JPEG as baseline

The native full source is:

```text
4608×2592 JPEG
```

It is not RAW, but it is the best available MVP baseline for today.

### 14.3 Keep coordinate systems explicit

Every image should record:

```text
native image size
crop coordinates in native image coordinates
output image size
downsample method
```

### 14.4 Be careful with misleading 1:1 displays

A fixed-pixel 1:1 crop can make lower-resolution images appear to have a different ROI. Do not use that as the primary comparison.

For this sprint, use:

```text
same ROI normalized display
```

where the full cropped ROI is scaled to the same visual size in every tile.

### 14.5 Use all four AprilTags

Unlike earlier exploratory tests, this sprint requires all four tags.

The card layout is:

```text
0  1
2  3
```

No corner inference should be used for PASS/WARN status.

Corner inference may still be useful for debugging, but the final status should require all four.

---

## 15. Expected Output / Decision

At the end of the sprint, produce a short recommendation like:

```text
Fixed crop [768,432,3072,1728]:

3072×1728: PASS
2688×1512: PASS
2304×1296: PASS
1920×1080: PASS
1600×900: PASS or WARN
1280×720: WARN or FAIL
1024×576: FAIL
854×480: FAIL
640×360: FAIL

Recommended minimum pre-HEIC spatial sampling density:
  <resolution>

Next step:
  HEIC compression sweep around <resolution range>
```

The exact threshold will be determined by the generated metrics.

---

## 16. Next Sprint After This

Once this sprint is complete:

1. Select the viable resolution band.
2. Run HEIC compression sweep for that band.
3. Decode HEIC back to JPEG.
4. Run the same `bm_reference_card_quality_v2.py` analyzer.
5. Pick the lowest HEIC file size that still detects all four AprilTags and preserves card quality.
6. Update production capture/transmit code accordingly.

Likely production direction:
- use `libcamera-still` / `rpicam-still` path for still-image capture
- capture high quality source
- apply explicit crop/downsample/compression
- transmit only the optimized HEIC/JPEG payload
