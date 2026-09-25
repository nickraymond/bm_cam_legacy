#!/usr/bin/env python3
# filename: rc_telemetry.py
# description: Sprint26 S1 — logging, unit identity, storage health, and the compact uplink messages (<WS>, START/END fields, CSV log).
"""
Telemetry and message helpers for the RC runtime, moved verbatim out of
process_image_v2.py in Sprint26 S1 (tests/golden pins every byte they emit).

  debug_print()                    stdout (+ Spotter log when BM_CAMERA_LOG_TO_SPOTTER=1)
  get_hostname / get_software_sha / get_cpu_temperature   unit identity
  collect_storage_health()         SD usage + artifact directory sizes
  compact_kv_message / _clean_value / _num                <TAG k=v ...> builders
  send_compact_text_message()      one message on the shared port (bm_port.get())
  send_wake_status()               the <WS ...> heartbeat
  _build_end_image_message / _start_metadata_pairs        END / START metadata fields
  log_message()                    the CSV cycle log

Paths (IMAGE_DIRECTORY, BUFFER_DIRECTORY, LOG_FILE, SOFTWARE_*) are the deployed
runtime's (/home/pi/BM_Devel_Pi). BUFFER_DIRECTORY and zero_byte_heic_count are
HEIC-era storage fields still sent in START (`bf`, `zh`); DESIGN W1 drops them.
"""

import csv
import os
import shutil
import socket
import subprocess

import bm_port


# Debug flag to control printing of messages to the terminal
DEBUG = True


# Hard-coded image directory path
IMAGE_DIRECTORY = "/home/pi/BM_Devel_Pi/images"


BUFFER_DIRECTORY = "/home/pi/BM_Devel_Pi/buffer"


LOG_FILE = "/home/pi/BM_Devel_Pi/camera_log.csv"


# Runtime software identity.
# Production code is copied into /home/pi/BM_Devel_Pi, while git operations may
# happen in /home/pi/repos/bm_cam_legacy. Prefer explicit env/file, then repo SHA.
SOFTWARE_SHA_FILE = "/home/pi/BM_Devel_Pi/software_sha.txt"


SOFTWARE_REPO_PATH = "/home/pi/repos/bm_cam_legacy"


def debug_print(message):
    """Helper function to print debug messages if debugging is enabled.

    Keep debug logging side-effect free by default. During bmcam000 dev,
    capture-only and compression-only tests must not touch the BM bus. Set
    BM_CAMERA_LOG_TO_SPOTTER=1 only when explicit Spotter-side debug logging
    is needed.
    """
    if DEBUG:
        print(f"[DEBUG] {message}")

    if os.environ.get("BM_CAMERA_LOG_TO_SPOTTER") == "1":
        try:
            bm_port.get().spotter_log("camera_module.log", message)
        except Exception:
            # Debug logging must never break capture/compression/transmit.
            pass


def _directory_size_bytes(path):
    """Return best-effort recursive byte size for a local runtime directory.

    Read-only helper. Does not delete, create, or modify files.
    Symlinks are ignored to avoid accidentally walking outside runtime storage.
    """
    total = 0
    try:
        if not os.path.exists(path):
            return 0
        if os.path.isfile(path) and not os.path.islink(path):
            return os.path.getsize(path)
        for dirpath, dirnames, filenames in os.walk(path):
            # Do not follow symlinked directories.
            dirnames[:] = [
                name for name in dirnames
                if not os.path.islink(os.path.join(dirpath, name))
            ]
            for name in filenames:
                file_path = os.path.join(dirpath, name)
                try:
                    if not os.path.islink(file_path):
                        total += os.path.getsize(file_path)
                except OSError:
                    pass
    except Exception as exc:
        debug_print(f"Failed to compute directory size for {path}: {exc}")
    return int(total)


def _zero_byte_heic_count(images_directory=IMAGE_DIRECTORY):
    """Count zero-byte HEIC artifacts in the local images directory only."""
    count = 0
    try:
        if not os.path.isdir(images_directory):
            return 0
        for name in os.listdir(images_directory):
            if not name.lower().endswith(".heic"):
                continue
            path = os.path.join(images_directory, name)
            try:
                if os.path.isfile(path) and os.path.getsize(path) == 0:
                    count += 1
            except OSError:
                pass
    except Exception as exc:
        debug_print(f"Failed to count zero-byte HEIC files: {exc}")
    return int(count)


