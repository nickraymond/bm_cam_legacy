# BM Camera Reference-Image Validation

## Purpose

This README records the repeatable workflow used to evaluate realistic reef images and the physical reef reference card through the Bristlemouth camera image pipeline.

The test sequence is:

1. Prepare a synthetic IMX708-native reference image on the Mac.
2. Run a crop sweep on `bmcam001`, download the results, and build crop cut sheets on the Mac.
3. Run a spatial-downsampling sweep on `bmcam001`, download the results, and build spatial cut sheets on the Mac.
4. Capture a real image of the reference card on `bmcam001`, repeat the spatial sweep, and run AprilTag/card-quality analysis on the Mac.

All analysis runs omit `--transmit`.

---

## Final MVP decision

Selected conservative shipping candidate:

```yaml
image_pipeline:
  crop:
    mode: fixed
    x: 768
    y: 432
    w: 3072
    h: 1728

  spatial:
    output_width: 1600
    output_height: 900
    resample: lanczos

  heic:
    quality: 20
```

Rationale:

- `crop_67` preserves the desired field of view.
- `1600 × 900 Q20` reduced the representative reef image to about 95 data buffers and 7.92 minutes of minimum paced transmission time.
- All four AprilTags remained detectable in the physical reference-card test.
- The minimum tag side at `1600 × 900` was approximately `19.8 px`.
- For the MVP field deployment, a slightly softer image that reliably arrives is preferable to a larger image that may not finish transmitting.

Observed reef spatial sweep:

| Profile | Output | HEIC | Buffers | Minimum paced time |
|---|---:|---:|---:|---:|
| up_2304 | 2304 × 1296 | 35.9 KiB | 164 | 13.67 min |
| base_2030 | 2030 × 1142 | 29.8 KiB | 136 | 11.33 min |
| down_1920 | 1920 × 1080 | 27.1 KiB | 124 | 10.33 min |
| **down_1600** | **1600 × 900** | **20.8 KiB** | **95** | **7.92 min** |

Assumptions:

```text
300 characters per data buffer
5 seconds between data buffers
```

The estimate does not include START/END messages, image processing, serial overhead, queueing, or cellular delivery.

---

# Path conventions

## Mac repository

```text
/Users/nickbuemond/Documents/GitHub/bm_cam_legacy
```

## Pi Git checkout

```text
/home/pi/repos/bm_cam_legacy
```

## Pi deployed runtime

```text
/home/pi/BM_Devel_Pi
```

The Git checkout and deployed runtime are separate. Pull source/reference assets under `/home/pi/repos/bm_cam_legacy`; use the active production helpers and YAML under `/home/pi/BM_Devel_Pi`.

Expected tools:

```text
tools/prepare_reference_images.py
tools/make_crop_q20_cut_sheet.py
tools/make_spatial_q20_cut_sheet.py
tools/bm_reference_card_quality_v2.py
```

Expected Pi runtime files:

```text
/home/pi/BM_Devel_Pi/main_pi_camera.py
/home/pi/BM_Devel_Pi/crop_downsample_helper.py
/home/pi/BM_Devel_Pi/heic_encode_helper.py
/home/pi/BM_Devel_Pi/camera_schedule.yaml
```

---

# General safety check on the Pi

Before a manual run:

```bash
ps -eo pid,args | \
grep -E 'python3 .*main_pi_camera.py|python3 .*crop_downsample_helper.py|python3 .*heic_encode_helper.py' | \
grep -v grep || true
```

Do not start a sweep while another image process is active.

Large command blocks below are wrapped in a subshell:

```bash
(
  set -euo pipefail
  ...
)
```

This prevents a failure from closing the interactive VS Code terminal.

---

# A. Create synthetic reference images on the Mac

## Goal

Convert a scientist-supplied reference photograph into a synthetic camera-native source:

```text
original
→ EXIF orientation correction
→ centered 16:9 crop
→ resize to 4608 × 2592
```

This preprocessing belongs on the Mac.

## Run location

**Mac — repository root**

```bash
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy
```

Install Pillow:

```bash
python3 -m pip install Pillow
```

Prepare one image:

```bash
python3 tools/prepare_reference_images.py \
  --input reference_images/P7071008.JPG \
  --output-root reference_images/prepared
```

Batch mode:

```bash
python3 tools/prepare_reference_images.py \
  --input-dir reference_images \
  --output-root reference_images/prepared
```

Expected output:

```text
reference_images/prepared/P7071008/
├── original_normalized.jpg
├── source_16x9.jpg
├── synthetic_native_4608x2592.jpg
├── preparation_manifest.json
└── comparison_sheet.jpg
```

Verify the synthetic sensor geometry:

```bash
python3 - <<'PY'
from pathlib import Path
from PIL import Image

path = Path(
    "reference_images/prepared/P7071008/"
    "synthetic_native_4608x2592.jpg"
)

with Image.open(path) as image:
    print("dimensions:", image.size)
    assert image.size == (4608, 2592)

print("PASS")
PY
```

