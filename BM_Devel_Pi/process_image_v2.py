# filename: process_image_v2.py
# description: all the support methods to take picture, compress, and send over BM

import base64
import csv
import gc
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

from PIL import Image

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

# Encoder image quality.
# This is not "compression amount".
# Convention: lower = smaller file / more compression / lower visual quality.
# higher = larger file / less compression / higher visual quality.
IMAGE_QUALITY = 25
COMPRESSION_QUALITY = IMAGE_QUALITY  # Backward-compatible alias for older log/code references.

RESOLUTION_KEY = "720p"

# Available resolution options for IMX708 / Raspberry Pi Camera Module 3-style captures.
RESOLUTIONS = {
    # 16:9 presets
    "native_12mp": (4608, 2592),
    "12MP": (4608, 2592),
    "4k": (3840, 2160),
    "2.7k": (2704, 1520),
    "1296p": (2304, 1296),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "480p": (854, 480),
    "360p": (640, 360),

    # 4:3 presets
    "4_3_full_crop": (3456, 2592),
    "4_3_8mp": (3264, 2448),
    "8MP": (3264, 2448),
    "4_3_5mp": (2592, 1944),
    "5MP": (2592, 1944),
    "4_3_3mp": (2048, 1536),
    "4_3_2mp": (1600, 1200),
    "4_3_1080": (1440, 1080),
    "XGA": (1024, 768),
    "SVGA": (800, 600),
    "VGA": (640, 480),
}


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


def validate_resolution(resolution_key):
    """Validate the resolution key and return the corresponding resolution."""
    if resolution_key not in RESOLUTIONS:
        raise ValueError(f"Invalid resolution key. Choose from: {', '.join(RESOLUTIONS.keys())}")
    return RESOLUTIONS[resolution_key]


def validate_image_quality(image_quality):
    """Validate encoder image quality.

    0 = smallest/lowest quality; 100 = largest/highest quality.
    """
    image_quality = int(image_quality)
    if not 0 <= image_quality <= 100:
        raise ValueError("image_quality must be between 0 and 100")
    return image_quality


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



def _pipeline_bool(value, default=False):
    """Return a forgiving boolean for YAML/CLI-derived image pipeline values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _pipeline_int(settings, key, default, min_value=None, max_value=None):
    value = settings.get(key, default) if isinstance(settings, dict) else default
    try:
        parsed = int(value)
    except Exception:
        debug_print(f"Invalid image_pipeline.{key}={value!r}; using default {default}")
        parsed = int(default)
    if min_value is not None and parsed < min_value:
        debug_print(f"image_pipeline.{key}={parsed} below minimum {min_value}; using {min_value}")
        parsed = min_value
    if max_value is not None and parsed > max_value:
        debug_print(f"image_pipeline.{key}={parsed} above maximum {max_value}; using {max_value}")
        parsed = max_value
    return parsed


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


def _crop_helper_path():
    """Return the colocated lightweight crop/downsample helper script path."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "crop_downsample_helper.py")


# Crop/downsample helper retry policy for bmcam000 MVP stability.
# This step opens the native 4608x2592 JPEG, crops, resizes, and writes the final
# JPEG. On Pi Zero 2W this can be a high-memory PIL/libjpeg step, so isolate it
# exactly like native capture and HEIC encode.
CROP_HELPER_TIMEOUT_SECONDS = 45
CROP_HELPER_MAX_RETRIES = 3
CROP_HELPER_RETRY_DELAY_SECONDS = 60


def _crop_tmp_path_for_output(final_image_path):
    base, ext = os.path.splitext(final_image_path)
    return f"{base}.tmp{ext}"


def _remove_crop_artifacts(final_image_path):
    """Remove final/temp crop outputs after failed or timed-out attempts."""
    for path in (_crop_tmp_path_for_output(final_image_path), final_image_path):
        try:
            if os.path.exists(path):
                os.remove(path)
                debug_print(f"Removed crop/downsample artifact: {path}")
        except Exception as exc:
            debug_print(f"Failed to remove crop/downsample artifact {path}: {exc}")


def _send_crop_status(
    action,
    error_code,
    attempt,
    max_attempts,
    native_image_path,
    final_image_path,
    output_width=None,
    output_height=None,
    jpeg_quality=None,
    wait_seconds=None,
    output_bytes=None,
    duration_sec=None,
    return_code=None,
):
    """Best-effort parseable BM status for crop/downsample retry handling."""
    try:
        try:
            cpu_temp = f"{get_cpu_temperature():.1f}"
        except Exception:
            cpu_temp = "na"

        try:
            native_bytes = os.path.getsize(native_image_path) if native_image_path and os.path.exists(native_image_path) else None
        except Exception:
            native_bytes = None

        out = f"{output_width}x{output_height}" if output_width and output_height else None

        fields = [
            ("v", "1"),
            ("a", action),
            ("e", error_code),
            ("try", attempt),
            ("max", max_attempts),
            ("tmo", CROP_HELPER_TIMEOUT_SECONDS),
            ("wait", wait_seconds),
            ("out", out),
            ("q", jpeg_quality),
            ("sz", native_bytes),
            ("jsz", output_bytes),
            ("dur", f"{duration_sec:.1f}" if duration_sec is not None else None),
            ("rc", return_code),
            ("ct", cpu_temp),
            ("sha", get_software_sha()),
            ("hn", get_hostname()),
        ]
        send_compact_text_message(compact_kv_message("WS", fields))
    except Exception as exc:
        debug_print(f"Failed to send crop/downsample status telemetry: {exc}")


