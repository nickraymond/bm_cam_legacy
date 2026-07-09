# Sprint 05 — Local Reference Card Color Correction Smoke Test

**Status:** short sprint spec / handoff draft  
**Scope:** local-only color-correction validation, not backend integration  
**Priority:** high, but must stay small  
**Target repo:** `bm_cam_legacy`  
**Primary output:** side-by-side cut sheet showing before/after correction and reference-card comparison

---

## 1. Sprint goal

Build a small local tool that can take a folder of BM camera images, detect/crop the reef reference card, compare the detected card against a high-resolution reference card template, run a first-pass color correction, and generate a visual cut sheet for QA.

This sprint is **not** meant to build the final scientific color-correction backend. It is a smoke test to answer:

```text
Can we use the deployed reference card to correct BM camera images after transmission?
Are the reference card patches visible and measurable?
Does a simple correction visibly improve image consistency?
Are the transmitted images good enough for later backend color-correction work?
```

The sprint should be fast and limited. Do not block testing the other cameras.

---

## 2. What the new agent should receive

The user will upload or provide:

```text
1. Current repo snapshot or relevant /tools folder.
2. One or more BM camera images from recent Q20 tests.
3. The high-resolution reference card image/PDF/PNG/SVG used for print.
4. Any existing reference-card detection scripts.
5. Any existing AprilTag / quality-analysis scripts.
6. Example metadata sidecar files if available.
```

Expected example input folder:

```text
input_images/
  2026-07-09T17:45:41Z_image.jpg
  2026-07-09T17:45:41Z_image_compressed.heic
  2026-07-09T17:45:41Z_image.jpg.capture_metadata.json
  reference_card_high_res.png
```

If HEIC support is not already available locally, the tool may operate on JPEG derivatives first.

---

## 3. Non-goals

Do **not** build backend integration in this sprint.

Do **not** modify live Pi capture code.

Do **not** modify BM serial transmission.

Do **not** design a final color science pipeline.

Do **not** require perfect underwater color correction.

Do **not** block shipment on this tool unless it proves that the reference-card images are unusable.

---

## 4. Proposed tool name

Preferred new script:

```text
tools/bm_reference_card_color_smoke.py
```

Optional helper module if needed:

```text
tools/reference_card_color_utils.py
```

The script should be runnable as:

```bash
python tools/bm_reference_card_color_smoke.py \
  --input-dir ./input_images \
  --output-dir ./color_smoke_output \
  --reference-card ./input_images/reference_card_high_res.png
```

Optional flags:

```bash
--image-glob "*_image.jpg"
--heic-glob "*_image_compressed.heic"
--template-json ./reference_card_template_v1/template_layout.json
--max-images 20
--make-pdf true
--make-contact-sheet true
```

---

## 5. MVP output

For each input image, write an output folder:

```text
color_smoke_output/
  summary.csv
  summary.json
  2026-07-09T17-45-41Z/
    before.jpg
    after_color_corrected.jpg
    detected_card_overlay.jpg
    detected_card_warp.jpg
    reference_card_template_resized.jpg
    reference_card_comparison.jpg
    patch_samples.json
    color_correction_matrix.json
    metrics.json
    cutsheet.png
```

Optional PDF output:

```text
color_smoke_output/
  cutsheets/
    2026-07-09T17-45-41Z_cutsheet.pdf
  color_correction_contact_sheet.pdf
```

---

## 6. Required cut sheet layout

The cut sheet is the main deliverable.

Each page should show:

### Top section — Before correction

```text
Original image / backend-rendered image
Title: BEFORE
Timestamp / filename
```

### Middle section — Reference-card QA

Show these side by side:

```text
Detected card crop/warp from the image
High-resolution reference card template resized to the same canonical frame
Difference/overlay or patch comparison preview
```

Include small metrics:

```text
card_detected: yes/no
tag_count
homography_ok: yes/no
warp_width_px
warp_height_px
sharpness_score
mean_luma
clip_percent_low
clip_percent_high
gray_neutrality_before
patch_count_used
```

