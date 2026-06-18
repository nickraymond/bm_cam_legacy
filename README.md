# bm_cam_legacy

Legacy Bristlemouth Raspberry Pi camera runtime for `bmcam001`, `bmcam002`, and fresh development units.

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
7. Exits cleanly when outside the window.

This repo is **not** the newer Nereus agent stack and is **not** the newer experimental Bristlemouth camera daemon. It exists to preserve and duplicate the working legacy BM camera behavior.

---

## 2. Important production rule

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

## 3. Recommended repo structure

```text
bm_cam_legacy/
├── README.md
├── .gitignore
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

The minimum runtime files are:

```text
BM_Devel_Pi/main_pi_camera.py
BM_Devel_Pi/process_image_v2.py
BM_Devel_Pi/bm_serial.py
BM_Devel_Pi/spotter_time_sync.py
BM_Devel_Pi/camera_schedule.yaml
BM_Devel_Pi/run_capture_cycle.sh
BM_Devel_Pi/read_CPU_temp.py
```

---

## 4. Files that should not be committed

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

## 5. Production runtime behavior

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

## 6. Controlling the app with crontab

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

## 7. What `flock` and the lock file do

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

## 8. Logs

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

## 9. Editing the YAML config

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

## 10. Editing the capture/transmit window

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

## 11. Editing image resolution and compression

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

`image_quality` controls JPEG compression quality. Lower values make smaller files but reduce image quality. Higher values improve quality but increase transmit size.

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

## 12. Manual test commands

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

## 13. Updating bmcam002 from Git safely

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

## 14. Updating bmcam001 from Git safely

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

## 15. Fresh device setup

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

## 16. Updating this repo from Windows before committing

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
git commit -m "Add clean legacy BM camera production runtime"
```

Push:

```powershell
git push
```

Tag a known-good production state:

```powershell
git tag bmcam002-prod-v0.1
git push origin bmcam002-prod-v0.1
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

## 17. Backup and restore

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

## 18. Troubleshooting

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

## 19. Current known-good production baseline

Known-good baseline from `bmcam002`:

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

The potted `bmcam002` should be treated as the golden working production reference. Avoid making direct experimental changes to its runtime folder unless a backup has been created first.