def _log_crop_progress_tail(progress_log_path, max_lines=20):
    """Best-effort log tail for crop helper progress JSONL."""
    try:
        if not progress_log_path or not os.path.exists(progress_log_path):
            debug_print(f"Crop/downsample progress log not found: {progress_log_path}")
            return
        with open(progress_log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-max_lines:]
        debug_print(f"Crop/downsample progress log tail: {progress_log_path}")
        for line in lines:
            debug_print(f"Crop progress: {line.rstrip()}")
    except Exception as exc:
        debug_print(f"Failed to read crop/downsample progress log {progress_log_path}: {exc}")


def _run_crop_downsample_helper(native_image_path, final_image_path, settings):
    """Crop/downsample native JPEG in a lightweight subprocess with bounded retries."""
    helper = _crop_helper_path()
    if not os.path.exists(helper):
        raise FileNotFoundError(f"Crop/downsample helper not found: {helper}")

    crop_x = _pipeline_int(settings, "crop_x", 768, min_value=0)
    crop_y = _pipeline_int(settings, "crop_y", 432, min_value=0)
    crop_w = _pipeline_int(settings, "crop_w", 3072, min_value=1)
    crop_h = _pipeline_int(settings, "crop_h", 1728, min_value=1)
    out_w = _pipeline_int(settings, "output_width", crop_w, min_value=1)
    out_h = _pipeline_int(settings, "output_height", crop_h, min_value=1)
    jpeg_quality = _pipeline_int(settings, "source_jpeg_quality", 95, min_value=1, max_value=100)
    resample_name = str(settings.get("resample", "lanczos") if isinstance(settings, dict) else "lanczos").lower()

    if resample_name != "lanczos":
        debug_print(f"Unsupported resample={resample_name!r}; using lanczos for MVP pipeline")
        resample_name = "lanczos"

    progress_log_path = f"{final_image_path}.crop_progress.jsonl"
    try:
        if os.path.exists(progress_log_path):
            os.remove(progress_log_path)
    except Exception as exc:
        debug_print(f"Failed to remove old crop/downsample progress log {progress_log_path}: {exc}")

    cmd = [
        sys.executable or "/usr/bin/python3",
        helper,
        "--input", native_image_path,
        "--output", final_image_path,
        "--crop-x", str(crop_x),
        "--crop-y", str(crop_y),
        "--crop-w", str(crop_w),
        "--crop-h", str(crop_h),
        "--output-width", str(out_w),
        "--output-height", str(out_h),
        "--jpeg-quality", str(jpeg_quality),
        "--resample", resample_name,
        "--progress-log", progress_log_path,
    ]

    max_attempts = 1 + CROP_HELPER_MAX_RETRIES
    last_error = None

    for attempt in range(1, max_attempts + 1):
        debug_print(
            f"Running crop/downsample helper subprocess attempt {attempt}/{max_attempts}: "
            + " ".join(cmd)
        )
        started = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=CROP_HELPER_TIMEOUT_SECONDS,
                check=False,
            )
            duration = time.monotonic() - started

            parsed_summary = {}
            if result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    debug_print(f"Crop/downsample helper stdout: {line}")
                    try:
                        parsed_summary = json.loads(line)
                    except Exception:
                        pass

            if result.stderr.strip():
                for line in result.stderr.strip().splitlines():
                    debug_print(f"Crop/downsample helper stderr: {line}")

            if result.returncode != 0:
                last_error = RuntimeError(
                    f"Crop/downsample helper failed with exit_code={result.returncode} "
                    f"attempt={attempt}/{max_attempts} duration_sec={duration:.2f}"
                )
                debug_print(str(last_error))
                _log_crop_progress_tail(progress_log_path)
                _remove_crop_artifacts(final_image_path)
                _send_crop_status(
                    action="err",
                    error_code="crop_rc",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    native_image_path=native_image_path,
                    final_image_path=final_image_path,
                    output_width=out_w,
                    output_height=out_h,
                    jpeg_quality=jpeg_quality,
                    duration_sec=duration,
                    return_code=result.returncode,
                )
            elif not os.path.exists(final_image_path):
                last_error = RuntimeError(
                    f"Crop/downsample helper did not create output: {final_image_path} "
                    f"attempt={attempt}/{max_attempts}"
                )
                debug_print(str(last_error))
                _log_crop_progress_tail(progress_log_path)
                _remove_crop_artifacts(final_image_path)
                _send_crop_status(
                    action="err",
                    error_code="crop_missing",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    native_image_path=native_image_path,
                    final_image_path=final_image_path,
                    output_width=out_w,
                    output_height=out_h,
                    jpeg_quality=jpeg_quality,
                    duration_sec=duration,
                )
            else:
                output_size = os.path.getsize(final_image_path)
                if output_size <= 0:
                    last_error = RuntimeError(
                        f"Crop/downsample helper created zero-byte output: {final_image_path} "
                        f"attempt={attempt}/{max_attempts}"
                    )
                    debug_print(str(last_error))
                    _log_crop_progress_tail(progress_log_path)
                    _remove_crop_artifacts(final_image_path)
                    _send_crop_status(
                        action="err",
                        error_code="crop_zero",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        native_image_path=native_image_path,
                        final_image_path=final_image_path,
                        output_width=out_w,
                        output_height=out_h,
                        jpeg_quality=jpeg_quality,
                        duration_sec=duration,
                    )
                else:
                    debug_print(
                        f"Crop/downsample helper completed: output={final_image_path}, "
                        f"bytes={output_size}, duration_sec={duration:.2f}, "
                        f"attempt={attempt}/{max_attempts}"
                    )
                    if attempt > 1:
                        _send_crop_status(
                            action="rec",
                            error_code="crop",
                            attempt=attempt,
                            max_attempts=max_attempts,
                            native_image_path=native_image_path,
                            final_image_path=final_image_path,
                            output_width=out_w,
                            output_height=out_h,
                            jpeg_quality=jpeg_quality,
                            output_bytes=output_size,
                            duration_sec=duration,
                        )

                    geometry = {
                        "native_width": parsed_summary.get("native_width"),
                        "native_height": parsed_summary.get("native_height"),
                        "crop_x": crop_x,
                        "crop_y": crop_y,
                        "crop_w": crop_w,
                        "crop_h": crop_h,
                        "output_width": out_w,
                        "output_height": out_h,
                        "resample": "lanczos",
                        "intermediate_jpeg_quality": jpeg_quality,
                        "crop_downsample_duration_sec": round(duration, 3),
                        "crop_downsample_helper": os.path.basename(helper),
                    }
                    return geometry

        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            last_error = exc
            debug_print(
                f"Crop/downsample helper timeout attempt={attempt}/{max_attempts} "
                f"timeout_sec={CROP_HELPER_TIMEOUT_SECONDS} duration_sec={duration:.2f}"
            )
            _log_crop_progress_tail(progress_log_path)
            _remove_crop_artifacts(final_image_path)
            _send_crop_status(
                action="err",
                error_code="crop_timeout",
                attempt=attempt,
                max_attempts=max_attempts,
                native_image_path=native_image_path,
                final_image_path=final_image_path,
                output_width=out_w,
                output_height=out_h,
                jpeg_quality=jpeg_quality,
                duration_sec=duration,
            )

        if attempt < max_attempts:
            next_attempt = attempt + 1
            debug_print(
                f"Waiting {CROP_HELPER_RETRY_DELAY_SECONDS}s before crop/downsample retry "
                f"{next_attempt}/{max_attempts}"
            )
            _send_crop_status(
                action="retry",
                error_code="crop",
                attempt=next_attempt,
                max_attempts=max_attempts,
                native_image_path=native_image_path,
                final_image_path=final_image_path,
                output_width=out_w,
                output_height=out_h,
                jpeg_quality=jpeg_quality,
                wait_seconds=CROP_HELPER_RETRY_DELAY_SECONDS,
            )
            time.sleep(CROP_HELPER_RETRY_DELAY_SECONDS)

    _send_crop_status(
        action="fail",
        error_code="crop",
        attempt=max_attempts,
        max_attempts=max_attempts,
        native_image_path=native_image_path,
        final_image_path=final_image_path,
        output_width=out_w,
        output_height=out_h,
        jpeg_quality=jpeg_quality,
    )
    raise RuntimeError(
        f"Crop/downsample helper failed after {max_attempts} attempts; last_error={last_error!r}"
    )