### Bottom section — After correction

```text
Color-corrected image
Title: AFTER COLOR CORRECTION
```

Include color metrics:

```text
gray_neutrality_after
mean_patch_error_before
mean_patch_error_after
correction_method
correction_matrix
```

The cut sheet should be visually useful, even if metrics are approximate.

---

## 7. Reference-card detection approach

Use existing project code if available.

Preferred method:

```text
1. Detect AprilTags.
2. Use known tag IDs/corners to compute homography.
3. Warp full reference card into canonical coordinates.
4. Save the warped card crop.
```

If full AprilTag detection is not available in the local environment, implement a fallback mode:

```text
--manual-card-corners path/to/corners.json
```

Example fallback JSON:

```json
{
  "image": "2026-07-09T17:45:41Z_image.jpg",
  "corners": {
    "top_left": [100, 200],
    "top_right": [900, 210],
    "bottom_right": [910, 700],
    "bottom_left": [95, 690]
  }
}
```

Fallback mode is acceptable for this sprint because the goal is to smoke-test the color-correction concept, not rebuild the detector.

---

## 8. Template / patch layout

The tool needs a canonical reference-card coordinate system.

If a prior `template_layout.json` does not exist, create a simple provisional one:

```json
{
  "template_name": "nereus_reef_reference_card_v1",
  "template_width_px": 2000,
  "template_height_px": 1200,
  "patches": [
    {
      "id": "gray_01",
      "type": "gray",
      "label": "neutral_gray_light",
      "x": 100,
      "y": 100,
      "w": 80,
      "h": 80,
      "target_srgb": [200, 200, 200]
    },
    {
      "id": "gray_02",
      "type": "gray",
      "label": "neutral_gray_mid",
      "x": 200,
      "y": 100,
      "w": 80,
      "h": 80,
      "target_srgb": [128, 128, 128]
    },
    {
      "id": "gray_03",
      "type": "gray",
      "label": "neutral_gray_dark",
      "x": 300,
      "y": 100,
      "w": 80,
      "h": 80,
      "target_srgb": [64, 64, 64]
    }
  ]
}
```

The first sprint can use rough patch coordinates if exact measured coordinates are not available, but all assumptions must be written in `metrics.json`.

---

## 9. Color-correction method

Start simple and transparent.

### Step A — sample patches

For each patch:

```text
crop interior region
ignore border pixels
compute median RGB
compute mean RGB
compute low/high clipping percentage
```

Use median RGB for correction.

### Step B — gray-balance correction

Compute per-channel gain from neutral gray patches:

```text
target_gray = mean target luminance of selected gray patches
observed_rgb = median observed RGB across selected gray patches
gain_r = target_gray / observed_r
gain_g = target_gray / observed_g
gain_b = target_gray / observed_b
```

Apply gains to the image.

### Step C — optional 3x3 color matrix

If there are enough non-gray color patches with known targets:

```text
solve least squares 3x3 matrix:
observed_rgb @ M ≈ target_rgb
```

Apply matrix after gray balance or instead of gray balance.

For MVP, implement both if fast, but default to the simplest robust method:

```text
method = gray_balance
```

If 3x3 matrix is implemented, save:

```text
color_correction_matrix.json
```

and include matrix coefficients in the cut sheet.

---

## 10. Metrics

At minimum, write the following per image:

```json
{
  "image": "2026-07-09T17:45:41Z_image.jpg",
  "card_detected": true,
  "tag_count": 4,
  "homography_ok": true,
  "patch_count_total": 12,
  "patch_count_used": 9,
  "sharpness_score": 1234.5,
  "mean_luma_before": 118.2,
  "mean_luma_after": 132.7,
  "clip_percent_low_before": 0.1,
  "clip_percent_high_before": 0.0,
  "clip_percent_low_after": 0.0,
  "clip_percent_high_after": 0.2,
  "gray_neutrality_before": 0.21,
  "gray_neutrality_after": 0.04,
  "mean_patch_error_before": 31.2,
  "mean_patch_error_after": 12.8,
  "correction_method": "gray_balance",
  "notes": []
}
```