def collect_storage_health():
    """Return read-only SD-card/local artifact usage fields for metadata.

    This is intentionally reporting-only. It does not perform ring-buffer
    deletion and does not mutate local image, buffer, config, code, or log files.
    """
    root_path = "/"
    cron_logs_dir = "/home/pi/BM_Devel_Pi/cron_logs"

    total = used = free = None
    used_pct = None
    try:
        usage = shutil.disk_usage(root_path)
        total = int(usage.total)
        used = int(usage.used)
        free = int(usage.free)
        if total > 0:
            used_pct = round((used / total) * 100.0, 2)
    except Exception as exc:
        debug_print(f"Failed to read SD-card disk usage: {exc}")

    return {
        "sd_total_bytes": total,
        "sd_used_bytes": used,
        "sd_free_bytes": free,
        "sd_used_pct": used_pct,
        "images_dir_bytes": _directory_size_bytes(IMAGE_DIRECTORY),
        "buffer_dir_bytes": _directory_size_bytes(BUFFER_DIRECTORY),
        "cron_logs_dir_bytes": _directory_size_bytes(cron_logs_dir),
        "zero_byte_heic_count": _zero_byte_heic_count(IMAGE_DIRECTORY),
    }


def _num(value, digits=2):
    """Compact numeric formatting for telemetry fields."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
        if f.is_integer():
            return str(int(f))
        return f"{f:.{digits}f}".rstrip("0").rstrip(".")
    except Exception:
        return _clean_value(value, max_len=16)


def _metadata_first(metadata, *keys):
    for key in keys:
        if key in metadata and metadata.get(key) is not None:
            return metadata.get(key)
    return None


def _format_colour_gains(value):
    """Format Picamera2 ColourGains as compact r:b string."""
    if value is None:
        return None
    if isinstance(value, str):
        return _clean_value(value, max_len=18)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        r = _num(value[0], digits=2)
        b = _num(value[1], digits=2)
        if r is not None and b is not None:
            return f"{r}:{b}"
    return _clean_value(value, max_len=18)


def _capture_metadata_end_fields(capture_metadata):
    """Return compact END-message fields from Picamera2/libcamera metadata.

    These are intentionally short because END is still one BM message.
    Missing keys are skipped. Typical useful keys include:
      ExposureTime, AnalogueGain, DigitalGain, ColourGains,
      ColourTemperature, LensPosition, AfState, AfMode, FocusFoM, Lux.
    """
    m = capture_metadata or {}
    fields = []

    # Requested focus controls from YAML, used to compare command vs actual
    # libcamera-reported metadata in the backend/cycle-log view.
    requested_focus_mode = _metadata_first(m, "requested_focus_mode")
    requested_lens_position = _metadata_first(m, "requested_lens_position")

    requested_white_balance_mode = _metadata_first(m, "requested_white_balance_mode")
    requested_colour_gains = _metadata_first(m, "requested_colour_gains")
    requested_exposure_mode = _metadata_first(m, "requested_exposure_mode")
    requested_shutter_us = _metadata_first(m, "requested_shutter_us")
    requested_analogue_gain = _metadata_first(m, "requested_analogue_gain")
    et = _metadata_first(m, "ExposureTime")
    ag = _metadata_first(m, "AnalogueGain", "AnalogGain")
    dg = _metadata_first(m, "DigitalGain")
    cg = _metadata_first(m, "ColourGains", "ColorGains")
    cct = _metadata_first(m, "ColourTemperature", "ColorTemperature")
    lp = _metadata_first(m, "LensPosition")
    afs = _metadata_first(m, "AfState")
    afm = _metadata_first(m, "AfMode")
    ffom = _metadata_first(m, "FocusFoM")
    lux = _metadata_first(m, "Lux")
    fd = _metadata_first(m, "FrameDuration")
    stemp = _metadata_first(m, "SensorTemperature", "CameraTemperature", "Temperature")

    requested_focus_mode_text = None
    if requested_focus_mode not in (None, ""):
        requested_focus_mode_text = _clean_value(str(requested_focus_mode).strip().lower(), max_len=12)

    requested_white_balance_mode_text = None

    if requested_white_balance_mode not in (None, ""):

        requested_white_balance_mode_text = _clean_value(str(requested_white_balance_mode).strip().lower(), max_len=12)


    requested_exposure_mode_text = None

    if requested_exposure_mode not in (None, ""):

        requested_exposure_mode_text = _clean_value(str(requested_exposure_mode).strip().lower(), max_len=12)


    candidate_fields = [
        ("rfm", requested_focus_mode_text),
        ("rlp", _num(requested_lens_position, digits=3)),
        ("rwb", requested_white_balance_mode_text),
        ("rcg", _format_colour_gains(requested_colour_gains)),
        ("rem", requested_exposure_mode_text),
        ("rsh", _num(requested_shutter_us, digits=0)),
        ("rag", _num(requested_analogue_gain, digits=2)),
        ("et_us", _num(et, digits=0)),
        ("ag", _num(ag, digits=2)),
        ("dg", _num(dg, digits=2)),
        ("cg", _format_colour_gains(cg)),
        ("cct", _num(cct, digits=0)),
        ("lp", _num(lp, digits=2)),
        ("afs", _num(afs, digits=0)),
        ("afm", _num(afm, digits=0)),
        ("ffom", _num(ffom, digits=0)),
        ("lux", _num(lux, digits=1)),
        ("fd_us", _num(fd, digits=0)),
        ("stemp", _num(stemp, digits=1)),
    ]

    for key, value in candidate_fields:
        if value is not None and value != "na":
            fields.append((key, value))
    return fields


def _build_end_image_message(compressed_file_name, core_fields, capture_metadata=None, max_payload_bytes=295):
    """Build END IMG message with budgeted optional camera metadata fields."""
    fields = list(core_fields)
    optional = _capture_metadata_end_fields(capture_metadata)

    def render(pairs):
        return "<END IMG> " + ", ".join(f"{k}: {v}" for k, v in pairs) + "\n"

    selected = list(fields)
    for pair in optional:
        candidate = selected + [pair]
        if len(render(candidate).encode("ascii", errors="ignore")) <= max_payload_bytes:
            selected.append(pair)
        else:
            debug_print(f"Skipping END metadata field due to payload budget: {pair[0]}")

    return render(selected)


def get_hostname(max_len=24):
    """Return a compact hostname for telemetry messages."""
    try:
        hostname = socket.gethostname().strip()
    except Exception:
        hostname = "unknown"
    return _clean_value(hostname, max_len=max_len)


def get_software_sha():
    """Return the deployed software SHA.

    Priority:
      1. BM_CAM_SOFTWARE_SHA env var
      2. /home/pi/BM_Devel_Pi/software_sha.txt
      3. git SHA from /home/pi/repos/bm_cam_legacy
      4. unknown

    The production runtime folder does not need to be a git checkout.
    """
    env_sha = os.environ.get("BM_CAM_SOFTWARE_SHA", "").strip()
    if env_sha:
        return _clean_value(env_sha, max_len=12)

    try:
        if os.path.exists(SOFTWARE_SHA_FILE):
            with open(SOFTWARE_SHA_FILE, "r", encoding="utf-8") as f:
                file_sha = f.read().strip()
            if file_sha:
                return _clean_value(file_sha, max_len=12)
    except Exception as exc:
        debug_print(f"Failed reading software SHA file: {exc}")

    try:
        result = subprocess.run(
            ["git", "-C", SOFTWARE_REPO_PATH, "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        repo_sha = result.stdout.strip()
        if result.returncode == 0 and repo_sha:
            return _clean_value(repo_sha, max_len=12)
    except Exception as exc:
        debug_print(f"Failed reading git software SHA: {exc}")

    return "unknown"


def _clean_value(value, max_len=64):
    """Return a compact telemetry-safe ASCII-ish value.

    Avoid spaces/commas because the backend probe extracts simple key/value text.
    """
    if value is None:
        return "na"
    value = str(value).strip()
    if not value:
        return "na"
    value = value.replace(" ", "_").replace(",", "_").replace("\n", "_").replace("\r", "_")
    value = "".join(ch for ch in value if 32 <= ord(ch) <= 126)
    return value[:max_len] if len(value) > max_len else value


def _format_hhmm(value):
    """Compact HH:MM or ISO-like time to HHMM where possible."""
    if value is None:
        return "na"
    value = str(value).strip()
    # Full ISO local time: 2026-06-25T04:00:02-04:00 -> 0400
    if "T" in value and len(value) >= 16:
        return value[11:16].replace(":", "")
    # Window config: 12:00 -> 1200
    if len(value) >= 5 and value[2] == ":":
        return value[:5].replace(":", "")
    return _clean_value(value, max_len=8)


def compact_kv_message(prefix, fields, max_payload_bytes=280):
    """Build one compact telemetry message and keep it under the payload budget.

    This is used for the wake status heartbeat. It is intentionally short so it
    remains a single BM/Sofar message under the legacy ~300 byte practical limit.
    """
    ordered_parts = [f"{key}={_clean_value(value, max_len=48)}" for key, value in fields if value is not None]
    message = f"<{prefix} " + " ".join(ordered_parts) + ">\n"

    if len(message.encode("ascii", errors="ignore")) <= max_payload_bytes:
        return message

    # If the message is unexpectedly large, drop least-critical optional fields first.
    drop_keys = {"lt", "r", "hn"}
    compact_parts = [
        f"{key}={_clean_value(value, max_len=32)}"
        for key, value in fields
        if value is not None and key not in drop_keys
    ]
    message = f"<{prefix} " + " ".join(compact_parts) + ">\n"

    if len(message.encode("ascii", errors="ignore")) <= max_payload_bytes:
        return message

    # Final safety: shorten timezone and SHA before truncating. This should be rare.
    shorter_parts = []
    for key, value in fields:
        if value is None or key in drop_keys:
            continue
        max_len = 16
        if key == "sha":
            max_len = 8
        elif key == "tz":
            max_len = 24
        shorter_parts.append(f"{key}={_clean_value(value, max_len=max_len)}")
    message = f"<{prefix} " + " ".join(shorter_parts) + ">\n"

    encoded = message.encode("ascii", errors="ignore")
    if len(encoded) > max_payload_bytes:
        encoded = encoded[:max_payload_bytes - 2] + b">\n"
        message = encoded.decode("ascii", errors="ignore")

    return message


def send_compact_text_message(message):
    """Send one compact ASCII message over the existing Spotter transmit-data path."""
    payload = message.encode("ascii", errors="ignore")
    bm_port.get().spotter_tx(payload)
    debug_print(f"Sent compact telemetry message ({len(payload)} bytes): {message.strip()}")
    return len(payload)


def send_wake_status(
    action,
    timezone_name=None,
    local_time=None,
    window_start=None,
    window_end=None,
    image_res_key=None,
    image_quality=None,
    reason=None,
):
    """Send one compact wake heartbeat.

    Action codes:
      cap       = capture path allowed
      skip_win  = outside configured transmit window
      skip_err  = schedule/time/config error path
      skip_legacy = legacy local time window skipped capture

    This compact heartbeat remains intentionally small and should not be
    chunked. It is independent of the larger image BUFFER_SIZE used for
    cellular-only image transfer.
    """
    cpu_temp = None
    try:
        cpu_temp = f"{get_cpu_temperature():.1f}"
    except Exception as exc:
        debug_print(f"Failed to read CPU temp for wake status: {exc}")

    fields = [
        ("v", "1"),
        ("a", action),
        ("tz", timezone_name),
        ("lt", _format_hhmm(local_time)),
        ("ws", _format_hhmm(window_start)),
        ("we", _format_hhmm(window_end)),
        ("rk", image_res_key),
        ("q", image_quality),
        ("ct", cpu_temp),
        ("sha", get_software_sha()),
        ("hn", get_hostname()),
    ]
    if reason:
        fields.append(("r", reason))

    message = compact_kv_message("WS", fields)
    return send_compact_text_message(message)


def get_cpu_temperature():
    """Get the Raspberry Pi's CPU temperature."""
    result = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True)
    temp_str = result.stdout.strip().replace("temp=", "").replace("'C", "")
    return float(temp_str)