def _crop_and_downsample_native(native_image_path, final_image_path, settings):
    """Crop/downsample native JPEG via isolated helper subprocess."""
    geometry_info = _run_crop_downsample_helper(native_image_path, final_image_path, settings)
    _release_memory_hint("crop/downsample")
    return geometry_info

def _release_memory_hint(context=""):
    """Best-effort memory release between high-memory stages on Pi Zero 2W."""
    try:
        gc.collect()
    except Exception:
        pass
    debug_print(f"Memory release hint complete{f' after {context}' if context else ''}")


def capture_image_libcamera_pipeline(image_pipeline, directory_path=IMAGE_DIRECTORY):
    """Capture native full JPEG, crop/downsample, and return final JPEG path.

    This branch intentionally mirrors the Mac-side spatial/HEIC sweeps:
      native 4608x2592 JPEG -> fixed native-coordinate crop -> optional
      LANCZOS downsample -> HEIC compression in the unchanged send path.
    """
    settings = image_pipeline or {}
    source_width = _pipeline_int(settings, "source_width", 4608, min_value=1)
    source_height = _pipeline_int(settings, "source_height", 2592, min_value=1)
    source_jpeg_quality = _pipeline_int(settings, "source_jpeg_quality", 95, min_value=1, max_value=100)
    capture_backend = settings.get("capture_backend", "auto") if isinstance(settings, dict) else "auto"

    command, actual_backend = _select_camera_command(capture_backend)
    if actual_backend == "picamera2":
        legacy_key = settings.get("legacy_resolution_key", RESOLUTION_KEY) if isinstance(settings, dict) else RESOLUTION_KEY
        debug_print(
            "image_pipeline requested legacy/picamera2 backend; using legacy capture path "
            f"with resolution_key={legacy_key}"
        )
        return capture_image(resolution_key=legacy_key, directory_path=directory_path, image_pipeline=None)

    os.makedirs(directory_path, exist_ok=True)

    image_filename = generate_filename()
    final_image_path = os.path.join(directory_path, image_filename)
    file_name_no_ext, _ = os.path.splitext(image_filename)
    native_image_path = os.path.join(directory_path, f"{file_name_no_ext}_native_full.jpg")
    log_prefix = os.path.join(directory_path, f"{file_name_no_ext}_native_full")

    capture_info = _run_native_full_capture(
        command=command,
        native_image_path=native_image_path,
        source_width=source_width,
        source_height=source_height,
        jpeg_quality=source_jpeg_quality,
        log_prefix=log_prefix,
        settings=settings,
    )

    geometry_info = _crop_and_downsample_native(native_image_path, final_image_path, settings)

    metadata = {
        "capture_backend_requested": capture_backend,
        "capture_backend_actual": actual_backend,
        "capture_command": capture_info.get("capture_command"),
        "capture_stdout_log": capture_info.get("stdout_log"),
        "capture_stderr_log": capture_info.get("stderr_log"),
        "capture_metadata_json": capture_info.get("metadata_json"),
        "camera_control_args": capture_info.get("camera_control_args"),
        "camera_control_args_used": capture_info.get("camera_control_args_used"),
        "camera_controls_fallback_used": capture_info.get("camera_controls_fallback_used"),
        "requested_camera_controls": capture_info.get("requested_camera_controls"),
        "native_image_path": native_image_path,
        "native_image_size_bytes": os.path.getsize(native_image_path),
        "final_image_path": final_image_path,
        "final_image_size_bytes": os.path.getsize(final_image_path),
        "pipeline_enabled": True,
        "pipeline_note": "native full JPEG -> native-coordinate crop -> optional LANCZOS downsample -> unchanged HEIC/send path",
    }
    requested_camera_controls = capture_info.get("requested_camera_controls")
    if isinstance(requested_camera_controls, dict):
        metadata.update(requested_camera_controls)

    libcamera_metadata = _load_libcamera_metadata_json(capture_info.get("metadata_json"))
    if libcamera_metadata:
        metadata.update(libcamera_metadata)
        metadata["libcamera_metadata_available"] = True
        metadata["libcamera_metadata_keys"] = sorted(libcamera_metadata.keys())
        debug_print(
            "Libcamera metadata loaded: "
            f"ExposureTime={libcamera_metadata.get('ExposureTime')}, "
            f"AnalogueGain={libcamera_metadata.get('AnalogueGain')}, "
            f"DigitalGain={libcamera_metadata.get('DigitalGain')}, "
            f"ColourGains={libcamera_metadata.get('ColourGains')}, "
            f"ColourTemperature={libcamera_metadata.get('ColourTemperature')}, "
            f"LensPosition={libcamera_metadata.get('LensPosition')}, "
            f"AfState={libcamera_metadata.get('AfState')}, "
            f"AfMode={libcamera_metadata.get('AfMode')}, "
            f"FocusFoM={libcamera_metadata.get('FocusFoM')}, "
            f"Lux={libcamera_metadata.get('Lux')}, "
            f"FrameDuration={libcamera_metadata.get('FrameDuration')}, "
            f"SensorTemperature={libcamera_metadata.get('SensorTemperature')}"
        )
    else:
        metadata["libcamera_metadata_available"] = False
        debug_print("Libcamera metadata unavailable for this capture; continuing without actual camera metadata")

    metadata.update(geometry_info)
    if isinstance(settings, dict):
        metadata["heic_quality_requested"] = settings.get("heic_quality")
        metadata["crop_mode"] = settings.get("crop_mode", "fixed")

    save_capture_metadata(final_image_path, metadata)

    debug_print(
        "Image pipeline output saved as "
        f"'{final_image_path}', file size = {os.path.getsize(final_image_path)} bytes"
    )
    debug_print(
        "Image pipeline geometry: "
        f"native={geometry_info['native_width']}x{geometry_info['native_height']} "
        f"crop=({geometry_info['crop_x']},{geometry_info['crop_y']},"
        f"{geometry_info['crop_w']},{geometry_info['crop_h']}) "
        f"output={geometry_info['output_width']}x{geometry_info['output_height']}"
    )

    return final_image_path


