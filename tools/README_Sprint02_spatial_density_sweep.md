# Sprint 02 Spatial Density Sweep README

**Project:** Underwater Reef Monitoring Cameras / Bristlemouth Camera MVP  
**Sprint:** Reference Card Spatial Density Sweep  
**Purpose:** Select the lowest practical 16:9 spatial sampling resolution where the Nereus Vision Reef Reference Card V1 is still reliably detected using all four AprilTags, before running the next HEIC compression sprint.

---

## 1. What this sprint tests

This sprint tests **spatial sampling density only**.

The wrapper starts from a high-quality native full JPEG, crops a 16:9 ROI, downsamples that ROI to a resolution ladder, and runs the existing `bm_reference_card_quality_v2.py` analyzer against each downsampled image.

Compression artifacts are intentionally minimized:

```text
JPEG quality = 95
JPEG subsampling = 0 / 4:4:4
Downsampling = Pillow Image.Resampling.LANCZOS
No HEIC in this sprint
```

HEIC/compression testing comes next, after the viable spatial resolution band is known.

---

## 2. Required files

Place these two files in the same working folder, or pass the analyzer path explicitly:

```text
bm_reference_card_spatial_density_sweep.py
bm_reference_card_quality_v2.py
```

The wrapper does not modify `bm_reference_card_quality_v2.py`. It uses that script as the metrics engine, then applies Sprint 02-specific stricter status logic.

---

## 3. Dependencies

Install dependencies on the Mac:

```bash
python3 -m pip install opencv-contrib-python pillow numpy
```

`opencv-contrib-python` is required because the analyzer uses OpenCV's ArUco/AprilTag detector.

Default detector scales used by the wrapper are:

```text
1 2 3 4
```

This is intentionally a bit more conservative on runtime/memory than the standalone analyzer default. For difficult low-resolution images, rerun with:

```bash
--scales 1 2 3 4 6 8
```

---

## 4. Expected input

The expected source image is the native full JPEG from the camera capture script:

```text
~/Downloads/bm_native_reference_captures/<run>/<remote_folder>/native_full_q95.jpg
```

Expected native size for IMX708 full native capture:

```text
4608×2592
```

If the image is not exactly `4608×2592`, the wrapper records a warning and continues as long as the fixed crop still fits inside the image.

---

## 5. Recommended command

From the folder containing both scripts:

```bash
python3 bm_reference_card_spatial_density_sweep.py \
  --input ~/Downloads/bm_native_reference_captures/<run>/<remote_folder>/native_full_q95.jpg \
  --output ~/Downloads/bm_spatial_density_sweep/test_$(date -u +%Y%m%dT%H%M%SZ) \
  --fixed-crop 768,432,3072,1728 \
  --corner-map tl:0,tr:1,bl:2,br:3 \
  --include-auto-centered \
  --quality-script ./bm_reference_card_quality_v2.py \
  --prefix test
```

If you want to label outputs by camera/run instead of `test`, change the prefix:

```bash
--prefix bmcam002
```

---

## 6. Default crop and downsample ladder

Primary fixed ROI:

```text
x = 768
y = 432
w = 3072
h = 1728
```

This creates:

```text
roi/fixed_3072x1728.jpg
```

If `--include-auto-centered` is provided and all four native tags are detected, the script also creates:

```text
roi/auto_3072x1728.jpg
```

The downsample ladder is:

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

Outputs are saved under:

```text
downsampled/fixed/
downsampled/auto/
```

---

## 7. Sprint 02 PASS / WARN / FAIL logic

The wrapper applies stricter Sprint 02 status logic than the analyzer's built-in status.

Required AprilTag layout:

```text
0  1
2  3
```

Required CLI map:

```text
--corner-map tl:0,tr:1,bl:2,br:3
```

Final status rules:

```text
PASS:
  all required tag IDs 0,1,2,3 detected
  tag_side_px_min >= 18 px

WARN:
  all required tag IDs 0,1,2,3 detected
  10 px <= tag_side_px_min < 18 px

FAIL:
  missing one or more required tag IDs
  or tag_side_px_min < 10 px
```