Because the repository globally ignores image files, keep explicit exceptions at the end of `.gitignore`:

```gitignore
!reference_images/
!reference_images/**/
!reference_images/**/*.jpg
!reference_images/**/*.jpeg
!reference_images/**/*.JPG
!reference_images/**/*.JPEG
!reference_images/**/*.png
!reference_images/**/*.json
```

Commit and push:

```bash
git add reference_images/
git status
git commit -m "Add prepared reef reference image"
git push origin main
```

On `bmcam001`, pull the new files:

```bash
cd /home/pi/repos/bm_cam_legacy
git pull --ff-only origin main
git log -1 --oneline
```

---

# B. Crop sweep

## Test definition

Fixed:

```text
synthetic input: 4608 × 2592
output: 2030 × 1142
HEIC: Q20
resample: Lanczos
no transmission
```

Profiles:

| Profile | x | y | Width | Height |
|---|---:|---:|---:|---:|
| crop_75 | 576 | 324 | 3456 | 1944 |
| crop_67 | 768 | 432 | 3072 | 1728 |
| crop_58 | 960 | 540 | 2688 | 1512 |
| crop_50 | 1152 | 648 | 2304 | 1296 |

## B1. Run on bmcam001

```bash
(
  set -euo pipefail

  REPO="/home/pi/repos/bm_cam_legacy"
  RUNTIME="/home/pi/BM_Devel_Pi"
  CONFIG="$RUNTIME/camera_schedule.yaml"

  SOURCE="$REPO/reference_images/prepared/P7071008/synthetic_native_4608x2592.jpg"
  ORIGINAL="$REPO/reference_images/prepared/P7071008/original_normalized.jpg"
  SOURCE_16X9="$REPO/reference_images/prepared/P7071008/source_16x9.jpg"

  test -s "$SOURCE"
  test -s "$CONFIG"
  test -s "$RUNTIME/crop_downsample_helper.py"
  test -s "$RUNTIME/heic_encode_helper.py"

  ACTIVE="$(
    ps -eo pid,args |
    grep -E 'python3 .*main_pi_camera.py|python3 .*crop_downsample_helper.py|python3 .*heic_encode_helper.py' |
    grep -v grep || true
  )"

  if [ -n "$ACTIVE" ]; then
    echo "ERROR: another image process is active:"
    echo "$ACTIVE"
    false
  fi

  read -r JPEG_Q RESAMPLE BUFFER_SIZE DELAY_SECONDS < <(
    /usr/bin/python3 - "$CONFIG" <<'PY'
import sys, yaml

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    cfg = yaml.safe_load(handle)

pipeline = cfg["image_pipeline"]
serial = cfg["bm_serial"]

print(
    pipeline["source"]["jpeg_quality"],
    pipeline["spatial"]["resample"],
    serial["image_buffer_size"],
    serial["image_transmit_delay_seconds"],
)
PY
  )

  OUT_W=2030
  OUT_H=1142
  HEIC_Q=20

  RUN_TAG="reef_crop_q20_sweep_$(date -u +%Y%m%dT%H%M%SZ)"
  RUN_DIR="$REPO/reference_runs/$RUN_TAG"
  RESULTS="$RUN_DIR/results.csv"
  LOG="$RUN_DIR/run.log"

  mkdir -p "$RUN_DIR"
  cp "$SOURCE" "$RUN_DIR/01_synthetic_native_4608x2592.jpg"
  cp "$CONFIG" "$RUN_DIR/active_camera_schedule.yaml"

  [ ! -s "$ORIGINAL" ] || cp "$ORIGINAL" "$RUN_DIR/00_original_reference_4x3.jpg"
  [ ! -s "$SOURCE_16X9" ] || cp "$SOURCE_16X9" "$RUN_DIR/00_source_16x9.jpg"

  cat > "$RUN_DIR/crop_profiles.tsv" <<'EOF'
crop_75	576	324	3456	1944
crop_67	768	432	3072	1728
crop_58	960	540	2688	1512
crop_50	1152	648	2304	1296
EOF

  printf '%s\n' \
  "profile,crop_x,crop_y,crop_w,crop_h,output_width,output_height,jpeg_bytes,heic_quality,heic_bytes,base64_chars,data_messages,total_serial_messages,pace_seconds,pace_minutes,target_status,crop_attempts,heic_attempts" \
  > "$RESULTS"

  echo "===== REEF CROP Q20 SWEEP =====" | tee "$LOG"

  while IFS=$'\t' read -r PROFILE CROP_X CROP_Y CROP_W CROP_H; do
    echo "===== $PROFILE =====" | tee -a "$LOG"

    PROFILE_DIR="$RUN_DIR/$PROFILE"
    JPEG="$PROFILE_DIR/02_${PROFILE}_${OUT_W}x${OUT_H}.jpg"
    PROGRESS="$PROFILE_DIR/02_${PROFILE}_${OUT_W}x${OUT_H}.crop_progress.jsonl"
    HEIC="$PROFILE_DIR/03_${PROFILE}_q${HEIC_Q}.heic"

    mkdir -p "$PROFILE_DIR"

    CROP_OK=false
    CROP_ATTEMPT_USED=0

    for ATTEMPT in 1 2 3 4; do
      rm -f "$JPEG" "$PROGRESS"

      if timeout 300s /usr/bin/python3 \
        "$RUNTIME/crop_downsample_helper.py" \
        --input "$SOURCE" \
        --output "$JPEG" \
        --crop-x "$CROP_X" \
        --crop-y "$CROP_Y" \
        --crop-w "$CROP_W" \
        --crop-h "$CROP_H" \
        --output-width "$OUT_W" \
        --output-height "$OUT_H" \
        --jpeg-quality "$JPEG_Q" \
        --resample "$RESAMPLE" \
        --progress-log "$PROGRESS" \
        2>&1 | tee -a "$LOG"; then

        if [ -s "$JPEG" ]; then
          CROP_OK=true
          CROP_ATTEMPT_USED="$ATTEMPT"
          break
        fi
      fi

      sync
      sleep 8
    done

    [ "$CROP_OK" = true ] || false
    sync
    sleep 5

    HEIC_OK=false
    HEIC_ATTEMPT_USED=0

    for ATTEMPT in 1 2 3 4; do
      rm -f "$HEIC"

      if timeout 300s /usr/bin/python3 \
        "$RUNTIME/heic_encode_helper.py" \
        --input "$JPEG" \
        --output "$HEIC" \
        --quality "$HEIC_Q" \
        2>&1 | tee -a "$LOG"; then

        if [ -s "$HEIC" ]; then
          HEIC_OK=true
          HEIC_ATTEMPT_USED="$ATTEMPT"
          break
        fi
      fi

      sync
      sleep 8
    done

    [ "$HEIC_OK" = true ] || false

    /usr/bin/python3 - \
      "$PROFILE" "$CROP_X" "$CROP_Y" "$CROP_W" "$CROP_H" \
      "$OUT_W" "$OUT_H" "$JPEG" "$HEIC" \
      "$HEIC_Q" "$BUFFER_SIZE" "$DELAY_SECONDS" \
      "$CROP_ATTEMPT_USED" "$HEIC_ATTEMPT_USED" \
      >> "$RESULTS" <<'PY'
import base64, csv, math, sys
from pathlib import Path

profile = sys.argv[1]
crop_x, crop_y, crop_w, crop_h = map(int, sys.argv[2:6])
out_w, out_h = map(int, sys.argv[6:8])
jpeg_path = Path(sys.argv[8])
heic_path = Path(sys.argv[9])
quality = int(sys.argv[10])
buffer_size = int(sys.argv[11])
delay = float(sys.argv[12])
crop_attempts = int(sys.argv[13])
heic_attempts = int(sys.argv[14])

data = heic_path.read_bytes()
base64_chars = len(base64.b64encode(data))
messages = math.ceil(base64_chars / buffer_size)
seconds = messages * delay
minutes = seconds / 60
status = "PASS" if minutes <= 10 else "WARN" if minutes <= 15 else "RISKY" if minutes <= 20 else "FAIL"

csv.writer(sys.stdout).writerow([
    profile, crop_x, crop_y, crop_w, crop_h,
    out_w, out_h, jpeg_path.stat().st_size,
    quality, len(data), base64_chars, messages, messages + 2,
    f"{seconds:.0f}", f"{minutes:.2f}", status,
    crop_attempts, heic_attempts,
])
PY

    sync
    sleep 8

  done < "$RUN_DIR/crop_profiles.tsv"

  /usr/bin/python3 - "$RESULTS" <<'PY'
import csv, sys

with open(sys.argv[1], newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

print(f"{'PROFILE':<10}{'ROI':<14}{'HEIC KiB':>11}{'BUFFERS':>10}{'MINUTES':>10}{'STATUS':>10}")
for row in rows:
    roi = f"{row['crop_w']}x{row['crop_h']}"
    print(
        f"{row['profile']:<10}{roi:<14}"
        f"{int(row['heic_bytes']) / 1024:>11.1f}"
        f"{row['data_messages']:>10}"
        f"{float(row['pace_minutes']):>10.2f}"
        f"{row['target_status']:>10}"
    )
PY

  cd "$REPO/reference_runs"
  tar -czf "${RUN_TAG}.tar.gz" "$RUN_TAG"
  cp "${RUN_TAG}.tar.gz" latest_reef_crop_q20_sweep.tar.gz

  echo "RUN_DIR=$RUN_DIR"
)
```