def capture_image(resolution_key="VGA", directory_path=IMAGE_DIRECTORY, image_pipeline=None):
    """Capture an image and save it in the directory.

    Legacy mode uses Picamera2 and the historical RESOLUTIONS table.
    New dev mode uses image_pipeline to run rpicam/libcamera native-full
    capture followed by explicit crop/spatial downsample before HEIC send.
    """
    if image_pipeline and _pipeline_bool(image_pipeline.get("enabled"), default=False):
        return capture_image_libcamera_pipeline(image_pipeline, directory_path=directory_path)

    resolution = validate_resolution(resolution_key)

    # Initialize the camera. Import Picamera2 lazily so the new libcamera
    # pipeline and HEIC compression path do not load Picamera2 unless legacy
    # capture mode is explicitly used.
    from picamera2 import Picamera2

    picam2 = Picamera2()

    # Set the configuration with the chosen resolution
    config = picam2.create_still_configuration(main={"size": resolution})

    # Apply the configuration
    picam2.configure(config)

    # Start the camera
    picam2.start()

    # Allow the camera to warm up
    time.sleep(2)

    # Generate the filename and construct the full image path
    image_filename = generate_filename()
    image_path = os.path.join(directory_path, image_filename)

    # Ensure the directory exists
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)

    # Capture the image and save it to the specified path. Picamera2 usually
    # returns the metadata for the captured frame; fall back to capture_metadata
    # if this version returns None. Metadata is used only for telemetry and must
    # not break image capture if unavailable.
    capture_metadata = None
    try:
        capture_metadata = picam2.capture_file(image_path)
    except TypeError:
        # Older/newer API variation safety: keep the original simple behavior.
        picam2.capture_file(image_path)

    if not isinstance(capture_metadata, dict):
        try:
            capture_metadata = picam2.capture_metadata()
        except Exception as exc:
            debug_print(f"Capture metadata unavailable: {exc}")
            capture_metadata = {}

    if capture_metadata:
        save_capture_metadata(image_path, capture_metadata)
        debug_print(
            "Capture metadata: "
            f"ExposureTime={capture_metadata.get('ExposureTime')}, "
            f"AnalogueGain={capture_metadata.get('AnalogueGain')}, "
            f"DigitalGain={capture_metadata.get('DigitalGain')}, "
            f"ColourGains={capture_metadata.get('ColourGains')}, "
            f"LensPosition={capture_metadata.get('LensPosition')}, "
            f"AfState={capture_metadata.get('AfState')}, "
            f"FocusFoM={capture_metadata.get('FocusFoM')}"
        )

    # Get the file size in bytes
    file_size = os.path.getsize(image_path)
    debug_print(f"Image saved as '{image_path}', file size = {file_size} bytes")
    debug_print(f"Resolution key: {resolution_key}, resolution: {resolution[0]}x{resolution[1]}")

    # Stop the camera
    picam2.stop()

    return image_path

def encode_to_base64(binary_data):
    return base64.b64encode(binary_data).decode('ascii')


def get_cpu_temperature():
    """Get the Raspberry Pi's CPU temperature."""
    result = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True)
    temp_str = result.stdout.strip().replace("temp=", "").replace("'C", "")
    return float(temp_str)