Metric definitions:

```text
sharpness_score
  Variance of Laplacian or equivalent focus/sharpness proxy.

gray_neutrality
  Mean channel imbalance on gray patches.
  Example: average absolute difference between normalized R/G/B channels.

mean_patch_error
  Simple RGB distance between sampled patch and target patch.
  Does not need to be formal Delta E for MVP.

clip_percent_low/high
  Percent of pixels near 0 or 255.
```

---

## 11. Summary CSV

Write:

```text
summary.csv
```

Columns:

```text
image
timestamp_utc
card_detected
tag_count
homography_ok
patch_count_used
sharpness_score
mean_luma_before
mean_luma_after
clip_percent_low_before
clip_percent_high_before
clip_percent_low_after
clip_percent_high_after
gray_neutrality_before
gray_neutrality_after
mean_patch_error_before
mean_patch_error_after
correction_method
cutsheet_path
corrected_image_path
notes
```

---

## 12. Dependencies

Prefer minimal dependencies already likely available:

```text
python
opencv-python
numpy
pillow
pillow-heif, if reading HEIC directly
matplotlib, for cut sheet/contact sheet
```

Avoid heavy or hard-to-install libraries unless the repo already uses them.

If AprilTag detection dependency is missing, support manual-corner fallback or use an existing project detector.

---

## 13. Acceptance criteria

Sprint is complete when:

```text
1. Script runs on a local folder of 1–5 BM images.
2. At least one image produces a before/after cut sheet.
3. Detected reference card crop is saved.
4. High-res reference card comparison image is saved.
5. Basic gray-balance correction is applied and saved.
6. JSON metrics are written per image.
7. summary.csv is written.
8. Failures are explicit and non-crashing.
9. README or script usage comments explain how to run it.
```

Stretch but useful:

```text
1. Batch PDF contact sheet.
2. 3x3 color correction matrix.
3. automatic AprilTag homography using existing project code.
4. visual patch grid overlay with sampled patch boxes.
```

---

## 14. Failure behavior

The tool should never silently skip an image.

If detection fails:

```text
write metrics.json
card_detected = false
save original image
save failure overlay if possible
write note in summary.csv
continue to next image
```

If color correction fails:

```text
save detected card crop
save before image
write correction_method = none
write failure reason
continue to next image
```

---

## 15. Suggested implementation order

1. Inspect existing `/tools` folder and reuse any reference-card detection code.
2. Implement image loading for JPG/PNG first.
3. Add HEIC loading if quick.
4. Implement output folder structure.
5. Implement reference-card crop/warp.
6. Implement high-res template resize/comparison image.
7. Implement simple patch sampling.
8. Implement gray-balance correction.
9. Generate before/after cut sheet PNG.
10. Write summary CSV/JSON.
11. Add optional PDF generation only if time remains.

---

## 16. Development rules

Follow existing project agent rules:

```text
- Keep code modular and well documented.
- Prefer simple effective solutions.
- Avoid elaborate abstractions.
- Do not rewrite existing working camera code.
- Reuse existing detection/quality tools where possible.
- Save before/after artifacts for easy human review.
- Make failures visible in output files and summary CSV.
- Do not block camera shipment unless the input data is clearly unusable.
```

---

## 17. Handoff note for the new agent

This sprint is a local smoke test. The product decision it supports is:

```text
Can Nereus ship cameras now and perform color correction after images arrive?
```

A good outcome is not perfect color science. A good outcome is a clear cut sheet showing:

```text
Before image
Detected reference card from image
High-resolution reference card template comparison
After image
Basic metrics showing whether correction helped
```

If that works on one or two images, pause and return results for review.