## B2. Download on the Mac

```bash
LOCAL_ROOT="$HOME/Desktop/reef_crop_q20_review"
mkdir -p "$LOCAL_ROOT"

scp \
  pi@bmcam001:/home/pi/repos/bm_cam_legacy/reference_runs/latest_reef_crop_q20_sweep.tar.gz \
  "$LOCAL_ROOT/"

tar -xzf \
  "$LOCAL_ROOT/latest_reef_crop_q20_sweep.tar.gz" \
  -C "$LOCAL_ROOT"

RUN_DIR="$(
  find "$LOCAL_ROOT" \
    -maxdepth 1 \
    -type d \
    -name 'reef_crop_q20_sweep_*' \
    -print |
  sort |
  tail -1
)"

echo "RUN_DIR=$RUN_DIR"
```

## B3. Create crop cut sheets on the Mac

```bash
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy

python3 tools/make_crop_q20_cut_sheet.py \
  --run-dir "$RUN_DIR"

open "$RUN_DIR/cut_sheets"
```

If this is a new terminal, replace `$RUN_DIR` with the literal extracted path.

Conclusion: crop changes had only a modest payload effect because all profiles were resized to the same `2030 × 1142` output.

---

# C. Spatial-downsampling sweep

## Test definition

Fixed:

```text
crop_67: x768, y432, 3072 × 1728
HEIC: Q20
resample: Lanczos
no transmission
```