def get_file_size(file_path):
    """Get the file size of a given file in bytes."""
    if os.path.exists(file_path):
        return os.path.getsize(file_path)
    return 0


def split_image_jpeg(image_path, buffer_directory, image_quality):
    """Splits the image into base64-encoded buffers after JPEG encoding."""
    image_quality = validate_image_quality(image_quality)

    if os.path.exists(buffer_directory):
        shutil.rmtree(buffer_directory)
        debug_print("Deleted buffers dir")

    os.makedirs(buffer_directory, exist_ok=True)
    debug_print("Created buffers dir")

    # Import OpenCV lazily; the HEIC/libcamera MVP path does not need it.
    import cv2

    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Failed to load image from path: {image_path}")

    retval, buffer = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), image_quality])
    if not retval:
        raise ValueError("Failed to encode image")

    file_dir, file_name = os.path.split(image_path)
    file_name_no_ext, file_ext = os.path.splitext(file_name)
    compressed_file_path = os.path.join(file_dir, f"{file_name_no_ext}_compressed{file_ext}")

    with open(compressed_file_path, 'wb') as compressed_file:
        compressed_file.write(buffer)

    debug_print(f"Compressed image saved as: {compressed_file_path}")

    base64_data = base64.b64encode(buffer).decode("ascii")
    file_length = len(base64_data)
    buffer_number = 0

    while buffer_number * BUFFER_SIZE < file_length:
        start_pos = buffer_number * BUFFER_SIZE
        current_buffer = base64_data[start_pos:start_pos + BUFFER_SIZE]
        buffer_path = os.path.join(buffer_directory, f"split_{buffer_number}.txt")

        with open(buffer_path, 'w') as buffer_file:
            buffer_file.write(current_buffer)

        buffer_number += 1

    debug_print(f"Saved {buffer_number} buffer txt files.")


