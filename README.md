# bm_cam_legacy

Legacy Bristlemouth Raspberry Pi camera runtime for potted BM cameras and fresh development units such as `bmcam000`.

This repo is the source of truth for the Pi-side runtime. The live runtime on each Pi remains a separate copied folder so that Git operations do not accidentally break a working camera:

```text
/home/pi/repos/bm_cam_legacy      # Git checkout / source-controlled repo
/home/pi/BM_Devel_Pi              # Runtime folder used by cron/manual tests
```

Do **not** turn `/home/pi/BM_Devel_Pi` into a Git working tree on production devices.

---

## 1. Current MVP status

### Current reef MVP image/transmit path

The current reef field-test candidate is the `libcamera` crop + HEIC subprocess path:

```text
native capture:       4608×2592 JPEG from libcamera-still
fixed crop:           x=768, y=432, w=3072, h=1728
spatial output:       2688×1512
HEIC quality:         Q20
HEIC implementation:  heic_encode_helper.py subprocess
BM route:             0x02 cellular-only
chunk size:           300 base64 chars per image chunk
chunk pacing:         5 seconds between chunks
```

This path exists because the reference-card spatial-density and HEIC tests showed that the old 480p/720p path was leaving useful image quality on the table. The optimized path captures high-quality source, crops the reef/reference-card ROI, downsamples to the selected field-test output size, encodes HEIC in an isolated helper process, chunks the HEIC, and transmits over Bristlemouth.

### Known validated behavior

On `bmcam000`, after the runtime-stabilization patch:

```text
capture-only 2688×1512: PASS
compression-only via heic_encode_helper.py: PASS
fresh capture + compression: PASS
manual transmit: reached buffer sending with ~115–140 chunks depending on image content
```

Typical observed payload:

```text
HEIC bytes:       ~25–32 KB
base64 chars:     ~34–42 K chars
300-byte buffers: ~115–140
estimated UART/queue pacing time at 5 sec/chunk: ~10–12 min
```

### Known limitations

Do not hide these limitations when handing the system to another developer or deploying as an engineering MVP:

```text
camera-frame metadata is incomplete
libcamera --metadata caused instability and is intentionally deferred
3072×1728 HEIC Q20 was unstable on bmcam000 and should not be used for field MVP
camera settings are not fully locked yet
SD-card health/ring-buffer are not implemented yet
cron/field soak testing is still required before treating this as production
```

---

## 2. Files in the runtime path

Minimum files expected in `/home/pi/BM_Devel_Pi`:

```text
main_pi_camera.py
process_image_v2.py
heic_encode_helper.py
bm_serial.py
spotter_time_sync.py
camera_schedule.yaml
run_capture_cycle.sh
read_CPU_temp.py
```

Important current roles:

```text
main_pi_camera.py        CLI/orchestration: load config, schedule gate, capture, optional transmit
process_image_v2.py      image pipeline support, crop/downsample, HEIC helper wrapper, chunk/send
heic_encode_helper.py    lightweight isolated HEIC encoder subprocess
spotter_time_sync.py     Spotter/BM UTC or RTC time helper plus current YAML parser
bm_serial.py             Bristlemouth UART publish/transmit implementation
camera_schedule.yaml     runtime schedule, image pipeline, and BM serial config
run_capture_cycle.sh     cron wrapper
```

`spotter_time_sync.py` currently does more than pure time sync because it also loads runtime config. Treat that as accepted technical debt for this MVP; a future refactor should move config loading into a dedicated config module.

---

## 3. Current YAML shape

Example `camera_schedule.yaml` for the reef MVP path:

```yaml
time_source: "spotter_utc"

timezone_preset: "sf"
timezone: "America/Los_Angeles"

enforce_time_window: true
enforce_spotter_time_window: true

transmit_window:
  start: "08:00"
  end: "15:00"

set_system_clock_from_spotter: true
spotter_time_timeout_seconds: 60
allow_system_clock_fallback: false
uart_port: "/dev/ttyAMA0"
baudrate: 115200

rtc:
  hwclock_path: "/usr/sbin/hwclock"
  set_system_clock_from_rtc: true
  require_plausible_after_utc: "2026-01-01T00:00:00+00:00"

# Legacy fallback fields still exist for older path compatibility.
image:
  resolution_key: "720p"
  image_quality: 25

# Current reef MVP path.
image_pipeline:
  enabled: true
  capture_backend: "rpicam"   # falls back to libcamera-still if rpicam-still is absent

  source:
    width: 4608
    height: 2592
    jpeg_quality: 95

  crop:
    mode: "fixed"
    x: 768
    y: 432
    w: 3072
    h: 1728

  spatial:
    output_width: 2688
    output_height: 1512
    resample: "lanczos"

  heic:
    quality: 20

bm_serial:
  network_type: 0x02
  image_buffer_size: 300
  image_transmit_delay_seconds: 5
```

