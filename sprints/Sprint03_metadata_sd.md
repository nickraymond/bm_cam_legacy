# Next Agent Spec — BM Reef Camera MVP Improvements

## 0. Mission

You are working on the legacy Bristlemouth Raspberry Pi camera runtime for an underwater reef monitoring MVP.

The field goal is to capture a useful reef/reference-card image, compress it small enough for Bristlemouth/Sofar cellular transmission, send it to the Nereus backend, and preserve enough metadata for downstream QA, color correction, and future coral bleaching analysis.

This is an MVP field system. Prioritize stability, testability, and reversible changes over cleverness.

---

## 1. Current known-good branch context

Current working branch:

```text
sprint03-libcamera-crop-heic-upload-test
```

Current runtime path:

```text
/home/pi/repos/bm_cam_legacy      # Git checkout
/home/pi/BM_Devel_Pi              # copied runtime folder
```

Current field-test candidate:

```text
native capture:       4608×2592 JPEG via libcamera-still
fixed crop:           x=768, y=432, w=3072, h=1728
output:               2688×1512
HEIC quality:         Q20
HEIC encoder:         heic_encode_helper.py subprocess
BM route:             0x02 cellular-only
chunk size:           300 base64 chars
chunk pacing:         5 sec
```

Why this candidate was selected:

```text
3072×1728 gave more detail but HEIC encode caused hard reset / wedge behavior on bmcam000.
2688×1512 repeatedly encoded successfully with the helper subprocess.
300-byte chunks are conservative and match the known-good legacy path.
```

Known current limitations:

```text
Camera-frame metadata is incomplete.
libcamera --metadata was tried and caused instability.
SD-card health/ring-buffer are not implemented.
Camera controls are not locked.
The codebase still has legacy/pasta-code structure.
```

---

## 2. Development rules

Follow these rules unless the user explicitly overrides them.

```text
1. Preserve the current working capture/compress/transmit path.
2. Make small, surgical changes.
3. Test one variable at a time.
4. Keep old runtime backups before copying files to /home/pi/BM_Devel_Pi.
5. Check file size / diff direction after edits.
6. Run py_compile before camera tests.
7. Test capture-only, then compression-only, then transmit.
8. Do not enable cron until manual tests pass.
9. Ask for prior art/context instead of inventing a new system.
10. Do not chase rabbit holes during field-MVP stabilization.
```

Do not touch unless the task explicitly requires it:

```text
bm_serial.py protocol framing
send_buffers() chunk-loop behavior
300-byte chunking
START/chunk/END sequence
libcamera capture geometry
heic_encode_helper.py validated encode method
```

Do not reintroduce on the critical path:

```text
libcamera-still --metadata
3072×1728 production HEIC
large 900/960/980 byte chunks
global Picamera2/OpenCV imports for the libcamera path
bm-daemon during manual legacy-runtime tests
```

---

## 3. Priority 1 — Restore metadata safely

### Goal

Restore useful metadata to the backend/frontend without destabilizing capture/transmit.

### Important warning

Do **not** restore metadata by adding this to the critical capture command:

```text
libcamera-still --metadata ...
```

That path caused instability during testing.

### Safe first metadata set

Start with metadata already known from config/runtime, not camera-frame metadata:

```text
software_sha
hostname
device id if available
time source
UTC capture timestamp
local timezone
window start/end
image_pipeline enabled
capture backend requested/actual
native source width/height
source JPEG quality
crop x/y/w/h
output width/height
resample method
HEIC quality
raw JPEG bytes
HEIC bytes
base64 chars
buffer count
chunk size
transmit delay
BM network selector
transmit duration
CPU temp
success/failure status
error reason if any
```

### Acceptance criteria

```text
Manual transmit still completes.
Backend receives complete image.
Metadata sidecar exists next to image.
END IMG remains within message budget.
No hard reset.
No zero-byte HEIC.
```

### Suggested branch name

```text
sprint04-bmcam-metadata-restore-safe
```

---

## 4. Priority 2 — SD-card health and local storage guardrails

### Goal

Report disk capacity and prevent local image storage from growing forever.

### Add metadata first

Add read-only disk fields to sidecar and compact telemetry:

```text
sd_total_bytes
sd_used_bytes
sd_free_bytes
sd_used_pct
images_dir_bytes
buffer_dir_bytes
cron_logs_dir_bytes
zero_byte_heic_count
```

Use `shutil.disk_usage("/")` or equivalent. Do not shell out unless needed.

### Then add ring buffer

Ring-buffer rules:

```text
Never delete files from the active run.
Never delete software/config/log files.
Only delete old local image artifacts in /home/pi/BM_Devel_Pi/images.
Prefer deleting oldest complete image groups first.
Keep at least the newest N captures, configurable in YAML.
Also enforce a minimum free-space threshold.
Dry-run mode first.
```