def _heic_helper_path():
    """Return the colocated lightweight HEIC helper script path."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "heic_encode_helper.py")


# HEIC helper retry policy for bmcam000 MVP stability.
# Successful encodes are typically 8-12 seconds. If the helper exceeds 30s,
# treat it as a likely libheif/pillow_heif stall, clean up outputs, wait,
# and retry. Initial attempt + 3 retries = 4 total attempts.
HEIC_HELPER_TIMEOUT_SECONDS = 30
HEIC_HELPER_MAX_RETRIES = 3
HEIC_HELPER_RETRY_DELAY_SECONDS = 60


def _heic_tmp_path_for_output(heic_output_path):
    base, ext = os.path.splitext(heic_output_path)
    return f"{base}.tmp{ext}"


def _remove_heic_encode_artifacts(heic_output_path):
    """Remove final/temp HEIC files after failed or timed-out encode attempts."""
    for path in (_heic_tmp_path_for_output(heic_output_path), heic_output_path):
        try:
            if os.path.exists(path):
                os.remove(path)
                debug_print(f"Removed HEIC encode artifact: {path}")
        except Exception as exc:
            debug_print(f"Failed to remove HEIC encode artifact {path}: {exc}")


def _image_dimensions_text(image_path):
    try:
        with Image.open(image_path) as img:
            return f"{img.size[0]}x{img.size[1]}"
    except Exception:
        return "na"


def _send_heic_status(action, error_code, attempt, max_attempts, image_path, image_quality,
                      wait_seconds=None, output_bytes=None, duration_sec=None, return_code=None):
    """Best-effort parseable BM status for HEIC retry/error handling.

    Message shape intentionally follows existing compact WS telemetry, e.g.:
      <WS v=1 a=err e=heic_timeout try=1 max=4 rk=2688x1512 q=20 ...>

    This must never break capture/compression/transmit.
    """
    try:
        try:
            cpu_temp = f"{get_cpu_temperature():.1f}"
        except Exception:
            cpu_temp = "na"

        try:
            input_bytes = os.path.getsize(image_path)
        except Exception:
            input_bytes = None

        fields = [
            ("v", "1"),
            ("a", action),
            ("e", error_code),
            ("try", attempt),
            ("max", max_attempts),
            ("tmo", HEIC_HELPER_TIMEOUT_SECONDS),
            ("wait", wait_seconds),
            ("rk", _image_dimensions_text(image_path)),
            ("q", image_quality),
            ("sz", input_bytes),
            ("hsz", output_bytes),
            ("dur", f"{duration_sec:.1f}" if duration_sec is not None else None),
            ("rc", return_code),
            ("ct", cpu_temp),
            ("sha", get_software_sha()),
            ("hn", get_hostname()),
        ]
        send_compact_text_message(compact_kv_message("WS", fields))
    except Exception as exc:
        debug_print(f"Failed to send HEIC status telemetry: {exc}")


def _run_heic_encode_helper(image_path, heic_output_path, image_quality):
    """Encode HEIC in a lightweight subprocess with bounded retries.

    On bmcam000 / Pi Zero 2W, the exact RGB + temp-file HEIC procedure passed
    repeatedly when run in a tiny standalone Python process, but the helper can
    still intermittently stall after repeated full capture/transmit cycles.
    Keep the encoder isolated and make stalls recoverable: timeout, cleanup,
    cooldown, retry, then fail cleanly without sending partial image chunks.
    """
    helper = _heic_helper_path()
    if not os.path.exists(helper):
        raise FileNotFoundError(f"HEIC helper not found: {helper}")

    cmd = [
        sys.executable or "/usr/bin/python3",
        helper,
        "--input", image_path,
        "--output", heic_output_path,
        "--quality", str(image_quality),
    ]

    max_attempts = 1 + HEIC_HELPER_MAX_RETRIES
    last_error = None

    for attempt in range(1, max_attempts + 1):
        debug_print(
            f"Running HEIC helper subprocess attempt {attempt}/{max_attempts}: "
            + " ".join(cmd)
        )
        started = time.monotonic()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=HEIC_HELPER_TIMEOUT_SECONDS,
                check=False,
            )
            duration = time.monotonic() - started

            if result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    debug_print(f"HEIC helper stdout: {line}")
            if result.stderr.strip():
                for line in result.stderr.strip().splitlines():
                    debug_print(f"HEIC helper stderr: {line}")

            if result.returncode != 0:
                last_error = RuntimeError(
                    f"HEIC helper failed with exit_code={result.returncode} "
                    f"attempt={attempt}/{max_attempts} duration_sec={duration:.2f}"
                )
                debug_print(str(last_error))
                _remove_heic_encode_artifacts(heic_output_path)
                _send_heic_status(
                    action="err",
                    error_code="heic_rc",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    image_path=image_path,
                    image_quality=image_quality,
                    duration_sec=duration,
                    return_code=result.returncode,
                )
            else:
                if not os.path.exists(heic_output_path):
                    last_error = ValueError(
                        f"HEIC helper did not create output: {heic_output_path} "
                        f"attempt={attempt}/{max_attempts}"
                    )
                    debug_print(str(last_error))
                    _remove_heic_encode_artifacts(heic_output_path)
                    _send_heic_status(
                        action="err",
                        error_code="heic_missing",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        image_path=image_path,
                        image_quality=image_quality,
                        duration_sec=duration,
                    )
                else:
                    output_size = os.path.getsize(heic_output_path)
                    if output_size <= 0:
                        last_error = ValueError(
                            f"HEIC helper created zero-byte output: {heic_output_path} "
                            f"attempt={attempt}/{max_attempts}"
                        )
                        debug_print(str(last_error))
                        _remove_heic_encode_artifacts(heic_output_path)
                        _send_heic_status(
                            action="err",
                            error_code="heic_zero",
                            attempt=attempt,
                            max_attempts=max_attempts,
                            image_path=image_path,
                            image_quality=image_quality,
                            duration_sec=duration,
                        )
                    else:
                        debug_print(
                            f"HEIC helper completed: output={heic_output_path}, "
                            f"bytes={output_size}, duration_sec={duration:.2f}, "
                            f"attempt={attempt}/{max_attempts}"
                        )
                        if attempt > 1:
                            _send_heic_status(
                                action="rec",
                                error_code="heic",
                                attempt=attempt,
                                max_attempts=max_attempts,
                                image_path=image_path,
                                image_quality=image_quality,
                                output_bytes=output_size,
                                duration_sec=duration,
                            )
                        return output_size, duration

        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            last_error = exc
            debug_print(
                f"HEIC helper timeout attempt={attempt}/{max_attempts} "
                f"timeout_sec={HEIC_HELPER_TIMEOUT_SECONDS} duration_sec={duration:.2f}"
            )
            _remove_heic_encode_artifacts(heic_output_path)
            _send_heic_status(
                action="err",
                error_code="heic_timeout",
                attempt=attempt,
                max_attempts=max_attempts,
                image_path=image_path,
                image_quality=image_quality,
                duration_sec=duration,
            )

        if attempt < max_attempts:
            next_attempt = attempt + 1
            debug_print(
                f"Waiting {HEIC_HELPER_RETRY_DELAY_SECONDS}s before HEIC retry "
                f"{next_attempt}/{max_attempts}"
            )
            _send_heic_status(
                action="retry",
                error_code="heic",
                attempt=next_attempt,
                max_attempts=max_attempts,
                image_path=image_path,
                image_quality=image_quality,
                wait_seconds=HEIC_HELPER_RETRY_DELAY_SECONDS,
            )
            time.sleep(HEIC_HELPER_RETRY_DELAY_SECONDS)

    _send_heic_status(
        action="fail",
        error_code="heic",
        attempt=max_attempts,
        max_attempts=max_attempts,
        image_path=image_path,
        image_quality=image_quality,
    )
    raise RuntimeError(
        f"HEIC helper failed after {max_attempts} attempts; last_error={last_error!r}"
    )


def _buffer_directory_stats(buffer_directory=BUFFER_DIRECTORY):
    """Return exact base64 buffer stats without changing buffer contents."""
    stats = {
        "buffer_directory": buffer_directory,
        "buffer_count": 0,
        "base64_chars": 0,
        "buffer_size": BUFFER_SIZE,
    }
    try:
        if not os.path.isdir(buffer_directory):
            return stats
        files = [
            name for name in os.listdir(buffer_directory)
            if name.startswith("split_") and name.endswith(".txt")
        ]
        stats["buffer_count"] = len(files)
        total_chars = 0
        for name in files:
            path = os.path.join(buffer_directory, name)
            with open(path, "r", encoding="ascii", errors="ignore") as f:
                total_chars += len(f.read())
        stats["base64_chars"] = total_chars
    except Exception as exc:
        debug_print(f"Failed to compute buffer stats: {exc}")
    return stats

def split_image_heic(image_path, image_quality=IMAGE_QUALITY):
    """Compress the image to HEIC and split into buffers.

    Safety note for bmcam000 / Pi Zero 2W tests:
    - The validated stable HEIC path is: open JPEG -> convert RGB -> write
      to a temporary HEIC file -> verify nonzero -> atomically rename.
    - That encode now runs in heic_encode_helper.py as a lightweight subprocess
      because the same encode was stable standalone but unstable inside the
      heavy production module context.
    - Do not change the existing base64 chunking or send path here.
      The Bristlemouth message path still chunks the base64 HEIC payload
      using BUFFER_SIZE exactly as before.
    """
    image_quality = validate_image_quality(image_quality)

    if os.path.exists(BUFFER_DIRECTORY):
        shutil.rmtree(BUFFER_DIRECTORY)
        debug_print("Deleted buffers directory")

    os.makedirs(BUFFER_DIRECTORY, exist_ok=True)
    debug_print("Created buffers directory")

    file_name_without_ext = os.path.splitext(os.path.basename(image_path))[0]
    heic_output_path = os.path.join(IMAGE_DIRECTORY, f"{file_name_without_ext}_compressed.heic")
    tmp_heic_output_path = os.path.join(IMAGE_DIRECTORY, f"{file_name_without_ext}_compressed.tmp.heic")

    # Avoid stale zero-byte files from prior interrupted encodes being mistaken
    # for valid output. The helper creates the final file only after the
    # temporary HEIC is fully encoded and verified.
    for stale_path in (tmp_heic_output_path, heic_output_path):
        try:
            if os.path.exists(stale_path):
                os.remove(stale_path)
                debug_print(f"Removed stale HEIC file before encode: {stale_path}")
        except Exception as exc:
            debug_print(f"Failed to remove stale HEIC file {stale_path}: {exc}")

    # Quality convention:
    # lower = smaller/more compressed/lower quality, higher = larger/less compressed/higher quality.
    debug_print(
        f"Starting isolated HEIC encode: input={image_path}, "
        f"output={heic_output_path}, quality={image_quality}"
    )
    file_size, encode_duration_sec = _run_heic_encode_helper(
        image_path=image_path,
        heic_output_path=heic_output_path,
        image_quality=image_quality,
    )

    debug_print(
        f"Compressed image saved as '{heic_output_path}', file size = {file_size} bytes, "
        f"encode_duration_sec={encode_duration_sec:.2f}"
    )

    with open(heic_output_path, "rb") as heic_file:
        heic_data = heic_file.read()

    if not heic_data:
        raise ValueError(f"HEIC output is empty after encode: {heic_output_path}")

    base64_data = base64.b64encode(heic_data).decode("ascii")
    file_length = len(base64_data)
    buffer_number = 0

    while buffer_number * BUFFER_SIZE < file_length:
        start_pos = buffer_number * BUFFER_SIZE
        current_buffer = base64_data[start_pos:start_pos + BUFFER_SIZE]
        buffer_path = os.path.join(BUFFER_DIRECTORY, f"split_{buffer_number}.txt")

        with open(buffer_path, 'w') as buffer_file:
            buffer_file.write(current_buffer)

        buffer_number += 1

    debug_print(
        f"Saved {buffer_number} buffer text files in {BUFFER_DIRECTORY}; "
        f"base64_chars={file_length}; buffer_size={BUFFER_SIZE}"
    )

    return os.path.basename(heic_output_path), buffer_number, file_size


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


def _build_start_image_message(
    compressed_file_name,
    current_timestamp,
    num_buffers,
    start_metadata=None,
    max_payload_bytes=285,
):
    """Build one budgeted START IMG message.

    START is not chunked, so keep this comfortably below the 300-byte practical
    BM/Sofar message limit. If the message is too long, drop lowest-priority
    optional storage/context fields first while preserving the fields most useful
    for backend reconstruction and field debugging.
    """
    base_parts = [
        f"filename: {_clean_value(compressed_file_name, max_len=96)}",
        f"timestamp: {_clean_value(current_timestamp, max_len=32)}",
        f"length: {int(num_buffers)}",
    ]

    optional = _start_metadata_pairs(start_metadata)

    # Lowest-value fields drop first if the filename/time/context makes START
    # too large. Preserve sf/sp/zh/rk/q/sha as long as possible.
    drop_order = [
        "lg",  # cron logs KiB
        "bf",  # buffer dir KiB
        "im",  # images dir MiB
        "st",  # SD total MiB
        "su",  # SD used MiB
        "tz",  # timezone string can be long
        "hn",  # hostname
        "ws",
        "we",
    ]

    def render(selected_optional):
        parts = list(base_parts)
        for key, value in selected_optional:
            max_len = 32
            if key == "sha":
                max_len = 12
            elif key == "tz":
                max_len = 24
            elif key in {"rk", "hn"}:
                max_len = 24
            parts.append(f"{key}={_clean_value(value, max_len=max_len)}")
        return "<START IMG> " + ", ".join(parts) + "\n"

    selected = list(optional)
    msg = render(selected)

    for key_to_drop in drop_order:
        if len(msg.encode("ascii", errors="ignore")) <= max_payload_bytes:
            break
        before = len(selected)
        selected = [(k, v) for (k, v) in selected if k != key_to_drop]
        if len(selected) != before:
            debug_print(f"Skipping START metadata field due to payload budget: {key_to_drop}")
        msg = render(selected)

    while len(msg.encode("ascii", errors="ignore")) > max_payload_bytes and selected:
        dropped_key, _ = selected.pop()
        debug_print(f"Skipping START metadata field due to payload budget: {dropped_key}")
        msg = render(selected)

    encoded = msg.encode("ascii", errors="ignore")
    if len(encoded) > max_payload_bytes:
        # Last-resort safety. This should be rare because base START is short.
        encoded = encoded[:max_payload_bytes - 2] + b"\n"
        msg = encoded.decode("ascii", errors="ignore")

    return msg

def send_buffers(buffer_directory, compressed_file_name, start_metadata=None, capture_metadata=None):
    """Send the buffer files over UART."""
    files = os.listdir(buffer_directory)
    num_buffers = len(files)
    if num_buffers == 0:
        raise ValueError("No buffers found to send!")

    current_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    debug_print(
        f"Starting transmission of image: {compressed_file_name} with {num_buffers} buffers; "
        f"buffer_size={BUFFER_SIZE}; delay_sec={IMAGE_TRANSMIT_DELAY_SECONDS}"
    )

    # START IMG is one unchunked BM message. Build it with a hard budget so
    # compact metadata cannot push the message over the practical payload limit.

    # Measure the full Pi-side send loop duration, including the existing pacing
    # sleeps between UART writes. This is the camera UART/application throughput.
    uart_start = time.monotonic()

    start_msg = _build_start_image_message(
        compressed_file_name,
        current_timestamp,
        num_buffers,
        start_metadata=start_metadata,
    )
    debug_print(f"START IMG message bytes={len(start_msg.encode('ascii', errors='ignore'))}: {start_msg.strip()}")
    _get_bm_serial().spotter_tx(start_msg.encode('ascii'))
    time.sleep(IMAGE_TRANSMIT_DELAY_SECONDS)

    sent_buffers = 0
    for i in range(num_buffers):
        buffer_path = os.path.join(buffer_directory, f"split_{i}.txt")

        with open(buffer_path, 'r') as buffer_file:
            buffer_data = buffer_file.read()

        buffer_to_send = f"<I{i}>{buffer_data}\n"
        _get_bm_serial().spotter_tx(buffer_to_send.encode('ascii'))
        sent_buffers += 1

        debug_print(f"Sent buffer {i + 1} of {num_buffers}")
        time.sleep(IMAGE_TRANSMIT_DELAY_SECONDS)

    uart_duration_sec = time.monotonic() - uart_start

    try:
        final_cpu_temp = get_cpu_temperature()
        final_cpu_temp_text = f"{final_cpu_temp:.1f}"
    except Exception as exc:
        debug_print(f"Failed to read final CPU temp: {exc}")
        final_cpu_temp_text = "na"

    end_msg = _build_end_image_message(
        compressed_file_name,
        [
            ("filename", compressed_file_name),
            ("uart_duration_sec", f"{uart_duration_sec:.1f}"),
            ("sent_buffers", sent_buffers),
            ("cpu_temp_c", final_cpu_temp_text),
        ],
        capture_metadata=capture_metadata,
    )
    debug_print(f"END IMG message bytes={len(end_msg.encode('ascii', errors='ignore'))}: {end_msg.strip()}")
    _get_bm_serial().spotter_tx(end_msg.encode('ascii'))

    debug_print(
        f"Finished transmission of image: {compressed_file_name}; "
        f"uart_duration_sec={uart_duration_sec:.1f}; sent_buffers={sent_buffers}; "
        f"cpu_temp_c={final_cpu_temp_text}"
    )

    return {
        "uart_duration_sec": uart_duration_sec,
        "sent_buffers": sent_buffers,
        "cpu_temp_c": final_cpu_temp_text,
    }


def compress_and_send_image(
    image_path,
    image_quality=IMAGE_QUALITY,
    image_res_key=None,
    schedule_metadata=None,
):
    """Compress the image to HEIC, save it, and send buffers."""
    bm_transfer_settings = apply_bm_serial_runtime_settings(configure_serial=True)

    compressed_file_name, num_buffers, file_size_compressed = split_image_heic(
        image_path,
        image_quality=image_quality,
    )
    buffer_stats = _buffer_directory_stats(BUFFER_DIRECTORY)
    compressed_file_path = os.path.join(IMAGE_DIRECTORY, compressed_file_name)
    storage_health = collect_storage_health()

    schedule_metadata = schedule_metadata or {}
    start_metadata = {
        "image_res_key": image_res_key,
        "image_quality": image_quality,
        "timezone": schedule_metadata.get("timezone"),
        "window_start": schedule_metadata.get("window_start"),
        "window_end": schedule_metadata.get("window_end"),
        "software_sha": get_software_sha(),
        "hostname": get_hostname(),
        "sd_total_bytes": storage_health.get("sd_total_bytes"),
        "sd_used_bytes": storage_health.get("sd_used_bytes"),
        "sd_free_bytes": storage_health.get("sd_free_bytes"),
        "sd_used_pct": storage_health.get("sd_used_pct"),
        "images_dir_bytes": storage_health.get("images_dir_bytes"),
        "buffer_dir_bytes": storage_health.get("buffer_dir_bytes"),
        "cron_logs_dir_bytes": storage_health.get("cron_logs_dir_bytes"),
        "zero_byte_heic_count": storage_health.get("zero_byte_heic_count"),
    }

    capture_metadata = update_capture_metadata(image_path, {
        "software_sha": get_software_sha(),
        "hostname": get_hostname(),
        "metadata_schema": "bmcam_runtime_sidecar_v1",
        "metadata_source": "config_runtime_not_libcamera_metadata",
        "transmit_requested": True,
        "image_res_key": image_res_key,
        "image_quality": image_quality,
        "timezone": schedule_metadata.get("timezone"),
        "window_start": schedule_metadata.get("window_start"),
        "window_end": schedule_metadata.get("window_end"),
        "raw_jpeg_bytes": get_file_size(image_path),
        "compressed_file_name": compressed_file_name,
        "compressed_file_path": compressed_file_path,
        "heic_bytes": file_size_compressed,
        "base64_chars": buffer_stats.get("base64_chars"),
        "buffer_count": num_buffers,
        "chunk_size": BUFFER_SIZE,
        "image_transmit_delay_seconds": IMAGE_TRANSMIT_DELAY_SECONDS,
        "bm_network_type": bm_transfer_settings.get("network_type"),
        "bm_network_description": bm_transfer_settings.get("network_description"),
        **storage_health,
    })
    transmit_stats = send_buffers(
        BUFFER_DIRECTORY,
        compressed_file_name,
        start_metadata=start_metadata,
        capture_metadata=capture_metadata,
    )

    post_transmit_storage_health = collect_storage_health()
    update_capture_metadata(image_path, {
        "transmit_success": True,
        "transmit_duration_sec": transmit_stats.get("uart_duration_sec"),
        "sent_buffers": transmit_stats.get("sent_buffers"),
        "final_cpu_temp_c": transmit_stats.get("cpu_temp_c"),
        **post_transmit_storage_health,
    })
    return compressed_file_name, num_buffers, file_size_compressed


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