---

## 4. Important development guardrails

For this repo, stability beats cleverness.

```text
Do not change bm_serial.py unless the explicit task is BM protocol/transport.
Do not change send_buffers() chunk-loop behavior unless the explicit task is transmission.
Do not reintroduce libcamera-still --metadata into the critical capture command.
Do not use 3072×1728 for field MVP HEIC on bmcam000.
Do not use large 900/980-byte chunks for this reef MVP path unless a new test branch is explicitly created.
Do not enable bm-daemon while manually testing this legacy runtime unless the test explicitly requires it.
Do not enable cron until manual capture/compress/transmit passes.
```

When changing runtime code:

```text
1. Work on a branch.
2. Back up /home/pi/BM_Devel_Pi before copying runtime files.
3. Check file sizes before/after and verify the direction makes sense.
4. Run py_compile before camera tests.
5. Test capture-only, then compression-only, then transmit.
6. Keep logs for every manual run.
```

---

## 5. Updating bmcam000 from Git safely

On the Pi:

```bash
REPO_DIR="/home/pi/repos/bm_cam_legacy"
APP_SRC_DIR="$REPO_DIR/BM_Devel_Pi"
APP_DIR="/home/pi/BM_Devel_Pi"

cd "$REPO_DIR"
git fetch origin
git checkout sprint03-libcamera-crop-heic-upload-test
git pull --ff-only
```

Stop dev-conflicting services while manually testing:

```bash
sudo systemctl stop bm-daemon.service 2>/dev/null || true
sudo systemctl disable bm-daemon.service 2>/dev/null || true
crontab -r 2>/dev/null || true

pgrep -af "bm_daemon|bm-agent|bm_agent|main_pi_camera|libcamera-still|rpicam-still|heic_encode_helper" || echo "OK: no conflicting process"
```

Back up and copy runtime:

```bash
RUN_TAG="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$HOME/bmcam000_runtime_backup_$RUN_TAG"
mkdir -p "$BACKUP_DIR"

cp "$APP_DIR/main_pi_camera.py" "$BACKUP_DIR/main_pi_camera.py"
cp "$APP_DIR/process_image_v2.py" "$BACKUP_DIR/process_image_v2.py"
cp "$APP_DIR/heic_encode_helper.py" "$BACKUP_DIR/heic_encode_helper.py" 2>/dev/null || true
cp "$APP_DIR/spotter_time_sync.py" "$BACKUP_DIR/spotter_time_sync.py"
cp "$APP_DIR/camera_schedule.yaml" "$BACKUP_DIR/camera_schedule.yaml"

cp "$APP_SRC_DIR/main_pi_camera.py" "$APP_DIR/main_pi_camera.py"
cp "$APP_SRC_DIR/process_image_v2.py" "$APP_DIR/process_image_v2.py"
cp "$APP_SRC_DIR/heic_encode_helper.py" "$APP_DIR/heic_encode_helper.py"
cp "$APP_SRC_DIR/spotter_time_sync.py" "$APP_DIR/spotter_time_sync.py"
cp "$APP_SRC_DIR/camera_schedule.yaml" "$APP_DIR/camera_schedule.yaml"

chmod +x "$APP_DIR/heic_encode_helper.py"
git -C "$REPO_DIR" rev-parse --short=12 HEAD > "$APP_DIR/software_sha.txt"
```

Verify:

```bash
cd "$APP_DIR"

ls -lh main_pi_camera.py process_image_v2.py heic_encode_helper.py spotter_time_sync.py camera_schedule.yaml
cat software_sha.txt

grep -n "Transmit disabled; skipping compact wake telemetry" main_pi_camera.py
grep -n "def _format_start_metadata" process_image_v2.py
grep -n "def _get_bm_serial" process_image_v2.py
grep -n "output_width" -A2 camera_schedule.yaml
grep -n "bm_serial:" -A5 camera_schedule.yaml

/usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py heic_encode_helper.py spotter_time_sync.py bm_serial.py
echo "syntax_ok=$?"
```

---

## 6. Preflight before tests

```bash
cd /home/pi/BM_Devel_Pi

echo "=== preflight ==="
hostname
uptime
vcgencmd get_throttled || true
vcgencmd measure_temp || true
df -h /
grep -E "MemAvailable|CmaTotal|CmaFree|SwapTotal|SwapFree" /proc/meminfo || true

echo
echo "conflicting processes:"
pgrep -af "bm_daemon|bm-agent|bm_agent|main_pi_camera|libcamera-still|rpicam-still|heic_encode_helper" || echo "OK: no conflicting process"

echo
echo "cron:"
crontab -l 2>/dev/null || echo "OK: no user crontab"

echo
echo "config:"
grep -n "image_pipeline:" -A25 camera_schedule.yaml
grep -n "bm_serial:" -A5 camera_schedule.yaml

echo
echo "sha:"
cat software_sha.txt 2>/dev/null || true
```