def _compact_unit_int(value, divisor):
    """Convert bytes to a compact integer unit for START metadata."""
    if value is None or value == "":
        return None
    try:
        return int(round(float(value) / float(divisor)))
    except Exception:
        return None


def _start_metadata_pairs(start_metadata):
    """Return compact top-level START IMG key/value pairs.

    These fields are transmitted in the START message so the backend can parse
    them from Sofar/BM message text. Keep names short because START is one
    unchunked BM message and must stay under the practical 300-byte limit.

    Storage fields:
      st = SD total MiB
      su = SD used MiB
      sf = SD free MiB
      sp = SD used percent
      im = images dir MiB
      bf = buffer dir KiB
      lg = cron_logs dir KiB
      zh = zero-byte HEIC count
    """
    if not start_metadata:
        return []

    mib = 1024 * 1024
    kib = 1024

    raw_pairs = [
        ("rk", start_metadata.get("image_res_key")),
        ("q", start_metadata.get("image_quality")),
        ("tz", start_metadata.get("timezone")),
        ("ws", _format_hhmm(start_metadata.get("window_start"))),
        ("we", _format_hhmm(start_metadata.get("window_end"))),
        ("sha", start_metadata.get("software_sha")),
        ("hn", start_metadata.get("hostname")),

        # Read-only storage health, compacted for BM payload budget.
        ("st", _compact_unit_int(start_metadata.get("sd_total_bytes"), mib)),
        ("su", _compact_unit_int(start_metadata.get("sd_used_bytes"), mib)),
        ("sf", _compact_unit_int(start_metadata.get("sd_free_bytes"), mib)),
        ("sp", _num(start_metadata.get("sd_used_pct"), digits=1)),
        ("im", _compact_unit_int(start_metadata.get("images_dir_bytes"), mib)),
        ("bf", _compact_unit_int(start_metadata.get("buffer_dir_bytes"), kib)),
        ("lg", _compact_unit_int(start_metadata.get("cron_logs_dir_bytes"), kib)),
        ("zh", start_metadata.get("zero_byte_heic_count")),
    ]

    pairs = []
    for key, value in raw_pairs:
        if value is None or value == "":
            continue
        pairs.append((key, value))
    return pairs


