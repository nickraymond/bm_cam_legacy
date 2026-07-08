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

    candidate_fields = [
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


def _build_end_image_message(compressed_file_name, core_fields, capture_metadata=None, max_payload_bytes=280):
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


def _run_native_full_capture(command, native_image_path, source_width, source_height, jpeg_quality, log_prefix):
    """Capture native/full-source JPEG with rpicam-still or libcamera-still."""
    stdout_log = f"{log_prefix}.stdout.log"
    stderr_log = f"{log_prefix}.stderr.log"

    cmd = [
        command,
        "-n",
        "--timeout", "2000",
        "--width", str(source_width),
        "--height", str(source_height),
        "--quality", str(jpeg_quality),
        "-o", native_image_path,
    ]

    debug_print("Running native capture command: " + " ".join(cmd))
    with open(stdout_log, "w", encoding="utf-8") as out, open(stderr_log, "w", encoding="utf-8") as err:
        result = subprocess.run(cmd, stdout=out, stderr=err, text=True)

    # Some older camera apps have option differences. Retry once without -n so
    # an option mismatch does not kill the test branch unnecessarily.
    if result.returncode != 0:
        retry_cmd = [x for x in cmd if x != "-n"]
        debug_print(
            f"Native capture command failed with exit {result.returncode}; "
            "retrying without -n"
        )
        with open(stdout_log, "a", encoding="utf-8") as out, open(stderr_log, "a", encoding="utf-8") as err:
            out.write("\n--- RETRY WITHOUT -n ---\n")
            err.write("\n--- RETRY WITHOUT -n ---\n")
            result = subprocess.run(retry_cmd, stdout=out, stderr=err, text=True)
            cmd = retry_cmd

    if result.returncode != 0:
        raise RuntimeError(
            f"Native capture failed with exit {result.returncode}. "
            f"See logs: {stdout_log}, {stderr_log}"
        )

    if not os.path.exists(native_image_path) or os.path.getsize(native_image_path) <= 0:
        raise RuntimeError(f"Native capture did not produce a valid file: {native_image_path}")

    return {
        "capture_command": cmd,
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
    }


def _crop_and_downsample_native(native_image_path, final_image_path, settings):
    """Crop in native image coordinates, then downsample to the requested output size."""
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

    with Image.open(native_image_path) as img:
        img = img.convert("RGB")
        native_w, native_h = img.size

        if crop_x + crop_w > native_w or crop_y + crop_h > native_h:
            raise ValueError(
                "image_pipeline crop exceeds native image bounds: "
                f"crop=({crop_x},{crop_y},{crop_w},{crop_h}) native={native_w}x{native_h}"
            )

        cropped = img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
        if (out_w, out_h) != (crop_w, crop_h):
            cropped = cropped.resize((out_w, out_h), Image.Resampling.LANCZOS)

        cropped.save(final_image_path, format="JPEG", quality=jpeg_quality, subsampling=0)
        del cropped
        del img

    _release_memory_hint("crop/downsample")

    return {
        "native_width": native_w,
        "native_height": native_h,
        "crop_x": crop_x,
        "crop_y": crop_y,
        "crop_w": crop_w,
        "crop_h": crop_h,
        "output_width": out_w,
        "output_height": out_h,
        "resample": "lanczos",
        "intermediate_jpeg_quality": jpeg_quality,
    }



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
    )

    geometry_info = _crop_and_downsample_native(native_image_path, final_image_path, settings)

    metadata = {
        "capture_backend_requested": capture_backend,
        "capture_backend_actual": actual_backend,
        "capture_command": capture_info.get("capture_command"),
        "capture_stdout_log": capture_info.get("stdout_log"),
        "capture_stderr_log": capture_info.get("stderr_log"),
        "native_image_path": native_image_path,
        "native_image_size_bytes": os.path.getsize(native_image_path),
        "final_image_path": final_image_path,
        "final_image_size_bytes": os.path.getsize(final_image_path),
        "pipeline_enabled": True,
        "pipeline_note": "native full JPEG -> native-coordinate crop -> optional LANCZOS downsample -> unchanged HEIC/send path",
    }
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


def _run_heic_encode_helper(image_path, heic_output_path, image_quality):
    """Encode HEIC in a lightweight subprocess.

    On bmcam000 / Pi Zero 2W, the exact RGB + temp-file HEIC procedure passed
    repeatedly when run in a tiny standalone Python process, but failed inside
    the heavier production module context. Keep the encoder isolated so it does
    not inherit Picamera2/OpenCV/BM serial runtime weight.
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

    debug_print("Running HEIC helper subprocess: " + " ".join(cmd))
    started = time.monotonic()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
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
        raise RuntimeError(
            f"HEIC helper failed with exit_code={result.returncode} "
            f"duration_sec={duration:.2f}"
        )

    if not os.path.exists(heic_output_path):
        raise ValueError(f"HEIC helper did not create output: {heic_output_path}")

    output_size = os.path.getsize(heic_output_path)
    if output_size <= 0:
        raise ValueError(f"HEIC helper created zero-byte output: {heic_output_path}")

    debug_print(
        f"HEIC helper completed: output={heic_output_path}, "
        f"bytes={output_size}, duration_sec={duration:.2f}"
    )
    return output_size, duration


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


def _format_start_metadata(start_metadata):
    """Return compact START IMG metadata suffix.

    This is deliberately short because START is one BM message and is separate
    from image chunks. It does not affect buffer generation or chunking.
    """
    if not start_metadata:
        return ""

    mapping = [
        ("rk", "image_res_key"),
        ("q", "image_quality"),
        ("tz", "timezone"),
        ("ws", "window_start"),
        ("we", "window_end"),
        ("sha", "software_sha"),
        ("hn", "hostname"),
    ]

    parts = []
    for label, key in mapping:
        value = start_metadata.get(key)
        if value is None or value == "":
            continue
        value = str(value).replace(" ", "_").replace(",", "_")
        if len(value) > 32:
            value = value[:32]
        parts.append(f"{label}={value}")

    if not parts:
        return ""
    return "meta: " + " ".join(parts)


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

    meta_text = _format_start_metadata(start_metadata)
    meta_suffix = f", {meta_text}" if meta_text else ""

    # Measure the full Pi-side send loop duration, including the existing pacing
    # sleeps between UART writes. This is the camera UART/application throughput.
    uart_start = time.monotonic()

    start_msg = (
        f"<START IMG> filename: {compressed_file_name}, "
        f"timestamp: {current_timestamp}, length: {num_buffers}"
        f"{meta_suffix}\n"
    )
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
    apply_bm_serial_runtime_settings(configure_serial=True)

    compressed_file_name, num_buffers, file_size_compressed = split_image_heic(
        image_path,
        image_quality=image_quality,
    )

    schedule_metadata = schedule_metadata or {}
    start_metadata = {
        "image_res_key": image_res_key,
        "image_quality": image_quality,
        "timezone": schedule_metadata.get("timezone"),
        "window_start": schedule_metadata.get("window_start"),
        "window_end": schedule_metadata.get("window_end"),
        "software_sha": get_software_sha(),
        "hostname": get_hostname(),
    }

    capture_metadata = load_capture_metadata(image_path)
    send_buffers(
        BUFFER_DIRECTORY,
        compressed_file_name,
        start_metadata=start_metadata,
        capture_metadata=capture_metadata,
    )
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