Profiles:

```text
up_2304:   2304 × 1296
base_2030: 2030 × 1142
down_1920: 1920 × 1080
down_1600: 1600 × 900
```

## C1. Run on bmcam001

```bash
(
  set -euo pipefail

  REPO="/home/pi/repos/bm_cam_legacy"
  RUNTIME="/home/pi/BM_Devel_Pi"
  CONFIG="$RUNTIME/camera_schedule.yaml"
  SOURCE="$REPO/reference_images/prepared/P7071008/synthetic_native_4608x2592.jpg"

  CROP_X=768
  CROP_Y=432
  CROP_W=3072
  CROP_H=1728
  HEIC_Q=20

  test -s "$SOURCE"
  test -s "$CONFIG"
  test -s "$RUNTIME/crop_downsample_helper.py"
  test -s "$RUNTIME/heic_encode_helper.py"

  read -r JPEG_Q RESAMPLE BUFFER_SIZE DELAY_SECONDS < <(
    /usr/bin/python3 - "$CONFIG" <<'PY'
import sys, yaml

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    cfg = yaml.safe_load(handle)

pipeline = cfg["image_pipeline"]
serial = cfg["bm_serial"]

print(
    pipeline["source"]["jpeg_quality"],
    pipeline["spatial"]["resample"],
    serial["image_buffer_size"],
    serial["image_transmit_delay_seconds"],
)
PY
  )

  RUN_TAG="reef_spatial_q20_sweep_$(date -u +%Y%m%dT%H%M%SZ)"
  RUN_DIR="$REPO/reference_runs/$RUN_TAG"
  RESULTS="$RUN_DIR/results.csv"
  LOG="$RUN_DIR/run.log"

  mkdir -p "$RUN_DIR"
  cp "$SOURCE" "$RUN_DIR/01_synthetic_native_4608x2592.jpg"
  cp "$CONFIG" "$RUN_DIR/active_camera_schedule.yaml"

  cat > "$RUN_DIR/spatial_profiles.tsv" <<'EOF'
up_2304	2304	1296
base_2030	2030	1142
down_1920	1920	1080
down_1600	1600	900
EOF

  printf '%s\n' \
  "profile,output_width,output_height,jpeg_bytes,heic_quality,heic_bytes,base64_chars,data_messages,total_serial_messages,pace_seconds,pace_minutes,target_status,crop_attempts,heic_attempts" \
  > "$RESULTS"

  echo "===== REEF SPATIAL Q20 SWEEP =====" | tee "$LOG"

  while IFS=$'\t' read -r PROFILE OUT_W OUT_H; do
    echo "===== $PROFILE: ${OUT_W}x${OUT_H} =====" | tee -a "$LOG"

    PROFILE_DIR="$RUN_DIR/$PROFILE"
    JPEG="$PROFILE_DIR/02_${PROFILE}_${OUT_W}x${OUT_H}.jpg"
    PROGRESS="$PROFILE_DIR/02_${PROFILE}_${OUT_W}x${OUT_H}.crop_progress.jsonl"
    HEIC="$PROFILE_DIR/03_${PROFILE}_q${HEIC_Q}.heic"

    mkdir -p "$PROFILE_DIR"

    CROP_OK=false
    CROP_ATTEMPT_USED=0

    for ATTEMPT in 1 2 3 4; do
      rm -f "$JPEG" "$PROGRESS"

      if timeout 300s /usr/bin/python3 \
        "$RUNTIME/crop_downsample_helper.py" \
        --input "$SOURCE" \
        --output "$JPEG" \
        --crop-x "$CROP_X" \
        --crop-y "$CROP_Y" \
        --crop-w "$CROP_W" \
        --crop-h "$CROP_H" \
        --output-width "$OUT_W" \
        --output-height "$OUT_H" \
        --jpeg-quality "$JPEG_Q" \
        --resample "$RESAMPLE" \
        --progress-log "$PROGRESS" \
        2>&1 | tee -a "$LOG"; then

        if [ -s "$JPEG" ]; then
          CROP_OK=true
          CROP_ATTEMPT_USED="$ATTEMPT"
          break
        fi
      fi

      sync
      sleep 8
    done

    [ "$CROP_OK" = true ] || false
    sync
    sleep 5

    HEIC_OK=false
    HEIC_ATTEMPT_USED=0

    for ATTEMPT in 1 2 3 4; do
      rm -f "$HEIC"

      if timeout 300s /usr/bin/python3 \
        "$RUNTIME/heic_encode_helper.py" \
        --input "$JPEG" \
        --output "$HEIC" \
        --quality "$HEIC_Q" \
        2>&1 | tee -a "$LOG"; then

        if [ -s "$HEIC" ]; then
          HEIC_OK=true
          HEIC_ATTEMPT_USED="$ATTEMPT"
          break
        fi
      fi

      sync
      sleep 8
    done

    [ "$HEIC_OK" = true ] || false

    /usr/bin/python3 - \
      "$PROFILE" "$OUT_W" "$OUT_H" "$JPEG" "$HEIC" \
      "$HEIC_Q" "$BUFFER_SIZE" "$DELAY_SECONDS" \
      "$CROP_ATTEMPT_USED" "$HEIC_ATTEMPT_USED" \
      >> "$RESULTS" <<'PY'
import base64, csv, math, sys
from pathlib import Path

profile = sys.argv[1]
width = int(sys.argv[2])
height = int(sys.argv[3])
jpeg_path = Path(sys.argv[4])
heic_path = Path(sys.argv[5])
quality = int(sys.argv[6])
buffer_size = int(sys.argv[7])
delay = float(sys.argv[8])
crop_attempts = int(sys.argv[9])
heic_attempts = int(sys.argv[10])

data = heic_path.read_bytes()
base64_chars = len(base64.b64encode(data))
messages = math.ceil(base64_chars / buffer_size)
seconds = messages * delay
minutes = seconds / 60
status = "PASS" if minutes <= 10 else "WARN" if minutes <= 15 else "RISKY" if minutes <= 20 else "FAIL"

csv.writer(sys.stdout).writerow([
    profile, width, height, jpeg_path.stat().st_size,
    quality, len(data), base64_chars, messages, messages + 2,
    f"{seconds:.0f}", f"{minutes:.2f}", status,
    crop_attempts, heic_attempts,
])
PY

    sync
    sleep 8

  done < "$RUN_DIR/spatial_profiles.tsv"

  /usr/bin/python3 - "$RESULTS" <<'PY'
import csv, sys

with open(sys.argv[1], newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

print(f"{'PROFILE':<12}{'OUTPUT':<14}{'HEIC KiB':>11}{'BUFFERS':>10}{'MINUTES':>10}{'STATUS':>10}")
for row in rows:
    output = f"{row['output_width']}x{row['output_height']}"
    print(
        f"{row['profile']:<12}{output:<14}"
        f"{int(row['heic_bytes']) / 1024:>11.1f}"
        f"{row['data_messages']:>10}"
        f"{float(row['pace_minutes']):>10.2f}"
        f"{row['target_status']:>10}"
    )
PY

  cd "$REPO/reference_runs"
  tar -czf "${RUN_TAG}.tar.gz" "$RUN_TAG"
  cp "${RUN_TAG}.tar.gz" latest_reef_spatial_q20_sweep.tar.gz

  echo "RUN_DIR=$RUN_DIR"
)
```