`throttled=0x0` is preferred. If the Pi requires a hard power cycle after a test, treat that as a real system fault, not a Wi-Fi issue.

---

## 7. Manual test sequence

Run these in order.

### 7.1 Capture-only

```bash
cd /home/pi/BM_Devel_Pi

TEST_LOG="/home/pi/BM_Devel_Pi/cron_logs/manual_capture_$(date -u +%Y%m%dT%H%M%SZ).log"

timeout 180s /usr/bin/python3 -u main_pi_camera.py \
  --skip-time-window \
  --capture-backend libcamera \
  --output-size 2688x1512 \
  --heic-quality 20 \
  2>&1 | tee "$TEST_LOG"

echo "capture_exit_code=${PIPESTATUS[0]}"
echo "TEST_LOG=$TEST_LOG"
```

Expected:

```text
Transmit disabled; skipping compact wake telemetry send.
Image pipeline geometry: native=4608x2592 crop=(768,432,3072,1728) output=2688x1512
capture_exit_code=0
```

Capture-only intentionally reports:

```text
Compressed image size: 0 bytes
Buffers: 0
```

because no `--transmit` flag was provided.

### 7.2 Compression-only on the captured image

Set `LATEST_IMAGE` from the capture log:

```bash
LATEST_IMAGE="$(grep "Image pipeline output saved as" "$TEST_LOG" | tail -n 1 | sed -E "s/.*'([^']+)'.*/\1/")"
echo "$LATEST_IMAGE"
```

Run compression:

```bash
COMPRESS_LOG="/home/pi/BM_Devel_Pi/cron_logs/manual_compress_$(date -u +%Y%m%dT%H%M%SZ).log"

timeout 180s /usr/bin/python3 -u - <<PY 2>&1 | tee "$COMPRESS_LOG"
from pathlib import Path
from PIL import Image
import process_image_v2 as p

image_path = Path("$LATEST_IMAGE")
print("INPUT=", image_path, flush=True)

with Image.open(image_path) as im:
    print("SIZE=", im.size, flush=True)

p.apply_bm_serial_runtime_settings()
name, nbuf, nbytes = p.split_image_heic(str(image_path), image_quality=20)

print("HEIC=", name, flush=True)
print("HEIC_BYTES=", nbytes, flush=True)
print("BUFFERS=", nbuf, flush=True)
print("EST_MIN=", round(nbuf * 5 / 60, 2), flush=True)

if nbytes <= 0:
    raise RuntimeError("HEIC output was zero bytes")
if nbuf <= 0:
    raise RuntimeError("No buffers generated")

print("STABILIZED_COMPRESSION_OK=true", flush=True)
PY

echo "compress_exit_code=${PIPESTATUS[0]}"
echo "COMPRESS_LOG=$COMPRESS_LOG"
```

Expected:

```text
HEIC helper completed
Compressed image saved
Saved <N> buffer text files
STABILIZED_COMPRESSION_OK=true
compress_exit_code=0
```

Typical result:

```text
HEIC_BYTES: 25–35 KB
BUFFERS: 115–150
```

### 7.3 Full manual transmit

```bash
cd /home/pi/BM_Devel_Pi

TRANSMIT_LOG="/home/pi/BM_Devel_Pi/cron_logs/manual_transmit_$(date -u +%Y%m%dT%H%M%SZ).log"

timeout 1500s /usr/bin/python3 -u main_pi_camera.py \
  --transmit \
  --skip-time-window \
  --capture-backend libcamera \
  --output-size 2688x1512 \
  --heic-quality 20 \
  2>&1 | tee "$TRANSMIT_LOG"

echo "transmit_exit_code=${PIPESTATUS[0]}"
echo "TRANSMIT_LOG=$TRANSMIT_LOG"
```

Inspect:

```bash
grep -E "Sent compact telemetry|HEIC helper completed|Compressed image saved|Saved .*buffer|Starting transmission|Sent buffer|Finished transmission|END IMG|Execution Time|Traceback|ERROR|NameError|Timeout" "$TRANSMIT_LOG" | tail -n 160
```

Pass criteria:

```text
transmit_exit_code=0
HEIC helper completed
Saved buffer text files
Sent buffer <N> of <N>
Finished transmission
no Traceback
no zero-byte compressed HEIC
```

### 7.4 Three-cycle ship-candidate test

