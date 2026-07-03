# bm_cam_legacy

Legacy Bristlemouth Raspberry Pi camera runtime for `bmcam001`, `bmcam002`, and fresh development units.

**Major release note:** current large-payload runtime requires updated Bristleback/mote `serial_bridge` firmware. The new image transfer path uses Spotter cellular-only BM messages and is not compatible with older mote firmware.

This repo preserves the known-good legacy camera application used on the potted Bristlemouth camera modules. The current production pattern is intentionally conservative: the Git repo is used as the source of truth, but the deployed runtime on each Pi lives in a separate folder so that a working potted device is not accidentally broken by Git operations.

---

## 1. Purpose of this repo

This repo is for the legacy Raspberry Pi Bristlemouth camera app that:

1. Boots with the Pi.
2. Runs one capture cycle using cron `@reboot`.
3. Uses Spotter/Bristlemouth UTC time as the trusted time source.
4. Converts UTC to a configured local timezone.
5. Checks a configured local capture/transmit window.
6. Captures and transmits an image only if the local time is inside the allowed window.
7. Sends image chunks over the cellular-only BM transmit path using the updated mote firmware.
8. Exits cleanly when outside the window.

This repo is **not** the newer Nereus agent stack and is **not** the newer experimental Bristlemouth camera daemon. It exists to preserve and duplicate the working legacy BM camera behavior.

---

## 2. Breaking change: large cellular-only BM image payloads

This release is a **breaking change** for Bristlemouth camera modules.

The runtime now sends image chunks through the Spotter `spotter/transmit-data`
**cellular-only** path instead of the older legacy sat/cell fallback path. It also
uses larger image payload chunks.

Current validated settings:

```text
BM transmit route: cellular-only
Spotter transmit-data selector: 0x02
Image BUFFER_SIZE: 980 base64 characters per image chunk
Image START/chunk pacing: 12 seconds between BM transmit-data writes
Known-good test device: bmcam000
Known-good Spotter: SPOT-31593C
Known-good BM node: 0x49cfe4d7cceb2771
```

The old runtime used smaller `BUFFER_SIZE = 300` chunks and the legacy network
selector `0x01`. The new `BUFFER_SIZE = 980` image chunks require updated mote
firmware with the newer Bristlemouth serial bridge / cellular-only transmit
support. Do **not** deploy this runtime to a camera whose mote firmware has not
been updated.

Practical result from bench testing:

```text
BUFFER_SIZE = 900    worked end-to-end with 12 second pacing
BUFFER_SIZE = 980    worked as the high-water candidate with 12 second pacing
BUFFER_SIZE = 1000   was too large / not reliable
```

Expected Spotter console evidence for the new path:

```text
Added message(...) to queue MS_Q_CELLULAR_ONLY
Submitted spotter/transmit-data message to cell-only queue, Len: ...
```

Do not rely on the orange cellular LED as the only success indicator. During
testing, cellular-only BM payloads reached the backend/frontend even when the
orange LED did not visibly flash.

Rollback point:

```text
If large cellular-only transfer fails:
1. Restore bm_serial.py to the legacy selector b"\x01".
2. Restore process_image_v2.py BUFFER_SIZE to 300.
3. Restore image transfer delay to 5 seconds if needed.
4. Re-run a 480p low-quality manual image test.
```

---

## 3. Mote firmware update required

The updated runtime requires a newer Bristlemouth mote / Bristleback
`serial_bridge` firmware image. Store the firmware binary in the repo under:

```text
mote_code/
```

Expected DFU firmware file format:

```text
*.elf.dfu.bin
```

Example file used during validation:

```text
mote_code/bm_mote_bristleback_v1_0-serial_bridge-dbg.elf.dfu.bin
```

Do not use the plain `.elf`, `.hex`, or non-DFU `.bin` file for this Spotter/Ebox
SD-card based update flow.

### 3.1 Copy the mote firmware to the Ebox SD card

1. Remove the Ebox / Spotter SD card.
2. Copy the DFU binary to the top level of the SD card.
3. Eject the card cleanly.
4. Reinstall it in the Ebox.
5. Open the Spotter / Ebox serial console.

Example filename:

```text
bm_mote_bristleback_v1_0-serial_bridge-dbg.elf.dfu.bin
```

### 3.2 Identify the mote node ID

Run this on the Spotter / Ebox console:

```text
bm topo
```

Example topology from the validated dev setup:

```text
Bristlemouth topology: 0e582dd12c1e1480 | 49cfe4d7cceb2771
```

Then inspect each node:

```text
bm info 49cfe4d7cceb2771
bm info 0e582dd12c1e1480
```

Example target mote / serial bridge node:

```text
Node ID: 49cfe4d7cceb2771
VersionStr: serial_bridge@ENG-v0.13.3-1-g31f3beb7+ec5dc832
```

Example bridge node that should **not** be flashed with mote firmware:

```text
Node ID: 0e582dd12c1e1480
VersionStr: bridge@v0.13.11
```

Critical rule:

```text
Flash the Bristleback / mote / serial_bridge node.
Do not flash the Spotter bridge node.
```

### 3.3 Run DFU from the Spotter / Ebox console

Template:

```text
bridge dfu <firmware_file.elf.dfu.bin> 0x<TARGET_MOTE_NODE_ID> 300000 force
```

Validated example:

```text
bridge dfu bm_mote_bristleback_v1_0-serial_bridge-dbg.elf.dfu.bin 0x49cfe4d7cceb2771 300000 force
```

Use `300000` ms as the timeout because mote DFU can take several minutes.

Expected success output:

```text
[BM_DFU] [INFO] Transfer complete!
[BM_DFU] [INFO] File transferred, entering update phase.
[BRIDGE_SYS] [INFO] Neighbor 49cfe4d7cceb2771 added
[BM_DFU] [INFO] Node 49cfe4d7cceb2771 update status: 1, 0
Update finished: 49cfe4d7cceb2771 success: 1 err:0
```

After DFU, verify:

```text
bm info 49cfe4d7cceb2771
```

Confirm the node still reports as `serial_bridge` and that the version/Git SHA
matches the firmware you intended to deploy.

---

## 4. Large cellular-only runtime settings

The Pi runtime changes for the large-message release are intentionally small.

In `BM_Devel_Pi/bm_serial.py`, the Spotter transmit-data network selector should
be cellular-only:

```python
SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK = b"\x01"
SPOTTER_NETWORK_CELLULAR_ONLY = b"\x02"
SPOTTER_TRANSMIT_NETWORK_TYPE = SPOTTER_NETWORK_CELLULAR_ONLY
```

The selector byte is the byte immediately after the topic `spotter/transmit-data`.
Do **not** confuse it with the BM serial publish header:

```python
bytearray.fromhex("0101")
```

In `BM_Devel_Pi/process_image_v2.py`, the validated image transfer settings are:

```python
BUFFER_SIZE = 980
IMAGE_TRANSMIT_DELAY_SECONDS = 12
```

`BUFFER_SIZE` is the number of base64 characters per image chunk before the
`<I#>` wrapper and newline are added. At `BUFFER_SIZE = 980`, the Spotter console
typically shows a BM transmit payload length near `986` bytes for full chunks.

The 12-second delay is a pacing workaround for the Spotter cellular-only queue.
Without pacing, the Spotter may accept some large chunks and then reject later
chunks with:

```text
Queue MS_Q_CELLULAR_ONLY is full.
Unable to submit message to cell-only queue
```

If this happens, the backend may show a closed but partial image with missing
chunks. During testing, a missing pattern such as `2, 5, 8, 11, 14` indicated
queue backpressure rather than random RF loss.

---

## 5. Spotter UTC / BM subscription test

Before testing image upload, verify that the Pi can subscribe to Spotter UTC over
BM Serial. This confirms the UART path and the `spotter/utc-time` subscription
service are working.

Run on the Pi:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u spotter_time_sync.py
```

Expected successful output includes:

```text
[SYNC] opening UART port=/dev/ttyAMA0 baudrate=115200
[SYNC] sending official BM_SERIAL_SUB for spotter/utc-time
[SYNC] listening up to 60s for Spotter UTC...
[SYNC] decoded Spotter UTC: 2026-07-02T17:01:26.597000+00:00
set_system_clock: ok
time_source: spotter_utc
source_time: spotter
allowed=True
```

If the script works, the Pi has proven:

```text
Pi UART -> Bristlemouth serial bridge -> Spotter UTC topic -> Pi clock update
```

If it fails:

```text
1. Confirm /dev/ttyAMA0 exists.
2. Confirm camera_schedule.yaml uses uart_port: "/dev/ttyAMA0" and baudrate: 115200.
3. Confirm the mote is visible with bm topo from the Spotter console.
4. Confirm the mote is running the updated serial_bridge firmware.
5. Confirm the Spotter RTC is valid.
```

Useful Spotter console checks:

```text
bm topo
bm info <mote_node_id>
post
```

`post` should show `bridgeErrorState: OK`. For cellular upload tests,
`cellularErrorState` and `cellularSignalErrorState` should also be `OK`.

GPS note: `gpsErrorState: NO_SIGNAL` can affect normal Sofar `sensor-data`
visibility during indoor testing, but it does not by itself prove that the
Bristlemouth UART or cellular-only queue failed.

---

## 6. Manual camera upload tests

Use these tests after the mote firmware has been updated, the Pi runtime files
have been copied into `/home/pi/BM_Devel_Pi`, and Spotter UTC sync is working.

### 6.1 Fast low-quality cellular-only image test

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit --resolution-key 480p --image-quality 10
```

