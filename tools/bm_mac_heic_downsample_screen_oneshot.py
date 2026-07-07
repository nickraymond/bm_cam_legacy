#!/usr/bin/env python3
"""
Mac-side BM underwater image screening DOE.

Purpose:
  Take one or more high-resolution source images downloaded from a BM camera,
  crop a fixed square ROI, downsample it to a coarse screening ladder, encode
  each downsampled image to HEIC at a coarse quality sweep, decode HEIC back to
  JPEG for review, and generate contact-sheet outputs plus CSV summaries.

Install on Mac:
  python3 -m pip install pillow pillow-heif

Example, local downloaded source images:
  python3 bm_mac_heic_downsample_screen.py \
    --input ~/Downloads/bm_source_images \
    --output ~/Downloads/bm_heic_screen_001

Example, one-shot remote native full-resolution capture from bmcam000 + local screening:
  python3 bm_mac_heic_downsample_screen.py \
    --capture-remote \
    --remote-host bmcam000 \
    --remote-resolution-key native_full \
    --output ~/Downloads/bm_heic_screen_001

Default screening matrix:
  sizes:     2592, 1280, 720, 480
  qualities: 5, 25, 45, 65, 85

Notes:
  - Default ROI is the largest centered square in the source image.
    For a native 4608x2592 IMX708 source, that is x=1008, y=0, size=2592.
  - Use --roi x,y,size to lock a manual square ROI.
  - Results estimate BM chunk counts for 960, 900, and 300 byte base64 buffers.
  - With --capture-remote, the script SSHes to a Pi, captures one native
    full-resolution JPEG source image with rpicam-still/libcamera-still by
    default, downloads it with scp, then runs the Mac-side HEIC/downsample/contact-sheet workflow locally.
  - native_full intentionally does not pass --width/--height to libcamera-still;
    the Mac-side workflow crops/downsamples from the returned full camera image.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pillow_heif
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except Exception as exc:
    raise SystemExit(
        "Missing dependency. Install with:\n"
        "  python3 -m pip install pillow pillow-heif\n\n"
        f"Original error: {exc}"
    )

pillow_heif.register_heif_opener()

SCRIPT_VERSION = "2026-07-03-native-full-relative-path-v7"

DEFAULT_SIZES = [2592, 1280, 720, 480]
DEFAULT_QUALITIES = [5, 25, 45, 65, 85]
DEFAULT_BUFFER_CONFIGS = [
    (960, 16.0),
    (900, 16.0),
    (300, 5.0),
]

# Production BM camera resolution keys used for remote source capture.
# The Mac-side DOE can still downsample to arbitrary square sizes after download.
RESOLUTIONS: dict[str, tuple[int, int]] = {
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

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff"}


@dataclass(frozen=True)
class BufferConfig:
    size_bytes: int
    delay_seconds: float


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_token(value: Any, max_len: int = 90) -> str:
    text = str(value if value is not None else "na").strip()
    text = text.replace(" ", "_").replace("/", "-").replace(":", "-")
    text = "".join(ch for ch in text if ch.isalnum() or ch in "._-=")
    return text[:max_len] or "na"


def parse_int_list(values: list[str] | None, default: list[int]) -> list[int]:
    if not values:
        return list(default)
    out: list[int] = []
    for raw in values:
        for part in str(raw).replace(",", " ").split():
            if not part:
                continue
            value = int(part)
            if value <= 0:
                raise ValueError(f"size/quality values must be positive: {value}")
            out.append(value)
    return out


def parse_buffer_configs(values: list[str] | None) -> list[BufferConfig]:
    if not values:
        return [BufferConfig(size, delay) for size, delay in DEFAULT_BUFFER_CONFIGS]
    out: list[BufferConfig] = []
    for raw in values:
        for part in str(raw).replace(",", " ").split():
            if not part:
                continue
            if ":" in part:
                size_text, delay_text = part.split(":", 1)
                size = int(size_text)
                delay = float(delay_text)
            else:
                size = int(part)
                delay = 16.0 if size >= 900 else 5.0
            if size <= 0 or delay < 0:
                raise ValueError(f"invalid buffer config: {part!r}")
            out.append(BufferConfig(size, delay))
    return out


def parse_roi(raw: str | None) -> tuple[int, int, int] | None:
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace(" ", "").split(",")]
    if len(parts) != 3:
        raise ValueError("--roi must be x,y,size")
    x, y, size = (int(p) for p in parts)
    if size <= 0 or x < 0 or y < 0:
        raise ValueError("--roi requires x>=0, y>=0, size>0")
    return x, y, size


def find_images(input_path: Path, recursive: bool) -> list[Path]:
    input_path = input_path.expanduser().resolve()
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    pattern = "**/*" if recursive else "*"
    images = [p for p in input_path.glob(pattern) if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    # Avoid re-ingesting outputs if the user points to a previous output folder.
    skip_bits = {"heic", "decoded_jpeg", "downsample_refs", "contact_sheets"}
    filtered = []
    for p in images:
        if any(part in skip_bits for part in p.parts):
            continue
        filtered.append(p)
    return sorted(filtered)


def ssh_target(user: str, host: str) -> str:
    if "@" in host:
        return host
    return f"{user}@{host}" if user else host


def validate_resolution_key(key: str) -> tuple[int, int]:
    if key not in RESOLUTIONS:
        valid = ", ".join(sorted(RESOLUTIONS))
        raise ValueError(f"Unknown remote resolution key {key!r}. Valid keys: {valid}")
    return RESOLUTIONS[key]


NATIVE_FULL_KEYS = {"native_full", "native", "full", "full_native", "native_16x9"}


def is_native_full_key(key: str) -> bool:
    return str(key).strip().lower() in NATIVE_FULL_KEYS


def validate_remote_resolution_key(key: str) -> None:
    if is_native_full_key(key):
        return
    validate_resolution_key(key)


def run_checked(cmd: list[str], *, input_text: str | None = None, timeout_sec: float | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )
    if result.returncode != 0:
        stdout_tail = "\n".join((result.stdout or "").splitlines()[-20:])
        stderr_tail = "\n".join((result.stderr or "").splitlines()[-20:])
        raise RuntimeError(
            "Command failed:\n"
            f"  {' '.join(cmd)}\n"
            f"returncode={result.returncode}\n"
            f"stdout tail:\n{stdout_tail}\n"
            f"stderr tail:\n{stderr_tail}"
        )
    return result


def remote_capture_script() -> str:
    # Bash script sent over SSH to capture one direct JPEG source image on the Pi.
    # Default is native_full: do not pass --width/--height. This lets the camera
    # app choose the native still output (IMX708 typically 4608x2592), then the
    # Mac script crops/downsamples locally. This avoids the previous forced 4:3
    # scaler/crop path and keeps all HEIC work off the Pi.
    return r"""set -Eeuo pipefail