## C2. Download on the Mac

```bash
LOCAL_ROOT="$HOME/Desktop/reef_spatial_q20_review"
mkdir -p "$LOCAL_ROOT"

scp \
  pi@bmcam001:/home/pi/repos/bm_cam_legacy/reference_runs/latest_reef_spatial_q20_sweep.tar.gz \
  "$LOCAL_ROOT/"

tar -xzf \
  "$LOCAL_ROOT/latest_reef_spatial_q20_sweep.tar.gz" \
  -C "$LOCAL_ROOT"

RUN_DIR="$(
  find "$LOCAL_ROOT" \
    -maxdepth 1 \
    -type d \
    -name 'reef_spatial_q20_sweep_*' \
    -print |
  sort |
  tail -1
)"

echo "RUN_DIR=$RUN_DIR"
```

## C3. Create spatial cut sheets on the Mac

```bash
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy

python3 tools/make_spatial_q20_cut_sheet.py \
  --run-dir "$RUN_DIR"

open "$RUN_DIR/cut_sheets"
```

Most useful sheets:

```text
02_decoded_heic_full_frame_normalized.png
04_decoded_heic_center_detail_native_pixels.png
base_2030_pre_vs_heic_100pct.png
down_1600_pre_vs_heic_100pct.png
```

---