### 6.2 Standard development image test

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit --resolution-key 480p --image-quality 20
```

### 6.3 Bypass time window for bench testing only

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit --resolution-key 480p --image-quality 20 --skip-time-window
```

Do not use `--skip-time-window` in production cron.

### 6.4 Expected Pi-side output

Look for:

```text
[DEBUG] Runtime resolution_key: 480p
[DEBUG] Runtime image_quality: 20
[DEBUG] Schedule source_time: spotter
[DEBUG] Schedule set_system_clock: ok
[DEBUG] Compressed image saved as '..._compressed.heic'
[DEBUG] Starting transmission of image: ...
[DEBUG] Sent buffer 1 of ...
[DEBUG] Finished transmission of image: ...
```

With the large-message release, the transmit start debug line should include:

```text
buffer_size=980; delay_sec=12
```

### 6.5 Expected Spotter console output

For the new cellular-only path, look for:

```text
Added message(...) to queue MS_Q_CELLULAR_ONLY
Submitted spotter/transmit-data message to cell-only queue, Len: ...
Sending Cellular message to Notecard.
Queuing message ...
```

For full-size chunks near the current high-water mark, expected values are
approximately:

```text
BM_TX Len: ~986
MS queue len: ~1112
```

If you see this, the Pi is sending faster than the cellular-only queue can drain:

```text
Queue MS_Q_CELLULAR_ONLY is full.
Unable to submit message to cell-only queue
```

Increase `IMAGE_TRANSMIT_DELAY_SECONDS` or reduce `BUFFER_SIZE`.

### 6.6 Backend / frontend validation

The normal delay from Pi transmit to backend/frontend visibility can be several
minutes. During testing, a lag around 10 minutes was observed between capture and
backend receipt.

Expected Nereus media metadata for a successful large-message upload:

```text
Device: BMCAM_000
R2 Key: BMCAM_000/bm_sofar/...
Display Key: BMCAM_000/bm_sofar/.../display/...
File size: populated
Upload time: populated
Transfer rate: populated
```

If using the admin ingest tools, useful checks are:

```bash
curl -s "$API_BASE/admin/ingest/sofar-discover?external_system_id=SPOT-31593C&hours=1&token_env_var=SOFAR_API_TOKEN_BM_REEF" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

```bash
curl -s -X POST "$API_BASE/admin/ingest/sofar-poll-once?external_system_id=SPOT-31593C&hours=1&commit=true&max_images=5" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

For partial/missing-chunk debugging:

```bash
curl -s "$API_BASE/admin/ingest/sofar-message-probe?external_system_id=SPOT-31593C&hours=2&external_node_id=0x49cfe4d7cceb2771&max_images_per_node=10&max_messages_per_image=120&reconstruct_images=true&parse_image_metadata=true" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

Successful complete image diagnostics should show:

```text
Closed: yes
Expected chunks: <N>
Received chunks: <N>
Missing chunks: 0
Pillow OK: yes
Format: heic
```

---

## 7. Recommended major-release checklist

Use this checklist before tagging the large cellular-only release.

```text
[ ] Updated mote DFU binary committed under mote_code/
[ ] README documents DFU steps and target node precautions
[ ] bm_serial.py defaults to cellular-only selector 0x02
[ ] process_image_v2.py uses BUFFER_SIZE = 980
[ ] process_image_v2.py uses IMAGE_TRANSMIT_DELAY_SECONDS = 12
[ ] spotter_time_sync.py succeeds on the target camera
[ ] Manual 480p image test reaches Spotter MS_Q_CELLULAR_ONLY
[ ] No queue-full errors during the test
[ ] Backend shows complete_image_count >= 1
[ ] Frontend displays the uploaded image
[ ] Rollback path to 0x01 / 300 byte chunks is understood
```

Suggested tag name:

```bash
git tag bmcam-large-cellular-v1.0.0
git push origin bmcam-large-cellular-v1.0.0
```

---

## 8. Important production rule

Do **not** make the live production folder on a potted device into a Git working tree.

Use this separation:

```text
/home/pi/repos/bm_cam_legacy      # Git checkout / source-controlled repo
/home/pi/BM_Devel_Pi              # Production runtime folder used by cron
```

On production devices, Git operations happen in:

```bash
/home/pi/repos/bm_cam_legacy
```

The app actually runs from:

```bash
/home/pi/BM_Devel_Pi
```

This keeps the known-good production runtime isolated from accidental Git merges, checkout changes, or partial updates.

---

## 9. Recommended repo structure

```text
bm_cam_legacy/
├── README.md
├── .gitignore
├── mote_code/
│   └── bm_mote_bristleback_v1_0-serial_bridge-dbg.elf.dfu.bin
├── BM_Devel_Pi/
│   ├── main_pi_camera.py
│   ├── process_image_v2.py
│   ├── bm_serial.py
│   ├── spotter_time_sync.py
│   ├── camera_schedule.yaml
│   ├── run_capture_cycle.sh
│   ├── read_CPU_temp.py
│   └── README_BMCAM002_SCHEDULE_PATCH.md
├── device_profiles/
│   ├── bmcam001/
│   │   ├── camera_schedule.yaml
│   │   ├── crontab.txt
│   │   └── NOTES.md
│   └── bmcam002/
│       ├── camera_schedule.yaml
│       ├── crontab.txt
│       └── NOTES.md
├── requirements/
│   ├── requirements.freeze.txt
│   ├── python-version.txt
│   ├── apt-manual.txt
│   └── system-info.txt
├── scripts/
│   ├── collect_production_snapshot.sh
│   ├── deploy_runtime.sh
│   └── install_legacy_bmcam.sh
└── docs/
    ├── bmcam001_setup.md
    ├── bmcam002_setup.md
    └── fresh_device_setup.md