```bash
cd /home/pi/BM_Devel_Pi

for i in 1 2 3; do
  echo "===== TRANSMIT TEST $i / 3 ====="

  LOG="/home/pi/BM_Devel_Pi/cron_logs/ship_candidate_transmit_${i}_$(date -u +%Y%m%dT%H%M%SZ).log"

  timeout 1500s /usr/bin/python3 -u main_pi_camera.py \
    --transmit \
    --skip-time-window \
    --capture-backend libcamera \
    --output-size 2688x1512 \
    --heic-quality 20 \
    2>&1 | tee "$LOG"

  echo "exit_code=${PIPESTATUS[0]}"
  echo "LOG=$LOG"

  grep -E "HEIC helper completed|Compressed image saved|Saved .*buffer|Starting transmission|Sent buffer [0-9]+ of|Finished transmission|Execution Time|Traceback|ERROR|NameError|Timeout" "$LOG" | tail -n 120

  echo "Sleeping 60 seconds before next run..."
  sleep 60
done
```

Do not enable cron until manual runs pass.

---

## 8. Spotter/BM time sync

Manual time test:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u spotter_time_sync.py
```

Expected successful output includes:

```text
decoded Spotter UTC
set_system_clock: ok
time_source: spotter_utc
source_time: spotter
allowed=True or allowed=False depending on window
```

If using `--skip-time-window`, the app bypasses the Spotter UTC schedule gate. Do not use `--skip-time-window` in production cron.

---

## 9. Backend validation

After a transmit, backend/frontend visibility can lag by several minutes.

Successful reconstructed image diagnostics should show:

```text
Closed: yes
Expected chunks: <N>
Received chunks: <N>
Missing chunks: 0
Pillow OK: yes
Format: heic
```

Expected Nereus media metadata should include populated image size/upload fields and a display derivative, for example:

```text
Device: BMCAM_000
R2 Key: BMCAM_000/bm_sofar/...
Display Key: BMCAM_000/bm_sofar/.../display/...
File size: populated
Upload time: populated
Transfer rate: populated
```

Use the backend admin probes already used in this project for Sofar/BM discovery, poll-once, and message reconstruction.

---

## 10. Cron after manual validation

Only after manual transmit passes:

```bash
crontab -l 2>/dev/null | grep -v 'run_capture_cycle.sh' > /tmp/bmcam000_cron || true
echo '@reboot /usr/bin/flock -n /tmp/bmcam000_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh' >> /tmp/bmcam000_cron
crontab /tmp/bmcam000_cron
crontab -l
```

Then test the wrapper once manually:

```bash
cd /home/pi/BM_Devel_Pi && (
  /usr/bin/flock -n /tmp/bmcam000_capture.lock ./run_capture_cycle.sh &
  pid=$!
  sleep 2
  LOG="$(ls -t cron_logs/capture_cycle_*.log | head -1)"
  echo "Following $LOG"
  tail --pid=$pid -n 200 -f "$LOG"
)
```

---

## 11. Troubleshooting notes

### Capture-only shows compressed size 0

Expected if `--transmit` is not passed.

### `client_loop: send disconnect: Broken pipe`

If the Pi remains online and reconnects, this may be network/Tailscale. If a physical power cycle is needed, treat it as a hard system/camera/encoder fault.

### Zero-byte HEIC

Bad unless it is a temporary file from a failed/aborted test. Find them:

```bash
find /home/pi/BM_Devel_Pi/images -maxdepth 1 -name "*compressed.heic" -size 0 -print -ls
```

### 3072×1728 output

Do not use for field MVP on `bmcam000`. HEIC encode at this size caused hard-reset/wedge behavior during tests.

### libcamera metadata

Do not add `--metadata` to the critical libcamera capture command on this branch. It caused instability. Metadata restoration is the next branch.

### BM daemon

For this legacy runtime path, `bm-daemon.service` should be stopped/disabled during manual testing:

```bash
sudo systemctl stop bm-daemon.service 2>/dev/null || true
sudo systemctl disable bm-daemon.service 2>/dev/null || true
```

---

## 12. Known branch history / superseded docs

Older docs in this repo describe experiments with larger cellular chunks such as 900/960/980 bytes and 12–16 second pacing. Those tests are useful history, but they are **not** the current reef MVP path.

Current reef MVP uses:

```text
300-byte chunks
5-second chunk delay
0x02 cellular-only queue
2688×1512 HEIC Q20
```

`README_updated_large_cellular.md` should be treated as historical large-payload testing notes unless a new branch intentionally resumes large-message testing.

---

## 13. Next planned work

Do not add these to the current branch unless a blocker requires it.

1. Restore metadata safely without destabilizing capture/transmit.
2. Add SD-card size/free/used and local storage ring buffer.
3. Lock camera settings for field consistency:
   - exposure
   - gain
   - white balance
   - focus
   - image-processing stability
4. Later: clean v3 refactor into small modules.

See `NEXT_AGENT_SPEC_BM_REEF_MVP.md` for the next-agent handoff.