APP="${BM_REMOTE_APP:-/home/pi/BM_Devel_Pi}"
CAPTURE_SUBDIR="${BM_REMOTE_CAPTURE_SUBDIR:-mac_doe_source_captures}"
RES_KEY="${BM_RESOLUTION_KEY:-native_full}"
SETTLE_SEC="${BM_SETTLE_SEC:-2.0}"
CAPTURE_BACKEND="${BM_REMOTE_CAPTURE_BACKEND:-apps}"
JPEG_QUALITY="${BM_REMOTE_JPEG_QUALITY:-95}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME="$(hostname)"
SCRIPT_VERSION_REMOTE="2026-07-03-native-full-relative-path-v7"

case "${RES_KEY}" in
  native_full|native|full|full_native|native_16x9)
    IS_NATIVE_FULL="true"
    WIDTH="native"
    HEIGHT="native"
    SAFE_RES_KEY="native_full"
    ;;
  *)
    IS_NATIVE_FULL="false"
    SAFE_RES_KEY="$RES_KEY"
    read -r WIDTH HEIGHT < <(BM_RESOLUTION_KEY="$RES_KEY" /usr/bin/python3 - <<'REMOTE_RES_PY'
import os
RESOLUTIONS = {
    "native_12mp": (4608, 2592),
    "12MP": (4608, 2592),
    "4k": (3840, 2160),
    "2.7k": (2704, 1520),
    "1296p": (2304, 1296),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "480p": (854, 480),
    "360p": (640, 360),
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
key = os.environ["BM_RESOLUTION_KEY"]
if key not in RESOLUTIONS:
    raise SystemExit(f"Unknown resolution key {key!r}. Valid keys: native_full, {', '.join(sorted(RESOLUTIONS))}")
w, h = RESOLUTIONS[key]
print(w, h)
REMOTE_RES_PY
)
    ;;
esac

OUT_DIR="$APP/$CAPTURE_SUBDIR/${HOSTNAME}_${STAMP}_${SAFE_RES_KEY}"
mkdir -p "$OUT_DIR"

SOURCE_BASENAME="${HOSTNAME}_${STAMP}_${SAFE_RES_KEY}_source.jpg"
SOURCE_PATH="$OUT_DIR/$SOURCE_BASENAME"
METADATA_PATH="$OUT_DIR/${HOSTNAME}_${STAMP}_${SAFE_RES_KEY}_source.capture_metadata.json"
STDOUT_LOG="$OUT_DIR/${HOSTNAME}_${STAMP}_${SAFE_RES_KEY}_capture_stdout.log"
STDERR_LOG="$OUT_DIR/${HOSTNAME}_${STAMP}_${SAFE_RES_KEY}_capture_stderr.log"

printf '[REMOTE_CAPTURE] hostname=%s
' "$HOSTNAME"
printf '[REMOTE_CAPTURE] resolution_key=%s requested_size=%sx%s native_full=%s
' "$RES_KEY" "$WIDTH" "$HEIGHT" "$IS_NATIVE_FULL"
printf '[REMOTE_CAPTURE] source_path=%s
' "$SOURCE_PATH"
printf '[REMOTE_CAPTURE] settle_sec=%s
' "$SETTLE_SEC"
printf '[REMOTE_CAPTURE] script_version=%s
' "$SCRIPT_VERSION_REMOTE"
printf '[REMOTE_CAPTURE] backend=%s jpeg_quality=%s
' "$CAPTURE_BACKEND" "$JPEG_QUALITY"

TIMEOUT_MS=$(python3 - <<PY
print(max(1, int(float('$SETTLE_SEC') * 1000)))
PY
)

capture_with_rpicam_app() {
  local app_bin="$1"
  local app_name
  app_name="$(basename "$app_bin")"
  echo "[REMOTE_CAPTURE] trying ${app_bin}"

  # v7: run from OUT_DIR and write to a short relative filename. This mirrors the
  # manual command that passed on bmcam000 after cma=128M, and avoids any weirdness
  # from long absolute output paths or option aliases.
  local size_args=()
  if [ "$IS_NATIVE_FULL" != "true" ]; then
    size_args=(--width "$WIDTH" --height "$HEIGHT")
  fi

  echo "[REMOTE_CAPTURE] exact command v7: cd '$OUT_DIR' && $app_name -n --immediate --encoding jpg --quality $JPEG_QUALITY ${size_args[*]} -o '$SOURCE_BASENAME'"
  (
    cd "$OUT_DIR"
    "$app_bin" -n --immediate --encoding jpg --quality "$JPEG_QUALITY" "${size_args[@]}" -o "$SOURCE_BASENAME"
  ) >"$STDOUT_LOG" 2>"$STDERR_LOG" && return 0

  local rc=$?
  echo "[REMOTE_CAPTURE] immediate command failed rc=$rc; stderr tail:" >&2
  tail -n 80 "$STDERR_LOG" >&2 || true

  echo "[REMOTE_CAPTURE] retrying timed command v7: cd '$OUT_DIR' && $app_name -n -t ${TIMEOUT_MS} --encoding jpg --quality $JPEG_QUALITY ${size_args[*]} -o '$SOURCE_BASENAME'" >&2
  (
    cd "$OUT_DIR"
    "$app_bin" -n -t "$TIMEOUT_MS" --encoding jpg --quality "$JPEG_QUALITY" "${size_args[@]}" -o "$SOURCE_BASENAME"
  ) >"$STDOUT_LOG" 2>"$STDERR_LOG" && return 0

  rc=$?
  echo "[REMOTE_CAPTURE] timed command failed rc=$rc; stderr tail:" >&2
  tail -n 80 "$STDERR_LOG" >&2 || true
  return "$rc"
}
capture_with_picamera2_python() {
  echo "[REMOTE_CAPTURE] trying picamera2 fallback"
  BM_SOURCE_PATH="$SOURCE_PATH" BM_METADATA_PATH="$METADATA_PATH" BM_HOSTNAME="$HOSTNAME" BM_RESOLUTION_KEY="$RES_KEY" BM_IS_NATIVE_FULL="$IS_NATIVE_FULL" BM_WIDTH="$WIDTH" BM_HEIGHT="$HEIGHT" BM_SETTLE_SEC="$SETTLE_SEC" /usr/bin/python3 - <<'REMOTE_PY'
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
from picamera2 import Picamera2

def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return float(value)
    except Exception:
        return str(value)

def iso_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

source_path = Path(os.environ["BM_SOURCE_PATH"])
metadata_path = Path(os.environ["BM_METADATA_PATH"])
res_key = os.environ["BM_RESOLUTION_KEY"]
is_native_full = os.environ.get("BM_IS_NATIVE_FULL", "false") == "true"
hostname = os.environ["BM_HOSTNAME"]
settle_sec = float(os.environ.get("BM_SETTLE_SEC", "2.0"))

picam2 = Picamera2()
metadata = {}
try:
    if is_native_full:
        config = picam2.create_still_configuration(buffer_count=1)
    else:
        width = int(os.environ["BM_WIDTH"])
        height = int(os.environ["BM_HEIGHT"])
        config = picam2.create_still_configuration(main={"size": (width, height)}, buffer_count=1)
    picam2.configure(config)
    picam2.start()
    time.sleep(settle_sec)
    capture_metadata = picam2.capture_file(str(source_path))
    if isinstance(capture_metadata, dict):
        metadata = json_safe(capture_metadata)
    else:
        try:
            metadata = json_safe(picam2.capture_metadata() or {})
        except Exception as exc:
            metadata = {"metadata_error": str(exc)}
finally:
    try:
        picam2.stop()
    except Exception:
        pass
    try:
        picam2.close()
    except Exception:
        pass

metadata.update({
    "DOECapturePath": "remote_picamera2_capture_file_fallback",
    "DOEResolutionKey": res_key,
    "DOEIsNativeFull": is_native_full,
    "DOEHost": hostname,
    "DOECapturedAtUTC": iso_utc(),
})
metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
REMOTE_PY
}

CAPTURE_OK="false"
CAPTURE_METHOD="none"

if [ "$CAPTURE_BACKEND" = "apps" ] || [ "$CAPTURE_BACKEND" = "auto" ] || [ "$CAPTURE_BACKEND" = "rpicam" ]; then
  if command -v rpicam-still >/dev/null 2>&1; then
    if capture_with_rpicam_app "$(command -v rpicam-still)"; then
      CAPTURE_OK="true"
      CAPTURE_METHOD="rpicam-still"
    else
      echo "[REMOTE_CAPTURE] WARN: rpicam-still failed; stderr tail:" >&2
      tail -n 20 "$STDERR_LOG" >&2 || true
    fi
  fi
fi

if [ "$CAPTURE_OK" != "true" ] && { [ "$CAPTURE_BACKEND" = "apps" ] || [ "$CAPTURE_BACKEND" = "auto" ] || [ "$CAPTURE_BACKEND" = "libcamera" ]; }; then
  if command -v libcamera-still >/dev/null 2>&1; then
    if capture_with_rpicam_app "$(command -v libcamera-still)"; then
      CAPTURE_OK="true"
      CAPTURE_METHOD="libcamera-still"
    else
      echo "[REMOTE_CAPTURE] WARN: libcamera-still failed; stderr tail:" >&2
      tail -n 20 "$STDERR_LOG" >&2 || true
    fi
  fi
fi

if [ "$CAPTURE_OK" != "true" ] && { [ "$CAPTURE_BACKEND" = "auto" ] || [ "$CAPTURE_BACKEND" = "picamera2" ]; }; then
  if capture_with_picamera2_python >"$STDOUT_LOG" 2>"$STDERR_LOG"; then
    CAPTURE_OK="true"
    CAPTURE_METHOD="picamera2"
  else
    echo "[REMOTE_CAPTURE] WARN: picamera2 fallback failed; stderr tail:" >&2
    tail -n 20 "$STDERR_LOG" >&2 || true
  fi
fi

if [ ! -s "$SOURCE_PATH" ]; then
  echo "[REMOTE_CAPTURE] ERROR: capture failed or source file missing/empty" >&2
  echo "[REMOTE_CAPTURE] backend=$CAPTURE_BACKEND. Default apps mode never falls back to Picamera2." >&2
  echo "[REMOTE_CAPTURE] Try --remote-capture-backend auto only if you intentionally want Picamera2 fallback." >&2
  echo "[REMOTE_CAPTURE] stdout log: $STDOUT_LOG" >&2
  echo "[REMOTE_CAPTURE] stderr log: $STDERR_LOG" >&2
  exit 1
fi

read -r ACTUAL_WIDTH ACTUAL_HEIGHT < <(BM_SOURCE_PATH="$SOURCE_PATH" /usr/bin/python3 - <<'REMOTE_SIZE_PY'
import os
from PIL import Image
p = os.environ["BM_SOURCE_PATH"]
with Image.open(p) as im:
    print(im.width, im.height)
REMOTE_SIZE_PY
)

BM_METADATA_PATH="$METADATA_PATH" BM_SOURCE_PATH="$SOURCE_PATH" BM_HOSTNAME="$HOSTNAME" BM_RESOLUTION_KEY="$RES_KEY" BM_SAFE_RES_KEY="$SAFE_RES_KEY" BM_IS_NATIVE_FULL="$IS_NATIVE_FULL" BM_CAPTURE_METHOD="$CAPTURE_METHOD" BM_ACTUAL_WIDTH="$ACTUAL_WIDTH" BM_ACTUAL_HEIGHT="$ACTUAL_HEIGHT" BM_JPEG_QUALITY="$JPEG_QUALITY" /usr/bin/python3 - <<'REMOTE_META_PY'
import json, os
from datetime import datetime, timezone
from pathlib import Path

def iso_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

metadata_path = Path(os.environ["BM_METADATA_PATH"])
source_path = Path(os.environ["BM_SOURCE_PATH"])
metadata = {
    "DOECapturePath": "remote_rpicam_libcamera_app_native_full" if os.environ.get("BM_IS_NATIVE_FULL") == "true" else "remote_rpicam_libcamera_app_explicit_size",
    "DOECaptureMethod": os.environ.get("BM_CAPTURE_METHOD"),
    "DOEResolutionKey": os.environ.get("BM_RESOLUTION_KEY"),
    "DOESafeResolutionKey": os.environ.get("BM_SAFE_RES_KEY"),
    "DOEIsNativeFull": os.environ.get("BM_IS_NATIVE_FULL") == "true",
    "DOEHost": os.environ.get("BM_HOSTNAME"),
    "DOECapturedAtUTC": iso_utc(),
    "DOEJPEGQuality": int(os.environ.get("BM_JPEG_QUALITY", "95")),
    "DOEActualWidth": int(os.environ.get("BM_ACTUAL_WIDTH", "0")),
    "DOEActualHeight": int(os.environ.get("BM_ACTUAL_HEIGHT", "0")),
    "DOESourceSizeBytes": source_path.stat().st_size if source_path.exists() else None,
}
metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
REMOTE_META_PY

SOURCE_SIZE_BYTES="$(stat -c '%s' "$SOURCE_PATH")"
SOURCE_SIZE_KB="$(python3 - <<PY
print(round(int('$SOURCE_SIZE_BYTES') / 1024, 3))
PY
)"

