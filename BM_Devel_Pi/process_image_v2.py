# filename: process_image_v2.py
# description: RC support helpers — native capture watchdog, telemetry, metadata sidecars, BM serial handle.
#
# Sprint26 S1: the HEIC path (main_pi_camera.py, crop/HEIC helper processes,
# split_image_heic / send_buffers / compress_and_send_image, ~1,140 lines) was
# deleted; everything left here is reachable from the RC runtime
# (rc_progressive_jpeg, rc_video_tx, video_recorder). PIL is no longer imported
# here (it was only used by the HEIC path).

import csv
import json
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone

from bm_serial import BristlemouthSerial, load_bm_serial_config


# Bristlemouth serial is intentionally lazy-loaded.
# The libcamera/crop/HEIC dev path can run capture-only and compression-only
# tests without touching the BM bus. Instantiate BM serial only when an actual
# Spotter/BM message is sent or when transmit settings must be applied to the
# serial object.
bm = None


def _get_bm_serial():
    """Return the lazily-created Bristlemouth serial instance."""
    global bm
    if bm is None:
        bm = BristlemouthSerial()
    return bm

# Safe fallback values if camera_schedule.yaml is missing bm_serial settings.
# For production large-message cellular-only deployments, set these in YAML:
#
# bm_serial:
#   network_type: 0x02
#   image_buffer_size: 960
#   image_transmit_delay_seconds: 16
DEFAULT_BUFFER_SIZE = 300
DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS = 5.0

# Runtime values. These are refreshed from camera_schedule.yaml before each
# image compression/send cycle.
BUFFER_SIZE = DEFAULT_BUFFER_SIZE
IMAGE_TRANSMIT_DELAY_SECONDS = DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS

# Debug flag to control printing of messages to the terminal
DEBUG = True

# Hard-coded image directory path
IMAGE_DIRECTORY = "/home/pi/BM_Devel_Pi/images"
BUFFER_DIRECTORY = "/home/pi/BM_Devel_Pi/buffer"
LOG_FILE = "/home/pi/BM_Devel_Pi/camera_log.csv"
CAPTURE_METADATA_SUFFIX = ".capture_metadata.json"

# Runtime software identity.
# Production code is copied into /home/pi/BM_Devel_Pi, while git operations may
# happen in /home/pi/repos/bm_cam_legacy. Prefer explicit env/file, then repo SHA.
SOFTWARE_SHA_FILE = "/home/pi/BM_Devel_Pi/software_sha.txt"
SOFTWARE_REPO_PATH = "/home/pi/repos/bm_cam_legacy"


