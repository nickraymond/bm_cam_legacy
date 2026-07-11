# Sprint 05 — Sample image AprilTag crop + color correction

This drop contains a local script that:

1. detects the four AprilTags on the Nereus reef reference card,
2. estimates the card homography,
3. crops/warps the card out of the background,
4. samples the grayscale and color patches,
5. applies a first-pass color correction, and
6. writes a cut sheet plus CSV/JSON metrics.

## Install

```bash
python3 -m pip install opencv-contrib-python pillow numpy
# Optional, only if you want direct HEIC input support later:
python3 -m pip install pillow-heif
```

## Run on a folder of BM images

```bash
unzip sprint05_color_smoke_sample_drop.zip -d sprint05_color_smoke_sample_drop
cd sprint05_color_smoke_sample_drop

mkdir -p "$HOME/Downloads/bm_color_smoke_input"
# Put BM images in that folder, then run:
./run_sample_image.sh "$HOME/Downloads/bm_color_smoke_input" "$HOME/Downloads/bm_color_smoke_test_$(date -u +%Y%m%dT%H%M%SZ)"
```

Or call the Python script directly:

```bash
python3 tools/bm_reference_card_color_smoke.py \
  --input-dir "$HOME/Downloads/bm_color_smoke_input" \
  --output-dir "$HOME/Downloads/bm_color_smoke_test_$(date -u +%Y%m%dT%H%M%SZ)" \
  --reference-card ./reference_card_template_v1/reference_card_template_2000x840.png \
  --template-json ./reference_card_template_v1/template_layout.json \
  --quality-script ./tools/bm_reference_card_quality_v2.py \
  --method gray_chroma \
  --scales 1 2 3 4 6 8
```

## Output

```text
output/
  summary.csv
  summary.json
  cutsheets/color_correction_contact_sheet.jpg
  <image-stem>/
    before.jpg
    after_color_corrected.jpg
    detected_card_overlay.jpg
    detected_card_warp.jpg
    detected_card_patch_overlay.jpg
    reference_card_template_resized.jpg
    reference_card_comparison.jpg
    patch_samples.json
    color_correction_matrix.json
    metrics.json
    cutsheet.png
```

## Correction methods

Default is:

```text
gray_chroma
```

This uses neutral gray patches to remove color cast while preserving approximate scene brightness. This is safer for real field images than absolute gray balance because it should not blow out the entire frame just because the reference card is dimly lit.

Other modes:

```text
gray_balance  # forces gray patches toward template brightness; can over-brighten scenes
matrix        # experimental 3x3 correction from gray + color patches
none          # detect/crop/sample only
```

## Sample image result

The included `sample_results/sample_contact_sheet.jpg` was generated from the uploaded BM sample image. It detected all four AprilTags and produced a rectified card crop.

Observed sample result:

```text
quality_status: PASS
tag_count: 4
tag_ids: 0 1 2 3
correction_method: gray_chroma
gray_neutrality_before: 4.714
gray_neutrality_after: 1.5713
```

## MVP caveat

The template patch targets are provisional sRGB values from the design file, not measured printed-card values. This is good enough for a local smoke test, but final color science should use measured printed-card patch values and controlled calibration images.