# D. Real camera capture, spatial sweep, and AprilTag analysis

## Setup

- Place the card approximately five feet from `bmcam001`.
- Keep the whole card inside `crop_67`.
- Confirm no production capture is active.

## D1. Capture one native image and run the spatial sweep on bmcam001

This uses `main_pi_camera.py` without `--transmit`, finds the newly created native `4608 × 2592` JPEG, and then runs the same four-profile spatial sweep.

```bash
(
  set -euo pipefail

  REPO="/home/pi/repos/bm_cam_legacy"
  RUNTIME="/home/pi/BM_Devel_Pi"
  CONFIG="$RUNTIME/camera_schedule.yaml"
  IMAGE_DIR="$RUNTIME/images"

  test -s "$CONFIG"
  test -s "$RUNTIME/main_pi_camera.py"
  test -s "$RUNTIME/crop_downsample_helper.py"
  test -s "$RUNTIME/heic_encode_helper.py"

  ACTIVE="$(
    ps -eo pid,args |
    grep -E 'python3 .*main_pi_camera.py|python3 .*crop_downsample_helper.py|python3 .*heic_encode_helper.py' |
    grep -v grep || true
  )"

  if [ -n "$ACTIVE" ]; then
    echo "ERROR: another image process is active:"
    echo "$ACTIVE"
    false
  fi

  RUN_TAG="reference_card_spatial_q20_$(date -u +%Y%m%dT%H%M%SZ)"
  RUN_DIR="$REPO/reference_runs/$RUN_TAG"
  CAPTURE_LOG="$RUN_DIR/00_camera_capture.log"
  RESULTS="$RUN_DIR/results.csv"
  LOG="$RUN_DIR/run.log"

  mkdir -p "$RUN_DIR"

  CAPTURE_START="$(date +%s)"

  cd "$RUNTIME"

  timeout 900s /usr/bin/python3 -u main_pi_camera.py \
    --skip-time-window \
    2>&1 | tee "$CAPTURE_LOG"

  CAPTURED_SOURCE="$(
    /usr/bin/python3 - "$IMAGE_DIR" "$CAPTURE_START" <<'PY'
import sys
from pathlib import Path
from PIL import Image

root = Path(sys.argv[1])
start = float(sys.argv[2])
matches = []

for pattern in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG"):
    for path in root.rglob(pattern):
        try:
            if path.stat().st_mtime < start - 2:
                continue
            with Image.open(path) as image:
                if image.size == (4608, 2592):
                    matches.append(path)
        except Exception:
            pass

if not matches:
    raise SystemExit("No new 4608x2592 native image found.")

matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
print(matches[0])
PY
  )"

  test -s "$CAPTURED_SOURCE"

  SOURCE="$RUN_DIR/01_camera_native_4608x2592.jpg"
  cp "$CAPTURED_SOURCE" "$SOURCE"
  cp "$CONFIG" "$RUN_DIR/active_camera_schedule.yaml"

  ln -s \
    "01_camera_native_4608x2592.jpg" \
    "$RUN_DIR/01_synthetic_native_4608x2592.jpg"

  CROP_X=768
  CROP_Y=432
  CROP_W=3072
  CROP_H=1728
  HEIC_Q=20

  read -r JPEG_Q RESAMPLE BUFFER_SIZE DELAY_SECONDS < <(
    /usr/bin/python3 - "$CONFIG" <<'PY'
import sys, yaml

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    cfg = yaml.safe_load(handle)

pipeline = cfg["image_pipeline"]
serial = cfg["bm_serial"]

print(
    pipeline["source"]["jpeg_quality"],
    pipeline["spatial"]["resample"],
    serial["image_buffer_size"],
    serial["image_transmit_delay_seconds"],
)
PY
  )

  cat > "$RUN_DIR/spatial_profiles.tsv" <<'EOF'
up_2304	2304	1296
base_2030	2030	1142
down_1920	1920	1080
down_1600	1600	900
EOF

  printf '%s\n' \
  "profile,output_width,output_height,jpeg_bytes,heic_quality,heic_bytes,base64_chars,data_messages,total_serial_messages,pace_seconds,pace_minutes,target_status,crop_attempts,heic_attempts" \
  > "$RESULTS"

  echo "===== REFERENCE CARD SPATIAL Q20 SWEEP =====" | tee "$LOG"

  while IFS=$'\t' read -r PROFILE OUT_W OUT_H; do
    echo "===== $PROFILE: ${OUT_W}x${OUT_H} =====" | tee -a "$LOG"

    PROFILE_DIR="$RUN_DIR/$PROFILE"
    JPEG="$PROFILE_DIR/02_${PROFILE}_${OUT_W}x${OUT_H}.jpg"
    PROGRESS="$PROFILE_DIR/02_${PROFILE}_${OUT_W}x${OUT_H}.crop_progress.jsonl"
    HEIC="$PROFILE_DIR/03_${PROFILE}_q${HEIC_Q}.heic"

    mkdir -p "$PROFILE_DIR"

    CROP_OK=false
    CROP_ATTEMPT_USED=0

    for ATTEMPT in 1 2 3 4; do
      rm -f "$JPEG" "$PROGRESS"

      if timeout 300s /usr/bin/python3 \
        "$RUNTIME/crop_downsample_helper.py" \
        --input "$SOURCE" \
        --output "$JPEG" \
        --crop-x "$CROP_X" \
        --crop-y "$CROP_Y" \
        --crop-w "$CROP_W" \
        --crop-h "$CROP_H" \
        --output-width "$OUT_W" \
        --output-height "$OUT_H" \
        --jpeg-quality "$JPEG_Q" \
        --resample "$RESAMPLE" \
        --progress-log "$PROGRESS" \
        2>&1 | tee -a "$LOG"; then

        if [ -s "$JPEG" ]; then
          CROP_OK=true
          CROP_ATTEMPT_USED="$ATTEMPT"
          break
        fi
      fi

      sync
      sleep 8
    done

    [ "$CROP_OK" = true ] || false
    sync
    sleep 5

    HEIC_OK=false
    HEIC_ATTEMPT_USED=0

    for ATTEMPT in 1 2 3 4; do
      rm -f "$HEIC"

      if timeout 300s /usr/bin/python3 \
        "$RUNTIME/heic_encode_helper.py" \
        --input "$JPEG" \
        --output "$HEIC" \
        --quality "$HEIC_Q" \
        2>&1 | tee -a "$LOG"; then

        if [ -s "$HEIC" ]; then
          HEIC_OK=true
          HEIC_ATTEMPT_USED="$ATTEMPT"
          break
        fi
      fi

      sync
      sleep 8
    done

    [ "$HEIC_OK" = true ] || false

    /usr/bin/python3 - \
      "$PROFILE" "$OUT_W" "$OUT_H" "$JPEG" "$HEIC" \
      "$HEIC_Q" "$BUFFER_SIZE" "$DELAY_SECONDS" \
      "$CROP_ATTEMPT_USED" "$HEIC_ATTEMPT_USED" \
      >> "$RESULTS" <<'PY'
import base64, csv, math, sys
from pathlib import Path

profile = sys.argv[1]
width = int(sys.argv[2])
height = int(sys.argv[3])
jpeg_path = Path(sys.argv[4])
heic_path = Path(sys.argv[5])
quality = int(sys.argv[6])
buffer_size = int(sys.argv[7])
delay = float(sys.argv[8])
crop_attempts = int(sys.argv[9])
heic_attempts = int(sys.argv[10])

data = heic_path.read_bytes()
base64_chars = len(base64.b64encode(data))
messages = math.ceil(base64_chars / buffer_size)
seconds = messages * delay
minutes = seconds / 60
status = "PASS" if minutes <= 10 else "WARN" if minutes <= 15 else "RISKY" if minutes <= 20 else "FAIL"

csv.writer(sys.stdout).writerow([
    profile, width, height, jpeg_path.stat().st_size,
    quality, len(data), base64_chars, messages, messages + 2,
    f"{seconds:.0f}", f"{minutes:.2f}", status,
    crop_attempts, heic_attempts,
])
PY

    sync
    sleep 8

  done < "$RUN_DIR/spatial_profiles.tsv"

  cd "$REPO/reference_runs"
  tar -czf "${RUN_TAG}.tar.gz" "$RUN_TAG"
  cp "${RUN_TAG}.tar.gz" latest_reference_card_spatial_q20.tar.gz

  echo "RUN_DIR=$RUN_DIR"
)
```