printf '[REMOTE_CAPTURE] capture_method=%s
' "$CAPTURE_METHOD"
printf '[REMOTE_CAPTURE] actual_size=%sx%s
' "$ACTUAL_WIDTH" "$ACTUAL_HEIGHT"
printf '[REMOTE_CAPTURE] source_size=%s
' "$(du -h "$SOURCE_PATH" | awk '{print $1}')"

BM_CAPTURE_METHOD="$CAPTURE_METHOD" BM_CAPTURED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" BM_HOSTNAME="$HOSTNAME" BM_RESOLUTION_KEY="$RES_KEY" BM_SAFE_RES_KEY="$SAFE_RES_KEY" BM_IS_NATIVE_FULL="$IS_NATIVE_FULL" BM_SOURCE_PATH="$SOURCE_PATH" BM_METADATA_PATH="$METADATA_PATH" BM_SOURCE_SIZE_BYTES="$SOURCE_SIZE_BYTES" BM_SOURCE_SIZE_KB="$SOURCE_SIZE_KB" BM_WIDTH="$ACTUAL_WIDTH" BM_HEIGHT="$ACTUAL_HEIGHT" /usr/bin/python3 - <<'REMOTE_JSON_PY'
import json, os
payload = {
    "capture_method": os.environ["BM_CAPTURE_METHOD"],
    "captured_at_utc": os.environ["BM_CAPTURED_AT"],
    "hostname": os.environ["BM_HOSTNAME"],
    "resolution_key": os.environ["BM_RESOLUTION_KEY"],
    "safe_resolution_key": os.environ["BM_SAFE_RES_KEY"],
    "is_native_full": os.environ.get("BM_IS_NATIVE_FULL") == "true",
    "source_path": os.environ["BM_SOURCE_PATH"],
    "metadata_path": os.environ["BM_METADATA_PATH"],
    "source_size_bytes": int(os.environ["BM_SOURCE_SIZE_BYTES"]),
    "source_size_kb": float(os.environ["BM_SOURCE_SIZE_KB"]),
    "width_px": int(os.environ["BM_WIDTH"]),
    "height_px": int(os.environ["BM_HEIGHT"]),
}
print("REMOTE_CAPTURE_JSON=" + json.dumps(payload, sort_keys=True))
REMOTE_JSON_PY
"""

def capture_remote_source(
    *,
    output_root: Path,
    remote_host: str,
    remote_user: str,
    remote_resolution_key: str,
    remote_app: str,
    remote_capture_subdir: str,
    remote_settle_sec: float,
    remote_capture_backend: str,
    remote_jpeg_quality: int,
    remote_fallback_resolution_keys: list[str],
    ssh_connect_timeout: int,
    ssh_extra_args: list[str],
) -> Path:
    # Try the requested source first. Default is native_full, which intentionally
    # omits --width/--height on the Pi and lets libcamera output the native still.
    # If that fails, fallback keys can still be used as lower-res debug paths.
    candidate_keys: list[str] = []
    for key in [remote_resolution_key, *remote_fallback_resolution_keys]:
        key = str(key).strip()
        if key and key not in candidate_keys:
            validate_remote_resolution_key(key)
            candidate_keys.append(key)

    target = ssh_target(remote_user, remote_host)
    local_dir = output_root / "_remote_captures" / safe_token(remote_host)
    local_dir.mkdir(parents=True, exist_ok=True)

    ssh_cmd = [
        "ssh",
        "-o", f"ConnectTimeout={ssh_connect_timeout}",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=2",
        *ssh_extra_args,
        target,
        "bash", "-s",
    ]

    print("\n[REMOTE] capturing source image")
    print(f"[REMOTE] script_version={SCRIPT_VERSION}")
    print(f"[REMOTE] target={target} requested_resolution_key={remote_resolution_key} app={remote_app} backend={remote_capture_backend} jpeg_quality={remote_jpeg_quality}")
    if len(candidate_keys) > 1:
        print(f"[REMOTE] fallback_resolution_keys={' '.join(candidate_keys[1:])}")

    result: subprocess.CompletedProcess[str] | None = None
    successful_key: str | None = None
    failures: list[str] = []

    for attempt_index, key in enumerate(candidate_keys, start=1):
        if attempt_index > 1:
            print(f"[REMOTE] retrying with fallback resolution_key={key}")
        remote_script = (
            f"export BM_REMOTE_APP={json.dumps(remote_app)}\n"
            f"export BM_REMOTE_CAPTURE_SUBDIR={json.dumps(remote_capture_subdir)}\n"
            f"export BM_RESOLUTION_KEY={json.dumps(key)}\n"
            f"export BM_SETTLE_SEC={json.dumps(str(remote_settle_sec))}\n"
            f"export BM_REMOTE_CAPTURE_BACKEND={json.dumps(remote_capture_backend)}\n"
            f"export BM_REMOTE_JPEG_QUALITY={json.dumps(str(remote_jpeg_quality))}\n"
            + remote_capture_script()
        )
        try:
            result = run_checked(ssh_cmd, input_text=remote_script, timeout_sec=max(60, int(remote_settle_sec + 120)))
            successful_key = key
            break
        except RuntimeError as exc:
            failures.append(f"{key}: {exc}")
            print(f"[REMOTE] WARN: capture failed for resolution_key={key}")
            # Keep the console readable; the full final exception includes all failures if none work.
            tail = str(exc).splitlines()[-12:]
            print("\n".join(tail))

    if result is None or successful_key is None:
        raise RuntimeError(
            "Remote capture failed for all candidate resolution keys.\n"
            + "\n\n".join(failures)
        )

    if result.stdout:
        print(result.stdout.strip())

    payload = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("REMOTE_CAPTURE_JSON="):
            payload = json.loads(line.split("=", 1)[1])
    if not payload:
        raise RuntimeError(f"Remote capture succeeded but did not return REMOTE_CAPTURE_JSON. Output:\n{result.stdout}")

    remote_source = payload["source_path"]
    remote_metadata = payload.get("metadata_path")
    local_source = local_dir / Path(remote_source).name
    local_metadata = local_dir / Path(remote_metadata).name if remote_metadata else None

    scp_base = [
        "scp",
        "-q",
        "-o", f"ConnectTimeout={ssh_connect_timeout}",
        *ssh_extra_args,
    ]
    run_checked([*scp_base, f"{target}:{remote_source}", str(local_source)], timeout_sec=120)
    if remote_metadata and local_metadata:
        run_checked([*scp_base, f"{target}:{remote_metadata}", str(local_metadata)], timeout_sec=120)

    local_manifest = {
        "created_at_utc": iso_utc(),
        "script_version": SCRIPT_VERSION,
        "remote_host": remote_host,
        "remote_target": target,
        "requested_remote_resolution_key": remote_resolution_key,
        "successful_remote_resolution_key": successful_key,
        "candidate_remote_resolution_keys": candidate_keys,
        "remote_source_path": remote_source,
        "remote_metadata_path": remote_metadata,
        "local_source_path": str(local_source),
        "local_metadata_path": str(local_metadata) if local_metadata else None,
        "remote_payload": payload,
    }
    (local_dir / f"{local_source.stem}.remote_capture_manifest.json").write_text(
        json.dumps(local_manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    print(f"[REMOTE] downloaded source={local_source}")
    if successful_key != remote_resolution_key:
        print(f"[REMOTE] NOTE: requested {remote_resolution_key} failed; using fallback source {successful_key}")
    if local_metadata:
        print(f"[REMOTE] downloaded metadata={local_metadata}")
    return local_source

def font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt, fill=(25, 40, 55)) -> None:
    x0, y0, x1, y1 = box
    lines = str(text).splitlines() or [""]
    metrics = []
    total_h = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=fnt)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        metrics.append((line, w, h))
        total_h += h
    total_h += max(0, len(lines) - 1) * 4
    y = y0 + (y1 - y0 - total_h) / 2
    for line, w, h in metrics:
        draw.text((x0 + (x1 - x0 - w) / 2, y), line, font=fnt, fill=fill)
        y += h + 4


def fit_tile(img: Image.Image, tile_w: int, image_h: int, *, no_upscale: bool = True) -> Image.Image:
    img = img.convert("RGB")
    if no_upscale:
        scale = min(tile_w / img.width, image_h / img.height, 1.0)
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        if (new_w, new_h) != img.size:
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    else:
        img.thumbnail((tile_w, image_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (tile_w, image_h), (230, 236, 242))
    canvas.paste(img, ((tile_w - img.width) // 2, (image_h - img.height) // 2))
    return canvas


def estimate_for_buffer(base64_chars: int, buffer: BufferConfig, throughput_kbps: float, start_message_seconds: float) -> dict[str, Any]:
    chunks = math.ceil(base64_chars / buffer.size_bytes) if base64_chars else 0
    payload_kbits = (base64_chars * 8.0) / 1000.0
    link_minutes = payload_kbits / throughput_kbps / 60.0 if throughput_kbps > 0 else None
    paced_minutes = (start_message_seconds + chunks * buffer.delay_seconds) / 60.0
    estimated_minutes = max([v for v in [link_minutes, paced_minutes] if v is not None])
    return {
        f"chunks_{buffer.size_bytes}": chunks,
        f"delay_sec_{buffer.size_bytes}": buffer.delay_seconds,
        f"paced_min_{buffer.size_bytes}": round(paced_minutes, 3),
        f"estimated_tx_min_{buffer.size_bytes}": round(estimated_minutes, 3),
    }


def classify_screen(row: dict[str, Any], primary_buffer: int, pass_chunks: int, warn_chunks: int) -> str:
    chunks = int(row.get(f"chunks_{primary_buffer}") or 0)
    if chunks <= pass_chunks:
        return "pass"
    if chunks <= warn_chunks:
        return "warn"
    return "fail"


def source_stem(path: Path) -> str:
    stem = safe_token(path.stem)
    # Shorten common BM DOE suffixes without losing identity.
    stem = re.sub(r"_src-jpeg_source$", "", stem)
    return stem


def make_contact_sheet(
    *,
    rows: list[dict[str, Any]],
    output_path: Path,
    title: str,
    primary_buffer: int,
    tile_w: int,
    image_h: int,
    label_h: int,
) -> None:
    if not rows:
        return

    sizes = sorted({int(r["output_size_px"]) for r in rows}, reverse=True)
    qualities = sorted({int(r["quality"]) for r in rows})
    by_key = {(int(r["output_size_px"]), int(r["quality"])): r for r in rows}

    margin = 24
    row_label_w = 150
    header_h = 110
    quality_header_h = 38
    tile_h = image_h + label_h
    width = margin * 2 + row_label_w + len(qualities) * tile_w
    height = margin * 2 + header_h + quality_header_h + len(sizes) * tile_h

    sheet = Image.new("RGB", (width, height), (244, 247, 251))
    draw = ImageDraw.Draw(sheet)
    f_title = font(24, bold=True)
    f_head = font(16, bold=True)
    f_label = font(13)
    f_small = font(12)

    first = rows[0]
    draw.text((margin, margin), title, font=f_title, fill=(30, 50, 70))
    draw.text(
        (margin, margin + 34),
        f"source={first.get('source_filename')}  roi={first.get('roi_x')},{first.get('roi_y')},{first.get('roi_size_px')}  primary={primary_buffer}B chunks",
        font=f_label,
        fill=(80, 100, 120),
    )
    draw.text(
        (margin, margin + 58),
        "Rows: downsampled output size. Columns: HEIC quality. Image shown: HEIC decoded to JPEG.",
        font=f_small,
        fill=(100, 115, 130),
    )

    y0 = margin + header_h
    for col, q in enumerate(qualities):
        x = margin + row_label_w + col * tile_w
        draw.rectangle((x, y0, x + tile_w - 4, y0 + quality_header_h - 4), fill=(255, 255, 255), outline=(205, 217, 228))
        draw_centered_text(draw, (x, y0, x + tile_w - 4, y0 + quality_header_h - 4), f"HEIC q{q:03d}", f_head)

    y = y0 + quality_header_h
    for size in sizes:
        draw.rectangle((margin, y, margin + row_label_w - 8, y + tile_h - 8), fill=(255, 255, 255), outline=(205, 217, 228))
        ratio = size / float(first.get("roi_size_px") or size)
        draw_centered_text(draw, (margin, y, margin + row_label_w - 8, y + tile_h - 8), f"{size}×{size}\n{ratio:.1%} linear", f_head)

        for col, q in enumerate(qualities):
            x = margin + row_label_w + col * tile_w
            r = by_key.get((size, q))
            status = str(r.get("screen_status") if r else "missing").lower()
            fill = (255, 255, 255)
            if status == "pass":
                fill = (232, 246, 236)
            elif status == "warn":
                fill = (255, 247, 222)
            elif status == "fail":
                fill = (255, 235, 235)
            draw.rectangle((x, y, x + tile_w - 4, y + tile_h - 8), fill=fill, outline=(205, 217, 228))
            if not r:
                draw_centered_text(draw, (x, y, x + tile_w - 4, y + image_h), "missing", f_label, fill=(170, 80, 80))
                continue

            jpeg_path = Path(str(r["decoded_jpeg_path"]))
            if jpeg_path.exists():
                try:
                    with Image.open(jpeg_path) as img:
                        tile = fit_tile(img, tile_w - 16, image_h - 12)
                    sheet.paste(tile, (x + 8, y + 6))
                except Exception as exc:
                    draw_centered_text(draw, (x, y, x + tile_w - 4, y + image_h), f"open failed\n{exc}", f_small, fill=(170, 80, 80))
            else:
                draw_centered_text(draw, (x, y, x + tile_w - 4, y + image_h), f"file not found\n{jpeg_path.name}", f_small, fill=(170, 80, 80))

            label_y = y + image_h
            chunks = r.get(f"chunks_{primary_buffer}")
            tx_min = r.get(f"estimated_tx_min_{primary_buffer}")
            lines = [
                f"HEIC {float(r['heic_size_kb']):.1f} KB   b64 {int(r['base64_chars'])}",
                f"{primary_buffer}B: {chunks} chunks, est {float(tx_min):.1f} min, {r.get('screen_status','').upper()}",
                f"900B: {r.get('chunks_900','—')} ch   300B: {r.get('chunks_300','—')} ch",
                f"downsample {float(r['linear_scale_vs_roi']):.1%} linear / {float(r['area_scale_vs_roi']):.1%} area",
            ]
            for i, line in enumerate(lines):
                draw.text((x + 10, label_y + 8 + i * 18), line, font=f_small, fill=(45, 65, 85))

        y += tile_h

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=92)


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_for_source(
    *,
    source_path: Path,
    output_root: Path,
    sizes: list[int],
    qualities: list[int],
    roi: tuple[int, int, int] | None,
    buffers: list[BufferConfig],
    primary_buffer: int,
    throughput_kbps: float,
    start_message_seconds: float,
    jpeg_roundtrip_quality: int,
    save_downsample_refs: bool,
    pass_chunks: int,
    warn_chunks: int,
    tile_width: int,
    image_height: int,
) -> tuple[list[dict[str, Any]], Path]:
    source_path = source_path.expanduser().resolve()
    src_name = source_stem(source_path)
    run_dir = output_root / src_name
    heic_dir = run_dir / "heic"
    jpg_dir = run_dir / f"decoded_jpeg_q{jpeg_roundtrip_quality:03d}"
    ref_dir = run_dir / "downsample_refs"
    sheet_dir = run_dir / "contact_sheets"
    for d in (heic_dir, jpg_dir, ref_dir, sheet_dir):
        d.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    started = time.monotonic()

    with Image.open(source_path) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    source_w, source_h = img.size

    if roi is None:
        crop_size = min(source_w, source_h)
        roi_x = (source_w - crop_size) // 2
        roi_y = (source_h - crop_size) // 2
    else:
        roi_x, roi_y, crop_size = roi
        if roi_x + crop_size > source_w or roi_y + crop_size > source_h:
            raise ValueError(
                f"ROI {roi_x},{roi_y},{crop_size} is outside source image {source_w}x{source_h}: {source_path}"
            )

    square = img.crop((roi_x, roi_y, roi_x + crop_size, roi_y + crop_size))
    roi_preview_path = run_dir / f"{src_name}_roi_{roi_x}_{roi_y}_{crop_size}.jpg"
    square.save(roi_preview_path, format="JPEG", quality=95, optimize=True)

    valid_sizes = [s for s in sizes if s <= crop_size]
    skipped_sizes = [s for s in sizes if s > crop_size]
    if skipped_sizes:
        print(f"[WARN] {source_path.name}: skipped sizes larger than ROI {crop_size}: {skipped_sizes}")

    for size in valid_sizes:
        resized = square.resize((size, size), Image.Resampling.LANCZOS) if size != crop_size else square.copy()
        ref_path = ""
        if save_downsample_refs:
            ref = ref_dir / f"{src_name}_{size}x{size}_downsample_ref.jpg"
            resized.save(ref, format="JPEG", quality=95, optimize=True)
            ref_path = str(ref)

        for q in qualities:
            if q < 0 or q > 100:
                raise ValueError(f"HEIC quality must be 0-100, got {q}")
            base = f"{src_name}_{size}x{size}_heic-q{q:03d}"
            heic_path = heic_dir / f"{base}.heic"
            jpeg_path = jpg_dir / f"{base}_decoded-jpeg-q{jpeg_roundtrip_quality:03d}.jpg"

            t0 = time.monotonic()
            resized.save(heic_path, format="HEIF", quality=q)
            encode_sec = time.monotonic() - t0

            heic_bytes = heic_path.read_bytes()
            b64_chars = len(base64.b64encode(heic_bytes).decode("ascii"))
            with Image.open(heic_path) as dec:
                dec.convert("RGB").save(jpeg_path, format="JPEG", quality=jpeg_roundtrip_quality, optimize=True)

            linear_scale = size / crop_size
            area_scale = (size * size) / float(crop_size * crop_size)
            row: dict[str, Any] = {
                "run_id": output_root.name,
                "source_filename": source_path.name,
                "source_path": str(source_path),
                "source_width_px": source_w,
                "source_height_px": source_h,
                "source_size_bytes": source_path.stat().st_size,
                "source_size_kb": round(source_path.stat().st_size / 1024, 3),
                "roi_mode": "manual" if roi else "center_square_max",
                "roi_x": roi_x,
                "roi_y": roi_y,
                "roi_size_px": crop_size,
                "roi_preview_path": str(roi_preview_path),
                "output_size_px": size,
                "width_px": size,
                "height_px": size,
                "linear_scale_vs_roi": round(linear_scale, 6),
                "area_scale_vs_roi": round(area_scale, 6),
                "downsample_factor_linear": round(crop_size / size, 3),
                "quality": q,
                "heic_filename": heic_path.name,
                "heic_path": str(heic_path),
                "heic_size_bytes": heic_path.stat().st_size,
                "heic_size_kb": round(heic_path.stat().st_size / 1024, 3),
                "base64_chars": b64_chars,
                "base64_kchars": round(b64_chars / 1000, 3),
                "decoded_jpeg_path": str(jpeg_path),
                "decoded_jpeg_size_bytes": jpeg_path.stat().st_size,
                "decoded_jpeg_size_kb": round(jpeg_path.stat().st_size / 1024, 3),
                "downsample_ref_path": ref_path,
                "compression_duration_sec": round(encode_sec, 4),
                "link_payload_kbits": round((b64_chars * 8.0) / 1000.0, 3),
                "link_throughput_kbps": throughput_kbps,
                "start_message_seconds": start_message_seconds,
                "created_at_utc": iso_utc(),
            }
            for buffer in buffers:
                row.update(estimate_for_buffer(b64_chars, buffer, throughput_kbps, start_message_seconds))
            row["screen_status"] = classify_screen(row, primary_buffer, pass_chunks, warn_chunks)
            rows.append(row)

        try:
            resized.close()
        except Exception:
            pass

    try:
        square.close()
        img.close()
    except Exception:
        pass

    csv_path = run_dir / "results.csv"
    write_csv(rows, csv_path)
    manifest = {
        "source_path": str(source_path),
        "source_width_px": source_w,
        "source_height_px": source_h,
        "roi": {"x": roi_x, "y": roi_y, "size_px": crop_size, "mode": "manual" if roi else "center_square_max"},
        "requested_sizes": sizes,
        "valid_sizes": valid_sizes,
        "skipped_sizes": skipped_sizes,
        "qualities": qualities,
        "buffer_configs": [{"size_bytes": b.size_bytes, "delay_seconds": b.delay_seconds} for b in buffers],
        "primary_buffer": primary_buffer,
        "pass_chunks": pass_chunks,
        "warn_chunks": warn_chunks,
        "output_dir": str(run_dir),
        "results_csv": str(csv_path),
        "roi_preview_path": str(roi_preview_path),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "row_count": len(rows),
        "created_at_utc": iso_utc(),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    sheet_path = sheet_dir / "contact_heic_decoded_screen.jpg"
    make_contact_sheet(
        rows=rows,
        output_path=sheet_path,
        title="BM HEIC downsample screening · decoded view",
        primary_buffer=primary_buffer,
        tile_w=tile_width,
        image_h=image_height,
        label_h=112,
    )

    return rows, run_dir


def print_summary(rows: list[dict[str, Any]], primary_buffer: int) -> None:
    if not rows:
        print("No rows generated.")
        return
    print("\nSUMMARY by source / size / quality")
    print("-" * 96)
    print(f"{'status':7} {'size':>6} {'q':>4} {'HEIC KB':>9} {str(primary_buffer)+'B ch':>8} {'tx min':>8} {'source':<30}")
    for r in sorted(rows, key=lambda x: (x.get("source_filename", ""), -int(x["output_size_px"]), int(x["quality"]))):
        status = str(r.get("screen_status", ""))
        print(
            f"{status.upper():7} {int(r['output_size_px']):6d} {int(r['quality']):4d} "
            f"{float(r['heic_size_kb']):9.1f} {int(r.get(f'chunks_{primary_buffer}', 0)):8d} "
            f"{float(r.get(f'estimated_tx_min_{primary_buffer}', 0)):8.1f} {str(r.get('source_filename',''))[:30]:<30}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mac-side HEIC downsample screening DOE for BM underwater images.")
    parser.add_argument("--input", default=None, help="Source image file or directory of downloaded source images. Optional when --capture-remote is used.")
    parser.add_argument("--output", default=None, help="Output folder. Default: ./bm_heic_downsample_screen_<utc>")
    parser.add_argument("--recursive", action="store_true", help="Recursively search input directory for images.")
    parser.add_argument("--sizes", nargs="*", default=None, help="Output square sizes. Default: 2592 1280 720 480")
    parser.add_argument("--qualities", nargs="*", default=None, help="HEIC quality values. Default: 5 25 45 65 85")
    parser.add_argument("--roi", default=None, help="Manual square ROI as x,y,size. Default: largest centered square.")
    parser.add_argument(
        "--buffers",
        nargs="*",
        default=None,
        help="Buffer configs as size:delay_sec. Default: 960:16 900:16 300:5",
    )
    parser.add_argument("--primary-buffer", type=int, default=960, help="Primary buffer size for PASS/WARN/FAIL labels. Default: 960")
    parser.add_argument("--pass-chunks", type=int, default=25, help="PASS if chunks <= this using primary buffer. Default: 25")
    parser.add_argument("--warn-chunks", type=int, default=45, help="WARN if chunks <= this using primary buffer. Default: 45")
    parser.add_argument("--throughput-kbps", type=float, default=0.361, help="Observed link throughput estimate for tx minutes. Default: 0.361")
    parser.add_argument("--start-message-sec", type=float, default=5.0, help="START message pacing overhead. Default: 5")
    parser.add_argument("--jpeg-roundtrip-quality", type=int, default=95, help="Decoded JPEG review quality. Default: 95")
    parser.add_argument("--save-downsample-refs", action="store_true", help="Also save pre-HEIC downsample reference JPEGs.")
    parser.add_argument("--tile-width", type=int, default=420, help="Contact sheet tile width. Default: 420")
    parser.add_argument("--image-height", type=int, default=300, help="Contact sheet image area height. Default: 300")

    # Optional one-shot SSH capture from a BM camera before running the local Mac DOE.
    parser.add_argument("--capture-remote", action="store_true", help="SSH to a BM camera, capture one source JPEG, download it, then run the local screen.")
    parser.add_argument("--remote-host", default="bmcam000", help="Remote camera hostname or SSH target host. Default: bmcam000")
    parser.add_argument("--remote-user", default="pi", help="Remote SSH user. Ignored if --remote-host already contains user@host. Default: pi")
    parser.add_argument("--remote-resolution-key", default="native_full", help="Remote source capture mode/key. Default: native_full, which omits --width/--height and captures the camera app's native full-resolution still.")
    parser.add_argument(
        "--remote-fallback-resolution-keys",
        nargs="*",
        default=[],
        help="Optional fallback remote resolution keys if the requested capture cannot allocate buffers. Default: none. Example: --remote-fallback-resolution-keys XGA",
    )
    parser.add_argument("--remote-app", default="/home/pi/BM_Devel_Pi", help="Remote BM runtime directory. Default: /home/pi/BM_Devel_Pi")
    parser.add_argument("--remote-capture-subdir", default="mac_doe_source_captures", help="Subdirectory under --remote-app for source captures.")
    parser.add_argument("--remote-settle-sec", type=float, default=2.0, help="Camera settle time before remote capture. Default: 2")
    parser.add_argument("--remote-capture-backend", choices=["apps", "rpicam", "libcamera", "auto", "picamera2"], default="apps", help="Remote capture backend. Default apps = rpicam-still then libcamera-still, with no Picamera2 fallback.")
    parser.add_argument("--remote-jpeg-quality", type=int, default=95, help="Remote still JPEG quality for rpicam/libcamera app capture. Default: 95")
    parser.add_argument("--ssh-connect-timeout", type=int, default=10, help="SSH/scp connect timeout seconds. Default: 10")
    parser.add_argument("--ssh-extra-arg", action="append", default=[], help="Extra argument to pass to ssh and scp, repeatable, e.g. --ssh-extra-arg=-i --ssh-extra-arg=~/.ssh/key")

    args = parser.parse_args()

    args.input_path = Path(args.input).expanduser().resolve() if args.input else None
    args.output_root = Path(args.output).expanduser().resolve() if args.output else Path.cwd() / f"bm_heic_downsample_screen_{utc_stamp()}"
    args.sizes = parse_int_list(args.sizes, DEFAULT_SIZES)
    args.qualities = parse_int_list(args.qualities, DEFAULT_QUALITIES)
    args.roi_tuple = parse_roi(args.roi)
    args.buffer_configs = parse_buffer_configs(args.buffers)

    primary_sizes = {b.size_bytes for b in args.buffer_configs}
    if args.primary_buffer not in primary_sizes:
        raise SystemExit(f"--primary-buffer {args.primary_buffer} is not present in --buffers {sorted(primary_sizes)}")
    if args.capture_remote:
        validate_remote_resolution_key(args.remote_resolution_key)
        for key in args.remote_fallback_resolution_keys:
            validate_remote_resolution_key(key)
        if args.remote_jpeg_quality < 1 or args.remote_jpeg_quality > 100:
            raise SystemExit("--remote-jpeg-quality must be 1-100")
    if args.input_path is None and not args.capture_remote:
        raise SystemExit("Provide --input or use --capture-remote.")
    return args


def main() -> None:
    args = parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)

    images: list[Path] = []
    if args.capture_remote:
        remote_source = capture_remote_source(
            output_root=args.output_root,
            remote_host=args.remote_host,
            remote_user=args.remote_user,
            remote_resolution_key=args.remote_resolution_key,
            remote_app=args.remote_app,
            remote_capture_subdir=args.remote_capture_subdir,
            remote_settle_sec=args.remote_settle_sec,
            remote_capture_backend=args.remote_capture_backend,
            remote_jpeg_quality=args.remote_jpeg_quality,
            remote_fallback_resolution_keys=args.remote_fallback_resolution_keys,
            ssh_connect_timeout=args.ssh_connect_timeout,
            ssh_extra_args=args.ssh_extra_arg,
        )
        images.append(remote_source)

    if args.input_path is not None:
        images.extend(find_images(args.input_path, args.recursive))

    # Preserve order while removing duplicates.
    seen: set[Path] = set()
    images = [p for p in images if not (p in seen or seen.add(p))]
    if not images:
        raise SystemExit("No source images found. Provide --input or use --capture-remote.")

    print("BM HEIC downsample screening")
    print("=" * 72)
    print(f"input:       {args.input_path if args.input_path is not None else '(remote capture only)'}")
    print(f"output:      {args.output_root}")
    print(f"images:      {len(images)}")
    print(f"sizes:       {' '.join(str(x) for x in args.sizes)}")
    print(f"qualities:   {' '.join(str(x) for x in args.qualities)}")
    print(f"buffers:     {' '.join(f'{b.size_bytes}:{b.delay_seconds:g}' for b in args.buffer_configs)}")
    print(f"roi:         {args.roi or 'center_square_max'}")
    print("=" * 72)

    all_rows: list[dict[str, Any]] = []
    run_dirs: list[str] = []
    for source in images:
        print(f"\n[RUN] {source}")
        rows, run_dir = run_for_source(
            source_path=source,
            output_root=args.output_root,
            sizes=args.sizes,
            qualities=args.qualities,
            roi=args.roi_tuple,
            buffers=args.buffer_configs,
            primary_buffer=args.primary_buffer,
            throughput_kbps=args.throughput_kbps,
            start_message_seconds=args.start_message_sec,
            jpeg_roundtrip_quality=args.jpeg_roundtrip_quality,
            save_downsample_refs=args.save_downsample_refs,
            pass_chunks=args.pass_chunks,
            warn_chunks=args.warn_chunks,
            tile_width=args.tile_width,
            image_height=args.image_height,
        )
        all_rows.extend(rows)
        run_dirs.append(str(run_dir))
        print(f"[DONE] {run_dir}")

    combined_csv = args.output_root / "combined_results.csv"
    write_csv(all_rows, combined_csv)
    combined_manifest = {
        "created_at_utc": iso_utc(),
        "input": str(args.input_path) if args.input_path is not None else None,
        "capture_remote": bool(args.capture_remote),
        "remote_host": args.remote_host if args.capture_remote else None,
        "remote_resolution_key": args.remote_resolution_key if args.capture_remote else None,
        "output_root": str(args.output_root),
        "source_count": len(images),
        "row_count": len(all_rows),
        "sizes": args.sizes,
        "qualities": args.qualities,
        "buffer_configs": [{"size_bytes": b.size_bytes, "delay_seconds": b.delay_seconds} for b in args.buffer_configs],
        "primary_buffer": args.primary_buffer,
        "run_dirs": run_dirs,
        "combined_results_csv": str(combined_csv),
    }
    (args.output_root / "manifest.json").write_text(json.dumps(combined_manifest, indent=2, sort_keys=True), encoding="utf-8")

    print_summary(all_rows, args.primary_buffer)
    print("\nOutputs")
    print("-" * 72)
    print(f"combined_results_csv={combined_csv}")
    print(f"output_root={args.output_root}")
    print("contact sheets are inside each source subfolder: contact_sheets/contact_heic_decoded_screen.jpg")


if __name__ == "__main__":
    main()