```

The minimum Pi runtime files are:

```text
BM_Devel_Pi/main_pi_camera.py
BM_Devel_Pi/process_image_v2.py
BM_Devel_Pi/bm_serial.py
BM_Devel_Pi/spotter_time_sync.py
BM_Devel_Pi/camera_schedule.yaml
BM_Devel_Pi/run_capture_cycle.sh
BM_Devel_Pi/read_CPU_temp.py
```

The `mote_code/` folder is not copied into `/home/pi/BM_Devel_Pi`; it is used for the Spotter/Ebox SD-card DFU process described above.

---

## 10. Files that should not be committed

Do not commit logs, image captures, buffers, secrets, or Tailscale state.

Recommended `.gitignore`:

```gitignore
__pycache__/
*.pyc
*.pyo

cron_logs/
*.log

buffer/
buffers/
capture_archive/
images/
test_logs/

*.jpg
*.jpeg
*.png
*.heic
*.h264
*.mp4
*.csv

.env
*.key
*.pem
id_rsa*
id_ed25519*

tailscaled.state
```

---

## 11. Production runtime behavior

The production boot flow is:

```text
Pi power cycles
→ cron runs @reboot command once
→ flock prevents duplicate capture cycles
→ run_capture_cycle.sh starts
→ wrapper creates a timestamped log file
→ wrapper waits for startup settle time
→ wrapper runs Python syntax check
→ wrapper runs main_pi_camera.py --transmit
→ main app loads camera_schedule.yaml
→ app requests Spotter UTC from spotter/utc-time over BM UART
→ app sets/checks system time if configured
→ app converts UTC to configured local timezone
→ app checks transmit_window
→ if outside window: skip capture/transmit and exit 0
→ if inside window: capture image, compress, transmit, and exit
```

The important command inside the wrapper is:

```bash
/usr/bin/python3 -u main_pi_camera.py --transmit
```

Do not use this in production unless doing an intentional manual bypass test:

```bash
/usr/bin/python3 -u main_pi_camera.py --transmit --skip-time-window
```

The `--skip-time-window` flag bypasses the Spotter UTC time gate.

---

## 12. Controlling the app with crontab

The production cron entry is installed for the `pi` user, not root.

Open the crontab:

```bash
crontab -e
```

If prompted for an editor, choose `nano`. To force nano:

```bash
EDITOR=nano crontab -e
```

Production `@reboot` entry for `bmcam002`:

```cron
@reboot /usr/bin/flock -n /tmp/bmcam002_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh
```

Production `@reboot` entry for `bmcam001`:

```cron
@reboot /usr/bin/flock -n /tmp/bmcam001_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh
```

Generic form for a fresh device:

```cron
@reboot /usr/bin/flock -n /tmp/<device_id>_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh
```

Save in nano:

```text
Ctrl+O
Enter
Ctrl+X
```

Confirm the installed crontab:

```bash
crontab -l
```

Back up the crontab before editing:

```bash
crontab -l > /home/pi/crontab_backup_$(date +%Y%m%d_%H%M%S).txt 2>/dev/null || true
```

---

## 13. What `flock` and the lock file do

Example:

```cron
@reboot /usr/bin/flock -n /tmp/bmcam002_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh
```

Breakdown:

```text
@reboot                         Run once when the Pi boots.
/usr/bin/flock                  Use Linux flock to prevent duplicate runs.
-n                              Non-blocking. If another run has the lock, exit immediately.
/tmp/bmcam002_capture.lock      Lock file used by flock.
/home/pi/BM_Devel_Pi/run_capture_cycle.sh
                                Script to run if the lock is available.