## D2. Download on the Mac

```bash
LOCAL_ROOT="$HOME/Desktop/reference_card_spatial_q20_review"
mkdir -p "$LOCAL_ROOT"

scp \
  pi@bmcam001:/home/pi/repos/bm_cam_legacy/reference_runs/latest_reference_card_spatial_q20.tar.gz \
  "$LOCAL_ROOT/"

tar -xzf \
  "$LOCAL_ROOT/latest_reference_card_spatial_q20.tar.gz" \
  -C "$LOCAL_ROOT"

RUN_DIR="$(
  find "$LOCAL_ROOT" \
    -maxdepth 1 \
    -type d \
    -name 'reference_card_spatial_q20_*' \
    -print |
  sort |
  tail -1
)"

echo "RUN_DIR=$RUN_DIR"
```

## D3. Create the normal spatial cut sheets on the Mac

```bash
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy

python3 tools/make_spatial_q20_cut_sheet.py \
  --run-dir "$RUN_DIR"

open "$RUN_DIR/cut_sheets"
```

## D4. Decode HEIC and run AprilTag/card-quality analysis on the Mac

```bash
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy

(
  set -euo pipefail

  LOCAL_ROOT="$HOME/Desktop/reference_card_spatial_q20_review"

  RUN_DIR="$(
    find "$LOCAL_ROOT" \
      -maxdepth 1 \
      -type d \
      -name 'reference_card_spatial_q20_*' \
      -print |
    sort |
    tail -1
  )"

  if [ -z "$RUN_DIR" ] || [ ! -d "$RUN_DIR" ]; then
    echo "ERROR: reference-card run not found under $LOCAL_ROOT"
    false
  fi

  QUALITY_SCRIPT="$PWD/tools/bm_reference_card_quality_v2.py"
  DECODED_DIR="$RUN_DIR/decoded_q20"
  QUALITY_DIR="$RUN_DIR/apriltag_quality_q20"

  test -s "$QUALITY_SCRIPT"
  mkdir -p "$DECODED_DIR" "$QUALITY_DIR"

  for PROFILE in up_2304 base_2030 down_1920 down_1600; do
    HEIC_FILE="$(
      find "$RUN_DIR/$PROFILE" \
        -maxdepth 1 \
        -type f \
        -name '03_*.heic' \
        -print |
      head -1
    )"

    if [ -z "$HEIC_FILE" ] || [ ! -s "$HEIC_FILE" ]; then
      echo "ERROR: no HEIC found for $PROFILE"
      false
    fi

    sips \
      -s format png \
      "$HEIC_FILE" \
      --out "$DECODED_DIR/${PROFILE}_q20_decoded.png" \
      >/dev/null
  done

  python3 -m pip install \
    opencv-contrib-python \
    pillow \
    numpy

  python3 "$QUALITY_SCRIPT" \
    --input "$DECODED_DIR" \
    --output "$QUALITY_DIR" \
    --corner-map tl:0,tr:1,bl:2,br:3 \
    --scales 1 2 3 4 6 8 \
    --reference "$DECODED_DIR/up_2304_q20_decoded.png"

  echo "QUALITY_DIR=$QUALITY_DIR"
  open "$QUALITY_DIR"
)
```