def log_message(
    rtc_time,
    compressed_image_filename,
    file_size_raw,
    file_size_compressed,
    image_quality,
    num_buffers,
    execution_time,
    within_window,
    cpu_temp,
):
    """Log details to the CSV file and print a concise log message to the terminal."""
    file_exists = os.path.isfile(LOG_FILE)

    with open(LOG_FILE, 'a', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow([
                "RTC Timestamp (UTC)",
                "Compressed Image Filename",
                "Raw File Size (bytes)",
                "Compressed File Size (bytes)",
                "Image Quality",
                "Number of Buffers",
                "Execution Time (minutes)",
                "Within Time Window",
                "CPU Temp (°C)",
            ])

        writer.writerow([
            rtc_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            compressed_image_filename,
            file_size_raw,
            file_size_compressed,
            image_quality,
            num_buffers,
            f"{execution_time:.2f}",
            within_window,
            f"{cpu_temp:.2f}",
        ])

    debug_print(f"Raw image size: {file_size_raw} bytes")
    debug_print(f"Image quality: {image_quality}")
    debug_print(f"Compressed image size: {file_size_compressed} bytes")
    debug_print(f"Buffers: {num_buffers}")
    debug_print(f"Execution Time: {execution_time:.2f} min")
    debug_print(f"Within Window: {within_window}")
    debug_print(f"CPU Temp: {cpu_temp:.2f}°C")
    debug_print(" ")
    debug_print(" ")
    debug_print(" ")