```

The lock file is not the important part by itself. The active lock is held by the running process. If the process exits or crashes, Linux releases the lock.

This prevents two capture/transmit cycles from running at the same time.

---

## 14. Logs

The wrapper creates one log file **per call** of `run_capture_cycle.sh`.

That means:

```text
cron @reboot runs wrapper once → one log file for that reboot
manual wrapper test             → one new log file for that manual call
```

Logs are stored in:

```bash
/home/pi/BM_Devel_Pi/cron_logs/
```

List newest logs:

```bash
ls -lt /home/pi/BM_Devel_Pi/cron_logs | head
```

View the newest log:

```bash
tail -n 200 "$(ls -t /home/pi/BM_Devel_Pi/cron_logs/capture_cycle_*.log | head -1)"
```

Live-follow the newest log:

```bash
LOG="$(ls -t /home/pi/BM_Devel_Pi/cron_logs/capture_cycle_*.log | head -1)"; echo "Following $LOG"; tail -n 200 -f "$LOG"
```

Run the wrapper and live-follow the new log in one command:

```bash
cd /home/pi/BM_Devel_Pi && ( /usr/bin/flock -n /tmp/bmcam002_capture.lock ./run_capture_cycle.sh & pid=$!; sleep 2; LOG="$(ls -t cron_logs/capture_cycle_*.log | head -1)"; echo "Following $LOG"; tail --pid=$pid -n 200 -f "$LOG" )
```

For `bmcam001`, change the lock file:

```bash
cd /home/pi/BM_Devel_Pi && ( /usr/bin/flock -n /tmp/bmcam001_capture.lock ./run_capture_cycle.sh & pid=$!; sleep 2; LOG="$(ls -t cron_logs/capture_cycle_*.log | head -1)"; echo "Following $LOG"; tail --pid=$pid -n 200 -f "$LOG" )
```

---

## 15. Editing the YAML config

The production config file is:

```bash
/home/pi/BM_Devel_Pi/camera_schedule.yaml
```

Open it with:

```bash
nano /home/pi/BM_Devel_Pi/camera_schedule.yaml
```

Save in nano:

```text
Ctrl+O
Enter
Ctrl+X
```

Example config:

```yaml
timezone_preset: "sf"
timezone: "America/Los_Angeles"

enforce_spotter_time_window: true

transmit_window:
  start: "08:00"
  end: "15:00"

set_system_clock_from_spotter: true
spotter_time_timeout_seconds: 60
allow_system_clock_fallback: false

uart_port: "/dev/ttyAMA0"
baudrate: 115200

image:
  resolution_key: "720p"
  image_quality: 25
```

---

## 16. Editing the capture/transmit window

The time window is local time in the configured timezone.

Example San Francisco development window:

```yaml
transmit_window:
  start: "08:00"
  end: "15:00"
```

Example wide-open manual test window:

```yaml
transmit_window:
  start: "00:00"
  end: "23:59"
```

Example production reef window:

```yaml
transmit_window:
  start: "12:00"
  end: "15:00"
```

After editing, test the time gate:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u spotter_time_sync.py
```

Expected outside-window result:

```text
allowed=False
source_time: spotter
reason: Outside transmit window ...
```

Expected inside-window result:

```text
allowed=True
source_time: spotter
reason: Within transmit window ...
```

---

## 17. Editing image resolution and compression

Image settings live in `camera_schedule.yaml`:

```yaml
image:
  resolution_key: "720p"
  image_quality: 25
```

`resolution_key` controls image size. Common values used in this project include:

```text
480p
720p
```

`image_quality` controls encoder quality. Lower values make smaller files with more compression and lower visual quality. Higher values improve quality but increase transmit size. The current HEIC transmit path uses the same convention.

Typical development setting:

```yaml
image:
  resolution_key: "480p"
  image_quality: 20
```

Typical field setting:

```yaml
image:
  resolution_key: "720p"
  image_quality: 25
```

After editing, run:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit
```

The app should print the runtime config it is using, for example:

```text
[DEBUG] Runtime resolution_key: 720p
[DEBUG] Runtime image_quality: 25
```

---

## 18. Manual test commands

The major-release cellular-only test workflow is documented in the breaking-change sections above. The commands below are the general legacy production/runtime checks.

Check Spotter UTC and schedule decision:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u spotter_time_sync.py
```