Primary output:

```text
apriltag_quality_q20/cut_sheets/reference_card_quality_sheet.jpg
```

Current MVP interpretation:

```text
PASS:
  all four tags 0,1,2,3 detected
  minimum tag side >= 18 px

WARN:
  all four tags detected
  minimum tag side >= 10 px but < 18 px

FAIL:
  a required tag is missing
  or minimum tag side < 10 px
```

The tested `1600 × 900 Q20` profile passed with all four tags and a minimum tag side of approximately `19.8 px`.

---

# Known failure modes

## Empty `$RUN_DIR`

Symptom:

```text
mkdir: /decoded_q20: Read-only file system
```

Cause: `$RUN_DIR` was empty, turning `"$RUN_DIR/decoded_q20"` into `/decoded_q20`.

Check before use:

```bash
echo "RUN_DIR=$RUN_DIR"
test -n "$RUN_DIR"
test -d "$RUN_DIR"
```

## Terminal closes with exit code 1

Cause: `exit 1` was run directly in the interactive shell.

Fix: use the subshell pattern shown in this README.

## Wrong Pi repo path

Correct:

```text
/home/pi/repos/bm_cam_legacy
```

Incorrect:

```text
/home/pi/bm_cam_legacy
```

## Camera is already in use

Check:

```bash
ps -eo pid,args | \
grep -E 'main_pi_camera.py|libcamera-still|rpicam-still' | \
grep -v grep
```

## Pi memory pressure

Check:

```bash
grep -E "MemAvailable|CmaTotal|CmaFree|SwapTotal|SwapFree" /proc/meminfo
```

Expected CMA total for native IMX708 capture:

```text
approximately 131072 kB
```

---

# Core conclusions

1. Crop changes had little payload impact because every crop was resized to the same final output dimensions.
2. Spatial output dimensions were the effective bandwidth control.
3. `1920 × 1080` remained slightly above the ten-minute target.
4. `1600 × 900 Q20` reached approximately:
   - 20.8 KiB HEIC
   - 95 data buffers
   - 7.92 minutes minimum paced time
5. The physical reference-card test still detected all four AprilTags at `1600 × 900`.
6. The conservative production candidate is:

```text
crop_67
1600 × 900
Lanczos
HEIC Q20
```

---

# Next operational step

Update the active YAML on `bmcam001` to:

```yaml
image_pipeline:
  spatial:
    output_width: 1600
    output_height: 900
    resample: lanczos

  heic:
    quality: 20
```

Keep:

```yaml
crop:
  x: 768
  y: 432
  w: 3072
  h: 1728
```

Then perform:

1. one no-transmit local production capture;
2. one full transmission capture;
3. backend reconstruction and JPEG derivative verification;
4. actual message-count comparison against the analysis estimate;
5. replication on `bmcam002` only after `bmcam001` is confirmed.