def _coerce_int_config(name, value, default, min_value=None, max_value=None):
    """Parse an integer config value with bounds and safe fallback."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except Exception:
        debug_print(f"Invalid bm_serial.{name}={value!r}; using default {default}")
        return default

    if min_value is not None and parsed < min_value:
        debug_print(f"bm_serial.{name}={parsed} below minimum {min_value}; using {min_value}")
        return min_value
    if max_value is not None and parsed > max_value:
        debug_print(f"bm_serial.{name}={parsed} above maximum {max_value}; using {max_value}")
        return max_value
    return parsed


def _coerce_float_config(name, value, default, min_value=None, max_value=None):
    """Parse a float config value with bounds and safe fallback."""
    if value is None:
        return default
    try:
        parsed = float(value)
    except Exception:
        debug_print(f"Invalid bm_serial.{name}={value!r}; using default {default}")
        return default

    if min_value is not None and parsed < min_value:
        debug_print(f"bm_serial.{name}={parsed} below minimum {min_value}; using {min_value}")
        return min_value
    if max_value is not None and parsed > max_value:
        debug_print(f"bm_serial.{name}={parsed} above maximum {max_value}; using {max_value}")
        return max_value
    return parsed


def apply_bm_serial_runtime_settings(configure_serial=False):
    """Load BM serial image-transfer settings from camera_schedule.yaml.

    The local deployment config block is:

    bm_serial:
      network_type: 0x02
      image_buffer_size: 960
      image_transmit_delay_seconds: 16

    network_type:
      0x01 / 1 = legacy sat/cell fallback queue
      0x02 / 2 = cellular-only queue for larger payload testing
    """
    global BUFFER_SIZE, IMAGE_TRANSMIT_DELAY_SECONDS

    cfg = load_bm_serial_config()

    # Keep the limits broad enough for development, but avoid accidental
    # pathological values if YAML is mistyped.
    BUFFER_SIZE = _coerce_int_config(
        "image_buffer_size",
        cfg.get("image_buffer_size"),
        DEFAULT_BUFFER_SIZE,
        min_value=1,
        max_value=1200,
    )
    IMAGE_TRANSMIT_DELAY_SECONDS = _coerce_float_config(
        "image_transmit_delay_seconds",
        cfg.get("image_transmit_delay_seconds"),
        DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS,
        min_value=0,
        max_value=120,
    )

    network_type = cfg.get("network_type")
    network_description = f"configured {network_type}" if network_type is not None else "default"
    network_value = network_type

    # BristlemouthSerial owns parsing/validation for network_type, but only
    # instantiate it when actually preparing to transmit. Compression-only tests
    # only need buffer size and delay.
    if configure_serial:
        serial = _get_bm_serial()
        try:
            serial.set_network_type(network_type)
        except Exception as exc:
            debug_print(
                f"Invalid bm_serial.network_type={network_type!r}; "
                f"keeping {serial.describe_network_type()}: {exc}"
            )
        network_value = serial.get_network_type_value()
        network_description = serial.describe_network_type()

    settings = {
        "network_type": network_value,
        "network_description": network_description,
        "image_buffer_size": BUFFER_SIZE,
        "image_transmit_delay_seconds": IMAGE_TRANSMIT_DELAY_SECONDS,
    }
    debug_print(
        "Runtime BM transfer settings: "
        f"network={settings['network_description']}; "
        f"image_buffer_size={settings['image_buffer_size']}; "
        f"image_transmit_delay_seconds={settings['image_transmit_delay_seconds']}"
    )
    return settings


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
            _get_bm_serial().spotter_log("camera_module.log", message)
        except Exception:
            # Debug logging must never break capture/compression/transmit.
            pass


def generate_filename():
    """Generate a filename in the format of ISO 8601 timestamp + image.jpg."""
    current_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return f"{current_timestamp}_image.jpg"


def _metadata_path_for_image(image_path):
    return f"{image_path}{CAPTURE_METADATA_SUFFIX}"


def _json_safe_metadata(value):
    """Return JSON-safe Picamera2 metadata values for sidecar storage."""
    if isinstance(value, dict):
        return {str(k): _json_safe_metadata(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_metadata(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return float(value)
    except Exception:
        return str(value)


def save_capture_metadata(image_path, metadata):
    """Save Picamera2 capture metadata next to the raw image for later transmit metadata."""
    if not metadata:
        return None
    path = _metadata_path_for_image(image_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_json_safe_metadata(metadata), f, sort_keys=True)
        return path
    except Exception as exc:
        debug_print(f"Failed to save capture metadata sidecar: {exc}")
        return None


def load_capture_metadata(image_path):
    """Load Picamera2 capture metadata sidecar if present."""
    path = _metadata_path_for_image(image_path)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        debug_print(f"Failed to load capture metadata sidecar: {exc}")
    return {}


def update_capture_metadata(image_path, metadata):
    """Merge safe runtime metadata into the existing local sidecar.

    This intentionally reuses the existing *.capture_metadata.json sidecar
    path. It does not call libcamera --metadata and does not affect capture,
    HEIC encoding, chunking, or UART framing.
    """
    if not metadata:
        return load_capture_metadata(image_path)

    existing = load_capture_metadata(image_path)
    if not isinstance(existing, dict):
        existing = {}

    safe_update = _json_safe_metadata(metadata)
    if isinstance(safe_update, dict):
        existing.update(safe_update)

    save_capture_metadata(image_path, existing)
    return existing

def _load_libcamera_metadata_json(metadata_path):
    """Load libcamera-still/rpicam-still --metadata JSON output.

    Best-effort only. Metadata improves reporting but must never break capture,
    crop/downsample, HEIC encoding, or transmit.
    """
    if not metadata_path:
        return {}
    try:
        if not os.path.exists(metadata_path):
            return {}
        with open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return _json_safe_metadata(data)

        if isinstance(data, list):
            for item in reversed(data):
                if isinstance(item, dict):
                    return _json_safe_metadata(item)

        debug_print(f"Unsupported libcamera metadata JSON shape in {metadata_path}: {type(data).__name__}")
    except Exception as exc:
        debug_print(f"Failed to load libcamera metadata JSON {metadata_path}: {exc}")
    return {}


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
    _get_bm_serial().spotter_tx(payload)
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


def _select_camera_command(capture_backend):
    """Return the rpicam/libcamera command to use for native full capture."""
    backend = (capture_backend or "auto").strip().lower()

    if backend in {"legacy", "picamera2"}:
        return None, "picamera2"

    if backend in {"auto", "rpicam"}:
        cmd = shutil.which("rpicam-still")
        if cmd:
            return cmd, "rpicam"
        if backend == "rpicam":
            debug_print("rpicam-still not found; falling back to libcamera-still if available")

    if backend in {"auto", "rpicam", "libcamera"}:
        cmd = shutil.which("libcamera-still")
        if cmd:
            return cmd, "libcamera"

    raise RuntimeError(
        "No supported camera command found. Expected rpicam-still or libcamera-still "
        f"for capture_backend={capture_backend!r}."
    )


# Native capture retry policy for bmcam000 MVP stability.
# libcamera-still/rpicam-still is already an external process, but it still
# needs a parent-process watchdog. If the camera app stalls, kill it, remove
# partial native files, wait, and retry instead of wedging the cycle.
CAPTURE_HELPER_TIMEOUT_SECONDS = 30
CAPTURE_HELPER_MAX_RETRIES = 3
CAPTURE_HELPER_RETRY_DELAY_SECONDS = 60


def _remove_capture_artifact(native_image_path):
    """Remove stale/partial native capture output after failed capture attempts."""
    try:
        if native_image_path and os.path.exists(native_image_path):
            os.remove(native_image_path)
            debug_print(f"Removed native capture artifact: {native_image_path}")
    except Exception as exc:
        debug_print(f"Failed to remove native capture artifact {native_image_path}: {exc}")


def _send_capture_status(action, error_code, attempt, max_attempts, native_image_path,
                         source_width=None, source_height=None, output_width=None,
                         output_height=None, jpeg_quality=None, wait_seconds=None,
                         duration_sec=None, return_code=None):
    """Best-effort parseable BM status for native capture retry/error handling.

    Message shape intentionally follows existing compact WS telemetry, e.g.:
      <WS v=1 a=err e=cap_timeout try=1 max=4 tmo=30 src=4608x2592 ...>

    This must never break capture/compression/transmit.
    """
    try:
        try:
            cpu_temp = f"{get_cpu_temperature():.1f}"
        except Exception:
            cpu_temp = "na"

        try:
            native_bytes = os.path.getsize(native_image_path) if native_image_path and os.path.exists(native_image_path) else None
        except Exception:
            native_bytes = None

        src = f"{source_width}x{source_height}" if source_width and source_height else None
        out = f"{output_width}x{output_height}" if output_width and output_height else None

        fields = [
            ("v", "1"),
            ("a", action),
            ("e", error_code),
            ("try", attempt),
            ("max", max_attempts),
            ("tmo", CAPTURE_HELPER_TIMEOUT_SECONDS),
            ("wait", wait_seconds),
            ("src", src),
            ("out", out),
            ("q", jpeg_quality),
            ("sz", native_bytes),
            ("dur", f"{duration_sec:.1f}" if duration_sec is not None else None),
            ("rc", return_code),
            ("ct", cpu_temp),
            ("sha", get_software_sha()),
            ("hn", get_hostname()),
        ]
        send_compact_text_message(compact_kv_message("WS", fields))
    except Exception as exc:
        debug_print(f"Failed to send capture status telemetry: {exc}")


def _run_camera_command_with_timeout(cmd, stdout_log, stderr_log, attempt_label):
    """Run the camera app with a hard timeout and append output to log files."""
    with open(stdout_log, "a", encoding="utf-8") as out, open(stderr_log, "a", encoding="utf-8") as err:
        out.write(f"\n--- {attempt_label} ---\n")
        err.write(f"\n--- {attempt_label} ---\n")
        out.flush()
        err.flush()
        started = time.monotonic()
        result = subprocess.run(
            cmd,
            stdout=out,
            stderr=err,
            text=True,
            timeout=CAPTURE_HELPER_TIMEOUT_SECONDS,
            check=False,
        )
        duration = time.monotonic() - started
    return result, duration


CAMERA_CONTROL_OPTIONS_WITH_VALUES = {
    "--autofocus-mode",
    "--lens-position",
    "--autofocus-range",
    "--autofocus-speed",
}


CAMERA_CONTROL_OPTIONS_WITH_VALUES.update({
    "--shutter",
    "--gain",
    "--ev",
    "--awb",
    "--awbgains",
    "--sharpness",
    "--contrast",
    "--brightness",
    "--saturation",
    "--denoise",
    "--hdr",
})


def _control_bool(value, default=False):
    """Parse YAML-ish booleans safely."""
    if value is None or value == "":
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(default)


def _focus_camera_controls_from_settings(settings):
    """Build libcamera focus CLI args and requested-control metadata.

    MVP: focus only. Exposure and white balance come later.
    """
    requested = {
        "camera_control_source": "image_pipeline.camera_controls",
        "camera_controls_enabled": False,
    }

    if not isinstance(settings, dict):
        return [], requested

    controls = settings.get("camera_controls")
    if not isinstance(controls, dict):
        return [], requested

    enabled = _control_bool(controls.get("enabled"), default=False)
    requested["camera_controls_enabled"] = enabled

    focus = controls.get("focus")
    if not isinstance(focus, dict):
        focus = {}

    focus_enabled = enabled and _control_bool(focus.get("enabled"), default=True)
    requested["requested_focus_enabled"] = focus_enabled

    args = []
    if not focus_enabled:
        return args, requested

    mode = str(focus.get("mode") or "").strip().lower()
    if mode:
        requested["requested_focus_mode"] = mode

    if mode in {"manual", "auto", "continuous"}:
        args.extend(["--autofocus-mode", mode])
    elif mode and mode not in {"default", "none", "null"}:
        debug_print(f"Unsupported focus.mode={mode!r}; not adding --autofocus-mode")

    lens_position = focus.get("lens_position")
    if lens_position not in (None, ""):
        try:
            lens_position_f = float(lens_position)
            requested["requested_lens_position"] = round(lens_position_f, 4)
            args.extend(["--lens-position", _num(lens_position_f, digits=4)])
        except Exception as exc:
            requested["requested_lens_position_error"] = str(exc)
            debug_print(f"Invalid focus.lens_position={lens_position!r}; ignoring: {exc}")

    focus_range = str(focus.get("range") or "").strip().lower()
    if focus_range:
        requested["requested_focus_range"] = focus_range
        if focus_range in {"normal", "macro", "full"}:
            args.extend(["--autofocus-range", focus_range])
        else:
            debug_print(f"Unsupported focus.range={focus_range!r}; ignoring")

    focus_speed = str(focus.get("speed") or "").strip().lower()
    if focus_speed:
        requested["requested_focus_speed"] = focus_speed
        if focus_speed in {"normal", "fast"}:
            args.extend(["--autofocus-speed", focus_speed])
        else:
            debug_print(f"Unsupported focus.speed={focus_speed!r}; ignoring")

    return args, requested


def _camera_controls_from_settings(settings):
    """Build libcamera CLI args for focus, white balance, exposure/gain, and image processing.

    Focus was proven first. This helper preserves that path and adds:
      - white_balance: requested mode + red/blue gains
      - exposure: requested shutter_us + analogue_gain
      - image_processing: denoise/sharpness/contrast/saturation/brightness/hdr

    Requested controls are saved to local sidecar metadata. Only the mission-
    critical requested focus/WB/exposure fields are packed into END metadata.
    """
    args, requested = _focus_camera_controls_from_settings(settings)

    if not isinstance(settings, dict):
        return args, requested

    controls = settings.get("camera_controls")
    if not isinstance(controls, dict):
        return args, requested

    enabled = _control_bool(controls.get("enabled"), default=False)
    requested["camera_controls_enabled"] = enabled
    if not enabled:
        return args, requested

    # -------------------------
    # White balance controls
    # -------------------------
    wb = controls.get("white_balance")
    if not isinstance(wb, dict):
        wb = {}

    wb_enabled = _control_bool(wb.get("enabled"), default=False)
    requested["requested_white_balance_enabled"] = wb_enabled

    if wb_enabled:
        wb_mode = str(wb.get("mode") or "").strip().lower()
        if wb_mode:
            requested["requested_white_balance_mode"] = wb_mode

        red_gain = wb.get("red_gain")
        blue_gain = wb.get("blue_gain")

        if red_gain not in (None, "") and blue_gain not in (None, ""):
            try:
                red_f = float(red_gain)
                blue_f = float(blue_gain)
                requested["requested_red_gain"] = round(red_f, 4)
                requested["requested_blue_gain"] = round(blue_f, 4)
                requested["requested_colour_gains"] = [round(red_f, 4), round(blue_f, 4)]

                # Raspberry Pi libcamera-apps generally use AWB custom/manual
                # gains via --awb custom --awbgains R,B.
                args.extend(["--awb", "custom"])
                args.extend(["--awbgains", f"{_num(red_f, digits=4)},{_num(blue_f, digits=4)}"])
            except Exception as exc:
                requested["requested_colour_gains_error"] = str(exc)
                debug_print(f"Invalid white_balance red/blue gains; ignoring: {exc}")
        elif wb_mode in {"auto", "daylight", "cloudy", "indoor", "fluorescent", "tungsten", "incandescent", "custom"}:
            args.extend(["--awb", wb_mode])
        elif wb_mode and wb_mode not in {"manual", "none", "null"}:
            debug_print(f"Unsupported white_balance.mode={wb_mode!r}; ignoring")

    # -------------------------
    # Exposure/gain controls
    # -------------------------
    exposure = controls.get("exposure")
    if not isinstance(exposure, dict):
        exposure = {}

    exposure_enabled = _control_bool(exposure.get("enabled"), default=False)
    requested["requested_exposure_enabled"] = exposure_enabled

    if exposure_enabled:
        exposure_mode = str(exposure.get("mode") or "").strip().lower()
        if exposure_mode:
            requested["requested_exposure_mode"] = exposure_mode

        shutter_us = exposure.get("shutter_us")
        if shutter_us not in (None, ""):
            try:
                shutter_i = int(float(shutter_us))
                if shutter_i > 0:
                    requested["requested_shutter_us"] = shutter_i
                    args.extend(["--shutter", str(shutter_i)])
            except Exception as exc:
                requested["requested_shutter_us_error"] = str(exc)
                debug_print(f"Invalid exposure.shutter_us={shutter_us!r}; ignoring: {exc}")

        analogue_gain = exposure.get("analogue_gain")
        if analogue_gain not in (None, ""):
            try:
                gain_f = float(analogue_gain)
                if gain_f > 0:
                    requested["requested_analogue_gain"] = round(gain_f, 4)
                    args.extend(["--gain", _num(gain_f, digits=4)])
            except Exception as exc:
                requested["requested_analogue_gain_error"] = str(exc)
                debug_print(f"Invalid exposure.analogue_gain={analogue_gain!r}; ignoring: {exc}")

        # EV compensation in stops (Sprint10 exp command; auto exposure
        # stays engaged — --ev biases it, unlike shutter/gain overrides).
        ev = exposure.get("ev")
        if ev not in (None, ""):
            try:
                ev_f = float(ev)
                requested["requested_ev"] = round(ev_f, 2)
                args.extend(["--ev", _num(ev_f, digits=2)])
            except Exception as exc:
                requested["requested_ev_error"] = str(exc)
                debug_print(f"Invalid exposure.ev={ev!r}; ignoring: {exc}")

    # -------------------------
    # Image-processing controls
    # -------------------------
    ip = controls.get("image_processing")
    if not isinstance(ip, dict):
        ip = {}

    ip_enabled = _control_bool(ip.get("enabled"), default=False)
    requested["requested_image_processing_enabled"] = ip_enabled

    if ip_enabled:
        scalar_options = [
            ("sharpness", "--sharpness", "requested_sharpness"),
            ("contrast", "--contrast", "requested_contrast"),
            ("saturation", "--saturation", "requested_saturation"),
            ("brightness", "--brightness", "requested_brightness"),
        ]

        for yaml_key, cli_flag, meta_key in scalar_options:
            value = ip.get(yaml_key)
            if value in (None, ""):
                continue
            try:
                value_f = float(value)
                requested[meta_key] = round(value_f, 4)
                args.extend([cli_flag, _num(value_f, digits=4)])
            except Exception as exc:
                requested[f"{meta_key}_error"] = str(exc)
                debug_print(f"Invalid image_processing.{yaml_key}={value!r}; ignoring: {exc}")

        denoise = ip.get("denoise")
        if denoise not in (None, ""):
            denoise_text = str(denoise).strip().lower()
            requested["requested_denoise"] = denoise_text
            args.extend(["--denoise", denoise_text])

        hdr = ip.get("hdr")
        if hdr not in (None, ""):
            # Leave HDR disabled unless explicitly requested. If unsupported,
            # the existing fallback will retry without camera controls.
            if isinstance(hdr, bool):
                requested["requested_hdr"] = hdr
                if hdr:
                    args.extend(["--hdr", "auto"])
            else:
                hdr_text = str(hdr).strip().lower()
                requested["requested_hdr"] = hdr_text
                if hdr_text not in {"false", "off", "none", "null", "0"}:
                    args.extend(["--hdr", hdr_text])

    return args, requested


def _without_camera_control_args(cmd):
    """Return camera command with focus-control options removed."""
    out = []
    skip_next = False
    for item in cmd:
        if skip_next:
            skip_next = False
            continue
        if item in CAMERA_CONTROL_OPTIONS_WITH_VALUES:
            skip_next = True
            continue
        out.append(item)
    return out


def _command_has_camera_control_args(cmd):
    return any(item in CAMERA_CONTROL_OPTIONS_WITH_VALUES for item in (cmd or []))


def _without_metadata_args(cmd):
    """Return camera command with --metadata <path> removed."""
    out = []
    skip_next = False
    for item in cmd:
        if skip_next:
            skip_next = False
            continue
        if item == "--metadata":
            skip_next = True
            continue
        out.append(item)
    return out


def _run_native_full_capture(command, native_image_path, source_width, source_height, jpeg_quality, log_prefix, settings=None):
    """Capture native/full-source JPEG with rpicam-still or libcamera-still.

    The camera app is already a subprocess, but on bmcam000 we observed native
    capture can occasionally stall when stress-testing repeated full cycles.
    Add the same safety pattern used for HEIC: timeout, cleanup, cooldown,
    retry, and parseable WS telemetry before giving up.
    """
    stdout_log = f"{log_prefix}.stdout.log"
    stderr_log = f"{log_prefix}.stderr.log"
    metadata_json_path = f"{log_prefix}.metadata.json"

    base_cmd = [
        command,
        "-n",
        "--timeout", "2000",
        "--width", str(source_width),
        "--height", str(source_height),
        "--quality", str(jpeg_quality),
        "--metadata", metadata_json_path,
    ]

    camera_control_args, requested_camera_controls = _camera_controls_from_settings(settings)
    if camera_control_args:
        base_cmd.extend(camera_control_args)
        debug_print(
            "Applying requested focus camera controls: "
            f"args={' '.join(camera_control_args)}; "
            f"requested={requested_camera_controls}"
        )

    base_cmd.extend(["-o", native_image_path])

    max_attempts = 1 + CAPTURE_HELPER_MAX_RETRIES
    last_error = None
    final_cmd = list(base_cmd)

    # Start fresh logs for this native capture.
    for path in (stdout_log, stderr_log):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass

    for attempt in range(1, max_attempts + 1):
        cmd = list(base_cmd)
        final_cmd = list(cmd)
        _remove_capture_artifact(native_image_path)
        try:
            if os.path.exists(metadata_json_path):
                os.remove(metadata_json_path)
                debug_print(f"Removed native metadata artifact: {metadata_json_path}")
        except Exception as exc:
            debug_print(f"Failed to remove native metadata artifact {metadata_json_path}: {exc}")

        debug_print(
            f"Running native capture command attempt {attempt}/{max_attempts}: "
            + " ".join(cmd)
        )

        try:
            result, duration = _run_camera_command_with_timeout(
                cmd,
                stdout_log,
                stderr_log,
                f"ATTEMPT {attempt}/{max_attempts}",
            )

            # Some older camera apps have option differences. Retry within this
            # attempt with progressively simpler commands so metadata support
            # cannot kill the branch unnecessarily.
            if result.returncode != 0:
                retry_variants = []

                if "-n" in cmd:
                    retry_variants.append((
                        [x for x in cmd if x != "-n"],
                        "WITHOUT -n",
                    ))

                if "--metadata" in cmd:
                    retry_variants.append((
                        _without_metadata_args(cmd),
                        "WITHOUT --metadata",
                    ))

                if "-n" in cmd and "--metadata" in cmd:
                    retry_variants.append((
                        _without_metadata_args([x for x in cmd if x != "-n"]),
                        "WITHOUT -n AND --metadata",
                    ))

                if camera_control_args and _command_has_camera_control_args(cmd):
                    retry_variants.append((
                        _without_camera_control_args(cmd),
                        "WITHOUT camera controls",
                    ))

                    if "--metadata" in cmd:
                        retry_variants.append((
                            _without_metadata_args(_without_camera_control_args(cmd)),
                            "WITHOUT camera controls AND --metadata",
                        ))

                seen = set()
                for retry_cmd, retry_label in retry_variants:
                    retry_key = tuple(retry_cmd)
                    if retry_key in seen:
                        continue
                    seen.add(retry_key)

                    final_cmd = list(retry_cmd)
                    debug_print(
                        f"Native capture command failed with exit {result.returncode}; "
                        f"retrying this attempt {retry_label}"
                    )
                    try:
                        result, duration = _run_camera_command_with_timeout(
                            retry_cmd,
                            stdout_log,
                            stderr_log,
                            f"ATTEMPT {attempt}/{max_attempts} RETRY {retry_label}",
                        )
                    except subprocess.TimeoutExpired as exc:
                        raise exc

                    if result.returncode == 0:
                        break

            if result.returncode == 0 and os.path.exists(native_image_path) and os.path.getsize(native_image_path) > 0:
                output_size = os.path.getsize(native_image_path)
                debug_print(
                    f"Native capture completed: output={native_image_path}, "
                    f"bytes={output_size}, duration_sec={duration:.2f}, "
                    f"attempt={attempt}/{max_attempts}"
                )
                if attempt > 1:
                    _send_capture_status(
                        action="rec",
                        error_code="cap",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        native_image_path=native_image_path,
                        source_width=source_width,
                        source_height=source_height,
                        jpeg_quality=jpeg_quality,
                        duration_sec=duration,
                    )
                return {
                    "capture_command": final_cmd,
                    "stdout_log": stdout_log,
                    "stderr_log": stderr_log,
                    "metadata_json": metadata_json_path if os.path.exists(metadata_json_path) else None,
                    "camera_control_args": camera_control_args,
                    "camera_control_args_used": _command_has_camera_control_args(final_cmd),
                    "camera_controls_fallback_used": bool(camera_control_args and not _command_has_camera_control_args(final_cmd)),
                    "requested_camera_controls": requested_camera_controls,
                }

            last_error = RuntimeError(
                f"Native capture failed or produced no valid image: "
                f"exit_code={result.returncode} attempt={attempt}/{max_attempts} "
                f"duration_sec={duration:.2f}"
            )
            debug_print(str(last_error))
            _remove_capture_artifact(native_image_path)
            _send_capture_status(
                action="err",
                error_code="cap_rc" if result.returncode != 0 else "cap_missing",
                attempt=attempt,
                max_attempts=max_attempts,
                native_image_path=native_image_path,
                source_width=source_width,
                source_height=source_height,
                jpeg_quality=jpeg_quality,
                duration_sec=duration,
                return_code=result.returncode,
            )

        except subprocess.TimeoutExpired as exc:
            duration = CAPTURE_HELPER_TIMEOUT_SECONDS
            last_error = exc
            debug_print(
                f"Native capture timeout attempt={attempt}/{max_attempts} "
                f"timeout_sec={CAPTURE_HELPER_TIMEOUT_SECONDS}"
            )
            _remove_capture_artifact(native_image_path)
            _send_capture_status(
                action="err",
                error_code="cap_timeout",
                attempt=attempt,
                max_attempts=max_attempts,
                native_image_path=native_image_path,
                source_width=source_width,
                source_height=source_height,
                jpeg_quality=jpeg_quality,
                duration_sec=duration,
            )

        if attempt < max_attempts:
            next_attempt = attempt + 1
            debug_print(
                f"Waiting {CAPTURE_HELPER_RETRY_DELAY_SECONDS}s before native capture retry "
                f"{next_attempt}/{max_attempts}"
            )
            _send_capture_status(
                action="retry",
                error_code="cap",
                attempt=next_attempt,
                max_attempts=max_attempts,
                native_image_path=native_image_path,
                source_width=source_width,
                source_height=source_height,
                jpeg_quality=jpeg_quality,
                wait_seconds=CAPTURE_HELPER_RETRY_DELAY_SECONDS,
            )
            time.sleep(CAPTURE_HELPER_RETRY_DELAY_SECONDS)

    _send_capture_status(
        action="fail",
        error_code="cap",
        attempt=max_attempts,
        max_attempts=max_attempts,
        native_image_path=native_image_path,
        source_width=source_width,
        source_height=source_height,
        jpeg_quality=jpeg_quality,
    )
    raise RuntimeError(
        f"Native capture failed after {max_attempts} attempts; last_error={last_error!r}. "
        f"See logs: {stdout_log}, {stderr_log}"
    )


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


def close_bm_serial():
    """Close the BM serial once complete, if it was ever opened."""
    global bm
    if bm is None:
        return 0
    try:
        bm.uart.close()
    finally:
        bm = None
    return 0