Run the production app once:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit
```

Run the production wrapper once:

```bash
/usr/bin/flock -n /tmp/bmcam002_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh
```

Run the production wrapper once and live-print logs:

```bash
cd /home/pi/BM_Devel_Pi && ( /usr/bin/flock -n /tmp/bmcam002_capture.lock ./run_capture_cycle.sh & pid=$!; sleep 2; LOG="$(ls -t cron_logs/capture_cycle_*.log | head -1)"; echo "Following $LOG"; tail --pid=$pid -n 200 -f "$LOG" )
```

Bypass the time window for intentional manual development testing only:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit --resolution-key 480p --image-quality 20 --skip-time-window
```

Do not use `--skip-time-window` in production cron.

---

## 19. Updating bmcam002 from Git safely

This is the safe production update workflow for `bmcam002`.

SSH into the camera:

```powershell
ssh pi@bmcam002
```

Or:

```powershell
ssh pi@bmcam002.tail079031.ts.net
```

On the Pi, clone the repo if needed:

```bash
mkdir -p /home/pi/repos
cd /home/pi/repos

git clone https://github.com/nickraymond/bm_cam_legacy.git
cd bm_cam_legacy
```

If the repo already exists:

```bash
cd /home/pi/repos/bm_cam_legacy
git fetch origin
git checkout main
git pull origin main
```

Back up the current production runtime:

```bash
cd /home/pi
tar -czf BM_Devel_Pi_backup_bmcam002_$(date -u +%Y%m%dT%H%M%SZ).tgz BM_Devel_Pi
crontab -l > crontab_backup_bmcam002_$(date -u +%Y%m%dT%H%M%SZ).txt 2>/dev/null || true
```

Copy approved runtime files from Git checkout into production:

```bash
SRC="/home/pi/repos/bm_cam_legacy/BM_Devel_Pi"
DST="/home/pi/BM_Devel_Pi"

mkdir -p "$DST"

cp "$SRC/main_pi_camera.py" "$DST/"
cp "$SRC/process_image_v2.py" "$DST/"
cp "$SRC/bm_serial.py" "$DST/"
cp "$SRC/spotter_time_sync.py" "$DST/"
cp "$SRC/camera_schedule.yaml" "$DST/"
cp "$SRC/run_capture_cycle.sh" "$DST/"
cp "$SRC/read_CPU_temp.py" "$DST/" 2>/dev/null || true

chmod +x "$DST/run_capture_cycle.sh"
```

If you want to use the bmcam002-specific profile schedule instead of the default repo schedule:

```bash
cp /home/pi/repos/bm_cam_legacy/device_profiles/bmcam002/camera_schedule.yaml /home/pi/BM_Devel_Pi/camera_schedule.yaml
```

Install or refresh the crontab:

```bash
crontab -l 2>/dev/null | grep -v 'run_capture_cycle.sh' > /tmp/bmcam002_cron || true
echo '@reboot /usr/bin/flock -n /tmp/bmcam002_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh' >> /tmp/bmcam002_cron
crontab /tmp/bmcam002_cron
crontab -l
```

Test syntax:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py spotter_time_sync.py bm_serial.py
```

Test schedule:

```bash
/usr/bin/python3 -u spotter_time_sync.py
```

Test app:

```bash
/usr/bin/python3 -u main_pi_camera.py --transmit
```

Test wrapper/logging:

```bash
cd /home/pi/BM_Devel_Pi && ( /usr/bin/flock -n /tmp/bmcam002_capture.lock ./run_capture_cycle.sh & pid=$!; sleep 2; LOG="$(ls -t cron_logs/capture_cycle_*.log | head -1)"; echo "Following $LOG"; tail --pid=$pid -n 200 -f "$LOG" )
```

---

## 20. Updating bmcam001 from Git safely

The process is the same as `bmcam002`, but use the `bmcam001` profile and lock file.

SSH into the camera:

```powershell
ssh pi@bmcam001
```

Or use the full Tailscale DNS name if needed.

On the Pi:

```bash
mkdir -p /home/pi/repos
cd /home/pi/repos

if [ ! -d bm_cam_legacy ]; then
  git clone https://github.com/nickraymond/bm_cam_legacy.git
fi

cd /home/pi/repos/bm_cam_legacy
git fetch origin
git checkout main
git pull origin main
```

Back up current runtime:

```bash
cd /home/pi
tar -czf BM_Devel_Pi_backup_bmcam001_$(date -u +%Y%m%dT%H%M%SZ).tgz BM_Devel_Pi
crontab -l > crontab_backup_bmcam001_$(date -u +%Y%m%dT%H%M%SZ).txt 2>/dev/null || true
```

Copy runtime files:

```bash
SRC="/home/pi/repos/bm_cam_legacy/BM_Devel_Pi"
DST="/home/pi/BM_Devel_Pi"

mkdir -p "$DST"