Suggested YAML:

```yaml
storage:
  report_disk_health: true
  ring_buffer_enabled: false
  keep_latest_captures: 50
  min_free_bytes: 2000000000
```

### Suggested branch name

```text
sprint05-bmcam-storage-health-ring-buffer
```

---

## 5. Priority 3 — Lock camera settings

### Goal

Make field cameras consistent with each other for color correction and coral bleaching detection.

Add YAML support gradually. Start with focus/exposure, then white balance.

Target YAML:

```yaml
camera:
  exposure:
    shutter_us: 8000
    gain: 1.0
    exposure_mode: manual

  white_balance:
    mode: manual
    awb_red_gain: 1.8
    awb_blue_gain: 1.4

  focus:
    mode: manual
    lens_position: 0.5

  image_processing:
    hdr: false
    denoise: cdn_off
    sharpness: 1.0
    contrast: 1.0
    saturation: 1.0
    brightness: 0.0
```

Implementation guidance:

```text
Translate YAML to libcamera-still flags only after checking the installed libcamera-still --help on the Pi.
Apply one control group at a time.
Log the requested controls into sidecar metadata.
Do not combine this with metadata restore or storage ring-buffer work in the same branch.
```

Suggested branch name:

```text
sprint06-bmcam-camera-control-locks
```

---

## 6. Later — clean v3 refactor

Do not do this before the immediate feature branches unless the current wrapper blocks progress.

Proposed structure:

```text
BM_Devel_Pi/
  bmcam_pipeline_v3.py       # thin CLI/orchestrator
  bm_config.py               # YAML parsing only
  bm_time.py                 # Spotter UTC / RTC / system time
  bm_capture.py              # libcamera native capture
  bm_image_geometry.py       # crop/downsample
  bm_encode.py               # HEIC helper subprocess wrapper
  heic_encode_helper.py      # isolated HEIC encode
  bm_chunk.py                # base64/chunk helpers
  bm_transmit.py             # BM serial START/chunks/END
  bm_metadata.py             # sidecars / compact message fields
  bm_storage.py              # disk health / ring buffer
  bm_logging.py              # logs
```

Refactor strategy:

```text
Build side-by-side.
Do not delete old main_pi_camera.py.
Make v3 support only the new reef MVP path first.
Switch cron only after manual v3 tests pass.
```

---

## 7. Required test ladder for every branch

Before copying to runtime:

```bash
python3 -m py_compile BM_Devel_Pi/main_pi_camera.py BM_Devel_Pi/process_image_v2.py BM_Devel_Pi/heic_encode_helper.py BM_Devel_Pi/spotter_time_sync.py
```

On Pi after copying:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py heic_encode_helper.py spotter_time_sync.py bm_serial.py
```

Manual runtime ladder:

```text
1. preflight
2. capture-only
3. compression-only
4. one manual transmit
5. three-cycle manual transmit soak
6. cron only after manual soak
```

Pass criteria:

```text
exit_code=0
no reboot / no hard power cycle needed
no zero-byte compressed HEIC
HEIC helper completed
all expected buffers sent
backend reconstructs complete HEIC
frontend displays image
```

---

## 8. Useful commands

Preflight:

```bash
cd /home/pi/BM_Devel_Pi
hostname
uptime
vcgencmd get_throttled || true
vcgencmd measure_temp || true
df -h /
grep -E "MemAvailable|CmaTotal|CmaFree|SwapTotal|SwapFree" /proc/meminfo || true
pgrep -af "bm_daemon|bm-agent|bm_agent|main_pi_camera|libcamera-still|rpicam-still|heic_encode_helper" || echo "OK: no conflicting process"
```

Stop conflicting services for manual dev:

```bash
sudo systemctl stop bm-daemon.service 2>/dev/null || true
sudo systemctl disable bm-daemon.service 2>/dev/null || true
crontab -r 2>/dev/null || true
```

Capture-only:

```bash
/usr/bin/python3 -u main_pi_camera.py \
  --skip-time-window \
  --capture-backend libcamera \
  --output-size 2688x1512 \
  --heic-quality 20
```

Manual transmit:

```bash
timeout 1500s /usr/bin/python3 -u main_pi_camera.py \
  --transmit \
  --skip-time-window \
  --capture-backend libcamera \
  --output-size 2688x1512 \
  --heic-quality 20
```

Find zero-byte HEICs:

```bash
find /home/pi/BM_Devel_Pi/images -maxdepth 1 -name "*compressed.heic" -size 0 -print -ls
```

---

## 9. Definition of done for next branch

A branch is done when it has:

```text
README or patch note update
clear commit message
runtime copy instructions
py_compile pass
capture-only pass
compression-only pass
manual transmit pass
known limitations listed
rollback path documented
```

Keep the field MVP moving. Do not refactor for aesthetics while a concrete stability or metadata feature is still unshipped.
