# BM legacy time-source patch v0.1

This patch keeps the bmcam002 production capture/transmit code path, but makes the UTC time source configurable in `camera_schedule.yaml`.

## Why this exists

`bmcam002` can receive `spotter/utc-time` from the BM/Spotter serial bridge. `bmcam001` has older serial bridge firmware and does not reliably receive that topic, but it has a working Pi hardware RTC.

The capture decision is unchanged:

```text
get UTC time
→ convert UTC to configured local timezone
→ check transmit window
→ capture/transmit only if inside the window
```

Only the UTC source changes.

## Files in this zip

```text
BM_Devel_Pi/spotter_time_sync.py
BM_Devel_Pi/camera_schedule.yaml
BM_Devel_Pi/run_capture_cycle.sh
device_profiles/bmcam001/camera_schedule.yaml
device_profiles/bmcam002/camera_schedule.yaml
docs/README_TIME_SOURCE_PATCH.md
MANIFEST.txt
```

## Config options

Use Spotter/BM UTC:

```yaml
time_source: "spotter_utc"
```

Use the Pi hardware RTC:

```yaml
time_source: "rtc"

rtc:
  hwclock_path: "/usr/sbin/hwclock"
  set_system_clock_from_rtc: true
  require_plausible_after_utc: "2026-01-01T00:00:00+00:00"
```

## bmcam001 sudoers requirement

RTC mode runs `hwclock -s --utc` from user cron. Allow the `pi` user to run `hwclock` without a password:

```bash
sudo tee /etc/sudoers.d/bmcam-hwclock >/dev/null <<'EOF'
pi ALL=(root) NOPASSWD: /usr/sbin/hwclock
EOF

sudo chmod 440 /etc/sudoers.d/bmcam-hwclock
sudo visudo -cf /etc/sudoers.d/bmcam-hwclock
```

Expected:

```text
/etc/sudoers.d/bmcam-hwclock: parsed OK
```

## Apply to local repo on Windows

From PowerShell in the extracted zip folder:

```powershell
Copy-Item .\BM_Devel_Pi\spotter_time_sync.py C:\Users\nickr\GitHub\bm_cam_legacy\BM_Devel_Pi\ -Force
Copy-Item .\BM_Devel_Pi\camera_schedule.yaml C:\Users\nickr\GitHub\bm_cam_legacy\BM_Devel_Pi\ -Force
Copy-Item .\BM_Devel_Pi\run_capture_cycle.sh C:\Users\nickr\GitHub\bm_cam_legacy\BM_Devel_Pi\ -Force
mkdir C:\Users\nickr\GitHub\bm_cam_legacy\device_profiles\bmcam001 -Force
Copy-Item .\device_profiles\bmcam001\camera_schedule.yaml C:\Users\nickr\GitHub\bm_cam_legacy\device_profiles\bmcam001\ -Force
Copy-Item .\device_profiles\bmcam002\camera_schedule.yaml C:\Users\nickr\GitHub\bm_cam_legacy\device_profiles\bmcam002\ -Force
```

Then commit on your branch:

```powershell
cd C:\Users\nickr\GitHub\bm_cam_legacy
git status
git add .
git commit -m "Add configurable RTC time source for bmcam001"
git push origin bmcam001-rtc-time-source
```

## Deploy branch to bmcam001

```bash
cd /home/pi/repos/bm_cam_legacy
git fetch origin
git checkout bmcam001-rtc-time-source
git pull origin bmcam001-rtc-time-source

SRC="/home/pi/repos/bm_cam_legacy/BM_Devel_Pi"
DST="/home/pi/BM_Devel_Pi"

cp "$SRC/main_pi_camera.py" "$DST/"
cp "$SRC/process_image_v2.py" "$DST/"
cp "$SRC/bm_serial.py" "$DST/"
cp "$SRC/spotter_time_sync.py" "$DST/"
cp "$SRC/run_capture_cycle.sh" "$DST/"
cp "$SRC/read_CPU_temp.py" "$DST/" 2>/dev/null || true
cp "/home/pi/repos/bm_cam_legacy/device_profiles/bmcam001/camera_schedule.yaml" "$DST/camera_schedule.yaml"

chmod +x "$DST/run_capture_cycle.sh"
```

## Test on bmcam001 before installing cron

```bash
cd /home/pi/BM_Devel_Pi
/usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py bm_serial.py spotter_time_sync.py
/usr/bin/python3 -u spotter_time_sync.py
/usr/bin/python3 -u main_pi_camera.py --transmit
```

Expected `spotter_time_sync.py` output in RTC mode:

```text
time_source: rtc
source_time: rtc
set_system_clock: ok
local_time: ...
reason: Within transmit window ...
```

## Install bmcam001 boot cron after testing

```bash
crontab -l 2>/dev/null | grep -v 'run_capture_cycle.sh' > /tmp/bmcam001_cron || true
echo '@reboot /usr/bin/flock -n /tmp/bmcam001_capture.lock /home/pi/BM_Devel_Pi/run_capture_cycle.sh' >> /tmp/bmcam001_cron
crontab /tmp/bmcam001_cron
crontab -l
```