cp "$SRC/main_pi_camera.py" "$DST/"
cp "$SRC/process_image_v2.py" "$DST/"
cp "$SRC/bm_serial.py" "$DST/"
cp "$SRC/spotter_time_sync.py" "$DST/"
cp "$SRC/camera_schedule.yaml" "$DST/"
cp "$SRC/run_capture_cycle.sh" "$DST/"
cp "$SRC/read_CPU_temp.py" "$DST/" 2>/dev/null || true

chmod +x "$DST/run_capture_cycle.sh"
```

Apply bmcam001-specific config if present:

```bash
if [ -f /home/pi/repos/bm_cam_legacy/device_profiles/bmcam001/camera_schedule.yaml ]; then
  cp /home/pi/repos/bm_cam_legacy/device_profiles/bmcam001/camera_schedule.yaml /home/pi/BM_Devel_Pi/camera_schedule.yaml
fi
```

Install or refresh the crontab:

```bash
crontab -l 2>/dev/null | grep -v 'run_capture_cycle.sh' > /tmp/bmcam001_cron || true
echo '@reboot /usr/bin/flock -n /tmp/bmcam001_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh' >> /tmp/bmcam001_cron
crontab /tmp/bmcam001_cron
crontab -l
```

Test:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py spotter_time_sync.py bm_serial.py
/usr/bin/python3 -u spotter_time_sync.py
/usr/bin/python3 -u main_pi_camera.py --transmit
```

---

## 21. Fresh device setup

This is the high-level procedure for a new Pi, such as `bmcam005`.

1. Flash Raspberry Pi OS.
2. Enable SSH.
3. Set hostname.
4. Join Wi-Fi or Ethernet.
5. Install Tailscale if remote access is needed.
6. Clone this repo into `/home/pi/repos/bm_cam_legacy`.
7. Copy runtime files into `/home/pi/BM_Devel_Pi`.
8. Edit `camera_schedule.yaml`.
9. Install `@reboot` cron.
10. Run syntax, Spotter time, app, and wrapper tests.

Example:

```bash
sudo hostnamectl set-hostname bmcam005
sudo reboot
```

After reconnecting:

```bash
mkdir -p /home/pi/repos
cd /home/pi/repos
git clone https://github.com/nickraymond/bm_cam_legacy.git
cd bm_cam_legacy
```

Deploy runtime:

```bash
mkdir -p /home/pi/BM_Devel_Pi
cp -r /home/pi/repos/bm_cam_legacy/BM_Devel_Pi/* /home/pi/BM_Devel_Pi/
chmod +x /home/pi/BM_Devel_Pi/run_capture_cycle.sh
```

Edit config:

```bash
nano /home/pi/BM_Devel_Pi/camera_schedule.yaml
```

Install cron:

```bash
crontab -l 2>/dev/null | grep -v 'run_capture_cycle.sh' > /tmp/bmcam005_cron || true
echo '@reboot /usr/bin/flock -n /tmp/bmcam005_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh' >> /tmp/bmcam005_cron
crontab /tmp/bmcam005_cron
crontab -l
```

Test:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py spotter_time_sync.py bm_serial.py
/usr/bin/python3 -u spotter_time_sync.py
/usr/bin/python3 -u main_pi_camera.py --transmit
```

---

## 22. Updating this repo from Windows before committing

Local Windows repo path:

```powershell
C:\Users\nickr\GitHub\bm_cam_legacy
```

Check status:

```powershell
cd C:\Users\nickr\GitHub\bm_cam_legacy
git status
```

Review changes:

```powershell
git diff
```

Add files:

```powershell
git add .
```

Commit:

```powershell
git commit -m "Release large cellular-only BM camera payload runtime"
```

Push:

```powershell
git push
```

Tag a known-good production state:

```powershell
git tag bmcam-large-cellular-v1.0.0
git push origin bmcam-large-cellular-v1.0.0
```

Check final status:

```powershell
git status
```

Expected:

```text
nothing to commit, working tree clean
```

---

## 23. Backup and restore

Create a local backup on a Pi:

```bash
cd /home/pi
tar -czf BM_Devel_Pi_PROD_$(hostname)_$(date -u +%Y%m%dT%H%M%SZ).tgz BM_Devel_Pi
crontab -l > crontab_PROD_$(hostname)_$(date -u +%Y%m%dT%H%M%SZ).txt 2>/dev/null || true
```

Restore a production runtime backup:

```bash
cd /home/pi
mv BM_Devel_Pi BM_Devel_Pi_broken_$(date -u +%Y%m%dT%H%M%SZ)
tar -xzf BM_Devel_Pi_PROD_<device>_<timestamp>.tgz
```

Restore crontab:

```bash
crontab crontab_PROD_<device>_<timestamp>.txt
crontab -l
```

---

## 24. Troubleshooting

### Check if cron installed

```bash
crontab -l
```

### Check newest logs

```bash
ls -lt /home/pi/BM_Devel_Pi/cron_logs | head
```

### Live-follow newest log

```bash
LOG="$(ls -t /home/pi/BM_Devel_Pi/cron_logs/capture_cycle_*.log | head -1)"; echo "Following $LOG"; tail -n 200 -f "$LOG"
```

### Check if app is still running

```bash
ps aux | grep -E 'run_capture_cycle|main_pi_camera|spotter_time_sync' | grep -v grep
```

### Check UART device

```bash
ls -la /dev/ttyAMA0
```

### Run syntax check

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py spotter_time_sync.py bm_serial.py
```