The analyzer's original status is preserved in the output as:

```text
analyzer_quality_status
```

The Sprint 02 status is written as:

```text
quality_status
```

---

## 8. Output structure

Each run is self-contained:

```text
<output>/
  run_manifest.json

  source/
    native_full_q95.jpg

  roi/
    fixed_3072x1728.jpg
    auto_3072x1728.jpg        # if auto-centered crop succeeds

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
      analyzer_stdout.log
      analyzer_stderr.log
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
    reference_card_quality_auto.csv     # only if auto path exists
    threshold_summary.csv
```

---

## 9. Primary outputs to inspect

Start with:

```text
results/threshold_summary.csv
cut_sheets/test_threshold_summary.jpg
```

Then inspect:

```text
cut_sheets/test_same_roi_normalized_display_fixed.jpg
cut_sheets/test_reference_card_metrics_fixed.jpg
results/reference_card_quality_fixed.csv
```

The fixed crop is the primary production-relevant result. The auto crop is for learning/comparison only.

---

## 10. How to read `threshold_summary.csv`

Columns:

```text
crop_mode
highest_resolution
lowest_pass_resolution
first_warn_resolution
first_fail_resolution
recommended_min_resolution
notes
status_sequence_high_to_low
```

Recommendation logic:

```text
recommended_min_resolution = lowest PASS resolution before WARN/FAIL
```

With the Sprint 02 margin rule:

```text
If the lowest PASS is immediately followed by WARN or FAIL at the next smaller size,
recommend one step higher for margin.
```

Example:

```text
3072×1728: PASS
2688×1512: PASS
2304×1296: PASS
1920×1080: PASS
1600×900: PASS
1280×720: WARN
1024×576: FAIL
```

In this case, the lowest PASS is `1600×900`, but it is immediately followed by `1280×720 WARN`, so the recommended minimum would be one step higher:

```text
1920×1080
```

---

## 11. Non-monotonic detection notes

Expected degradation is monotonic: as resolution decreases, results should move from PASS to WARN to FAIL.

A non-monotonic result means the detector gets worse, then better again at a lower resolution. Example:

```text
2304×1296: WARN
1920×1080: PASS
```

This can happen because AprilTag detection depends on resampling, edge placement, aliasing, contrast, and thresholding. If this happens, the wrapper flags it in `notes` and recommends conservatively rather than trusting a one-off lower-resolution PASS.

---

## 12. CSV fields added by the wrapper

The wrapper preserves analyzer metrics and adds run/ROI/status fields including:

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
relative_output_scale_vs_3072
output_pixels_total
approx_pixels_per_card_width
approx_pixels_per_tag_side
ref_roi_psnr_rgb
ref_roi_laplacian_corr
analyzer_quality_status
quality_status
status_reason
```

The analyzer's rectified-card comparison fields are preserved:

```text
ref_psnr_rgb
ref_laplacian_corr
```

The wrapper's full-ROI comparison fields are separate:

```text
ref_roi_psnr_rgb
ref_roi_laplacian_corr
```

---

## 13. Troubleshooting

### Analyzer fails with OpenCV/Aruco error

Install the contrib build:

```bash
python3 -m pip install --upgrade opencv-contrib-python pillow numpy
```

### Auto crop is skipped

Check `run_manifest.json` under `auto_crop`. The most likely reason is that all four required tags were not detected in the native full image.

The fixed crop path still runs and is the primary result.

### Input-size warning appears

This is okay if the fixed crop fits. The warning is preserved in `run_manifest.json`.

### The cut sheet says normalized display

This is intentional. The full ROI is shown at the same visual size for each resolution so framing can be compared fairly. It is not a 1:1 pixel sheet.

---

## 14. Next sprint

After selecting the viable resolution band:

1. Run HEIC compression sweep around that band.
2. Decode HEIC back to JPEG or analysis-compatible image format.
3. Run the same reference-card analyzer.
4. Pick the smallest transmitted file that still detects all four AprilTags and preserves card quality.
5. Update the production capture/transmit pipeline.
