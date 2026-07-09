# BM Image Quality / Reference Card DOE Tools

This file documents the analysis tools used to select the current reef MVP spatial sampling and HEIC compression settings.

The production runtime README is `README.md`. This file is only for design-of-experiments / analysis workflows.

---

## 1. Purpose

The DOE tools help compare:

```text
native image source
fixed reef/reference-card crop
downsampled spatial output size
HEIC quality
reference-card detectability
file size
base64 payload length
estimated BM message count
estimated transmit duration
```

The goal is to choose an MVP capture/transmit setting that preserves enough image detail for reference-card detection and visual reef inspection while staying within a realistic cellular/Bristlemouth transmission budget.

---

## 2. Key Sprint 02 / Sprint 03 decisions

### Fixed native crop

The working crop selected for the reef reference-card tests is:

```text
native source: 4608×2592
fixed crop:    x=768, y=432, w=3072, h=1728
```

### Spatial density results

The spatial-density ladder tested the same native crop at output sizes such as:

```text
3072×1728
2688×1512
2304×1296
2030×1142
1920×1080
1600×900
1280×720
1024×576
854×480
640×360
```

The important field conclusion was:

```text
1600×900 was the lowest PASS-ish bound for AprilTag/reference-card detection,
but visually it was too coarse for coral texture and readable reference-card text.
2030×1142 @ Q30 and 1920×1080 @ Q40 looked strong visually.
2688×1512 @ Q20 became the practical production candidate after Pi-side HEIC stability testing.
3072×1728 @ Q20 gave more detail but was unstable during HEIC encode on bmcam000.
```

---

## 3. Current production candidate from DOE

```text
source capture:       4608×2592 JPEG
crop:                 768,432,3072,1728
output:               2688×1512
HEIC quality:         Q20
HEIC implementation:  helper subprocess
BM chunk size:        300 base64 chars
estimated chunks:     typically ~115–150
estimated duration:   typically ~10–13 min at 5 sec/chunk
```

This is an MVP engineering choice, not the theoretical maximum image quality.

---

## 4. Mac-side spatial-density sweep

Example command:

```bash
cd ~/Documents/GitHub/bm_cam_legacy/tools

INPUT_IMAGE="$(find ~/Downloads/bm_native_reference_captures -name native_full_q95.jpg | tail -n 1)"

python3 bm_reference_card_spatial_density_sweep.py \
  --input "$INPUT_IMAGE" \
  --output ~/Downloads/bm_spatial_density_sweep/test_$(date -u +%Y%m%dT%H%M%SZ) \
  --fixed-crop 768,432,3072,1728 \
  --corner-map tl:0,tr:1,bl:2,br:3 \
  --include-auto-centered \
  --quality-script ./bm_reference_card_quality_v2.py \
  --prefix test
```

Expected high-level outputs:

```text
fixed/ and auto/ ROI crops
downsample ladder images
quality analyzer outputs
cut sheets
threshold summary
CSV/JSON metrics
```

---

## 5. Mac-side HEIC compression sweep

For the HEIC sprint, the quality ladder was:

```text
10 20 30 40 50 60 70 80 90
```

The heat map should show:

```text
spatial output size vs HEIC quality
300-byte message count
900-byte message count where useful for comparison
estimated transmit duration at 5 seconds/message
```

Preferred duration color bands:

```text
Green:   0–10 min
Yellow: 10–20 min
Orange: 20–25 min
Red:    >=25 min
```

Cut sheets should hold HEIC quality constant and compare output spatial sizes using the same visible reference-card ROI size. Do not shrink each tile so far that full-resolution differences are hidden.

---

## 6. Pi-side current production validation

Use the current runtime commands in `README.md`.

For DOE-style validation on the Pi, the key tests are:

```bash
# capture-only
/usr/bin/python3 -u main_pi_camera.py \
  --skip-time-window \
  --capture-backend libcamera \
  --output-size 2688x1512 \
  --heic-quality 20

# compression-only should call process_image_v2.split_image_heic()
# and use heic_encode_helper.py internally.
```

Do not use `3072×1728` for field MVP without a new stability sprint.

---

## 7. Historical DOE scripts

Older docs referenced:

```text
tests/bm_image_quality_doe_capture.py
tools/make_bm_image_doe_contact_sheet.py
doe_capture_quality_sweep.py
```

These are still useful for legacy image-quality experiments, but the reef MVP production candidate came from the later reference-card spatial-density and HEIC workflows. Before using any older DOE script, verify that the file exists in the current branch and that it matches the current `BM_Devel_Pi` runtime assumptions.

---

## 8. Analysis guardrails

```text
Do not mix spatial-density decisions with HEIC decisions in the same pass.
Record native size, crop coordinates, output size, resampling method, and HEIC quality for every result.
Always report base64 chars and message count, not just raw HEIC bytes.
For AprilTag/reference-card pass/fail, require all four tags unless the sprint explicitly changes that rule.
Use visual cut sheets plus metrics; do not rely on subjective impressions alone.
```