### Check Spotter UTC

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u spotter_time_sync.py
```

### Check production app behavior

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit
```

---

## 25. Current known-good production baseline

Known-good legacy production baseline:

```text
Runtime folder: /home/pi/BM_Devel_Pi
UART: /dev/ttyAMA0
Baudrate: 115200
Time source: Spotter/BM topic spotter/utc-time
Cron mode: @reboot
Time behavior: fail closed if Spotter UTC unavailable and fallback disabled
Production command: /usr/bin/python3 -u main_pi_camera.py --transmit
Wrapper: /home/pi/BM_Devel_Pi/run_capture_cycle.sh
```

Known-good large cellular-only payload baseline from `bmcam000`:

```text
Spotter: SPOT-31593C
Mapped Nereus device: BMCAM_000
BM node: 0x49cfe4d7cceb2771
Mote firmware: updated Bristleback serial_bridge firmware via DFU
bm_serial.py route: cellular-only selector 0x02
process_image_v2.py BUFFER_SIZE: 980
process_image_v2.py image transmit delay: 12 seconds
Validated result: image reached Nereus backend/frontend
Observed latency: several minutes between capture/transmit and backend receipt
```

The potted `bmcam001` / `bmcam002` devices should be treated carefully when moving to this release because it is a breaking firmware/runtime change. Back up the production runtime first, update the mote firmware, verify Spotter UTC subscription, then deploy the Pi runtime files.



---

## YAML-controlled BM serial transport testing

The large cellular-only release makes the key transport settings configurable in
`camera_schedule.yaml` so bench testing does not require editing Python files.

Add or update this section in `/home/pi/BM_Devel_Pi/camera_schedule.yaml`:

```yaml
bm_serial:
  # 0x01 = legacy sat/cell fallback queue, observed as MS_Q_LEGACY.
  # 0x02 = cellular-only queue, observed as MS_Q_CELLULAR_ONLY.
  network_type: 0x02

  # Base64 image chunk payload size before the <I#> wrapper and newline.
  # 300 is the old legacy-safe value. Larger values require updated mote
  # serial_bridge firmware and cellular-only routing.
  image_buffer_size: 960

  # Delay after START and after every image chunk. Increase this if the
  # Spotter reports MS_Q_CELLULAR_ONLY queue-full errors or the backend shows
  # structured missing chunks.
  image_transmit_delay_seconds: 16
```

Supported network selector values in YAML:

```text
0x01, 1, "legacy", "fallback", "cellular_iri_fallback"
0x02, 2, "cellular_only", "cell_only", "cellular"
```

Observed behavior on `bmcam000` / `SPOT-31593C`:

```text
b"\x01" -> MS_Q_LEGACY / sat-cell fallback queue
b"\x02" -> MS_Q_CELLULAR_ONLY / cellular-only queue
```

For testing, change only one variable at a time. Recommended sequence:

```text
300 bytes, 5-12s delay, 0x02  # cellular-only control
900 bytes, 12-16s delay, 0x02 # conservative large-payload test
960 bytes, 16s delay, 0x02    # current reduced large-payload test
980 bytes, 16s delay, 0x02    # high-water mark; verify carefully
```

Run a syntax check after copying updated files:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py spotter_time_sync.py bm_serial.py
```

Run a small manual upload test:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit --resolution-key 480p --image-quality 10 --skip-time-window
```

Run a larger comparison test:

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -u main_pi_camera.py --transmit --resolution-key 720p --image-quality 25 --skip-time-window
```

Expected Spotter-side success for cellular-only:

```text
[MS] [INFO] Added message(... len: ...) to queue MS_Q_CELLULAR_ONLY
[BM_TX] [INFO] Submitted spotter/transmit-data message to cell-only queue
```

Backend/logger success criteria:

```text
Closed: yes
Expected chunks == Received chunks
Missing chunks: 0
Pillow OK: yes
```

If missing chunks appear in a structured pattern, the queue is likely being
outpaced. Increase `image_transmit_delay_seconds` or reduce
`image_buffer_size`.
