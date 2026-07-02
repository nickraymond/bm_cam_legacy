#!/usr/bin/env python3
"""
BM camera image quality DOE capture/compression benchmark.

Runs on the Raspberry Pi camera. For each requested resolution, captures source
reference images and compresses each source reference to HEIC at each requested
quality value.

Source modes:
  jpeg  = production-like direct Picamera2.capture_file(...jpg) by default.
          This avoids holding a large RGB frame in memory and matches the current
          production capture path in BM_Devel_Pi/process_image_v2.py.
  png   = lossless PNG source created from a camera-processed RGB frame.

If both jpeg and png are requested together, the script uses the RGB frame path
for both source files so JPEG-vs-PNG comparisons come from the exact same frame.

It does NOT transmit over Bristlemouth and does NOT modify camera_schedule.yaml.

Default output:
  /home/pi/BM_Devel_Pi/doe_runs/<run_id>/
    images/*.jpg
    images/*.png
    images/*.heic
    results.csv
    manifest.json

Quality convention mirrors production HEIC compression:
  lower quality value = smaller file / more compression / lower visual quality
  higher quality value = larger file / less compression / higher visual quality
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pillow_heif
    from PIL import Image
    from picamera2 import Picamera2
except Exception as exc:  # pragma: no cover - intended to run on Pi
    print(f"ERROR: missing required Pi imaging dependency: {exc}", file=sys.stderr)
    raise

pillow_heif.register_heif_opener()

BUFFER_SIZE = 300
DEFAULT_OUTPUT_ROOT = Path("/home/pi/BM_Devel_Pi/doe_runs")
CAPTURE_METADATA_SUFFIX = ".capture_metadata.json"

# Production resolution keys copied from BM_Devel_Pi/process_image_v2.py,
# plus DOE-only square keys for resolution/aspect-ratio testing.
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

    # DOE-only square presets. These are intentionally local to this script.
    "420sq": (420, 420),
    "480sq": (480, 480),
    "720sq": (720, 720),
}

SOURCE_MODES = ("jpeg", "png")

METADATA_KEYS = [
    "ExposureTime",
    "AnalogueGain",
    "DigitalGain",
    "ColourGains",
    "ColourTemperature",
    "LensPosition",
    "AfState",
    "AfMode",
    "FocusFoM",
    "Lux",
    "FrameDuration",
    "SensorTemperature",
]


CSV_FIELDNAMES = [
    "run_id",
    "hostname",
    "timestamp_utc",
    "resolution_key",
    "width_px",
    "height_px",
    "source_mode",
    "source_format",
    "source_jpeg_quality",
    "quality",
    "source_filename",
    "source_path",
    "source_size_bytes",
    "source_size_kb",
    "heic_filename",
    "heic_path",
    "heic_size_bytes",
    "heic_size_kb",
    "base64_chars",
    "estimated_bm_buffer_size_bytes",
    "estimated_bm_buffers",
    "compression_ratio_heic_to_source",
    "compression_duration_sec",
    "link_throughput_kbps",
    "link_payload_kbits",
    "estimated_link_minutes",
    "estimated_paced_minutes",
    "estimated_transmit_minutes",
    "target_transmit_minutes",
    "hard_transmit_minutes",
    "link_budget_status",
    "link_budget_margin_minutes",
    "per_buffer_seconds",
    "start_message_seconds",
    "error",
] + [f"metadata_{key}" for key in METADATA_KEYS]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_token(value: Any, max_len: int = 80) -> str:
    text = str(value if value is not None else "na").strip()
    text = text.replace(" ", "_").replace("/", "-").replace(":", "-")
    text = "".join(ch for ch in text if ch.isalnum() or ch in "._-=")
    return text[:max_len] or "na"


def validate_resolution_key(key: str) -> tuple[int, int]:
    if key not in RESOLUTIONS:
        raise ValueError(f"Unknown resolution key {key!r}. Valid keys: {', '.join(sorted(RESOLUTIONS))}")
    return RESOLUTIONS[key]


def validate_quality(q: int) -> int:
    q = int(q)
    if not 0 <= q <= 100:
        raise ValueError("quality values must be between 0 and 100")
    return q


def validate_source_mode(mode: str) -> str:
    mode = str(mode).strip().lower()
    if mode not in SOURCE_MODES:
        raise ValueError(f"Unknown source mode {mode!r}. Valid source modes: {', '.join(SOURCE_MODES)}")
    return mode


def json_safe(value: Any) -> Any:
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


def capture_rgb_frame(
    resolution_key: str,
    settle_sec: float,
    *,
    fallback_array_color_order: str = "bgr",
) -> tuple[Image.Image, dict[str, Any], str]:
    """Capture one camera-processed RGB frame and return a PIL image + metadata.

    This avoids comparing PNG and JPEG source modes from different moments.
    Both source files are derived from the exact same captured frame.

    Important color note:
    - The previous DOE script used request.make_array("main") with RGB888 and
      Image.fromarray(array). On bmcam001 this produced swapped red/blue colors.
    - Picamera2's request.make_image("main") builds a PIL image using Picamera2's
      stream-format handling and is the preferred path here.
    - The array fallback remains for compatibility, with an explicit BGR->RGB
      channel swap by default because that matches the observed failure mode.
    """
    width, height = validate_resolution_key(resolution_key)
    print(f"[DOE] Capturing RGB frame {resolution_key} {width}x{height}")

    picam2 = Picamera2()
    capture_path = "make_image"
    try:
        config = picam2.create_still_configuration(main={"size": (width, height)})
        picam2.configure(config)
        picam2.start()
        time.sleep(settle_sec)

        request = picam2.capture_request()
        try:
            metadata = request.get_metadata() or {}
            try:
                image = request.make_image("main").convert("RGB")
            except Exception as exc:
                capture_path = f"make_array_{fallback_array_color_order}_fallback"
                print(f"[DOE] WARN: request.make_image failed ({exc}); using array fallback")
                array = request.make_array("main")
                if fallback_array_color_order.lower() == "bgr" and getattr(array, "ndim", 0) == 3 and array.shape[2] >= 3:
                    array = array[:, :, :3][:, :, ::-1]
                elif getattr(array, "ndim", 0) == 3 and array.shape[2] > 3:
                    array = array[:, :, :3]
                image = Image.fromarray(array).convert("RGB")
        finally:
            request.release()
    finally:
        try:
            picam2.stop()
        except Exception:
            pass
        try:
            picam2.close()
        except Exception:
            pass

    return image, json_safe(metadata or {}), capture_path



def capture_jpeg_direct(
    resolution_key: str,
    settle_sec: float,
    *,
    hostname: str,
    capture_ts: str,
    image_dir: Path,
) -> tuple[Path, dict[str, Any], str]:
    """Capture a production-like JPEG source using Picamera2.capture_file.

    This mirrors BM_Devel_Pi/process_image_v2.py more closely than the RGB-frame
    DOE path and greatly reduces peak memory for JPEG-only high-resolution tests.
    Picamera2/libcamera chooses its normal JPEG encoder settings; this function
    intentionally does not force a source JPEG quality knob.
    """
    width, height = validate_resolution_key(resolution_key)
    print(f"[DOE] Capturing production JPEG {resolution_key} {width}x{height}")

    source_name = (
        f"{safe_token(hostname)}_{capture_ts}_{safe_token(resolution_key)}_{width}x{height}"
        f"_src-jpeg_source.jpg"
    )
    source_path = image_dir / source_name

    picam2 = Picamera2()
    metadata: dict[str, Any] = {}
    try:
        config = picam2.create_still_configuration(main={"size": (width, height)})
        picam2.configure(config)
        picam2.start()
        time.sleep(settle_sec)

        capture_metadata = None
        try:
            capture_metadata = picam2.capture_file(str(source_path))
        except TypeError:
            picam2.capture_file(str(source_path))

        if isinstance(capture_metadata, dict):
            metadata = json_safe(capture_metadata)
        else:
            try:
                metadata = json_safe(picam2.capture_metadata() or {})
            except Exception as exc:
                print(f"[DOE] WARN: capture metadata unavailable after direct JPEG capture: {exc}")
                metadata = {}
    finally:
        try:
            picam2.stop()
        except Exception:
            pass
        try:
            picam2.close()
        except Exception:
            pass

    metadata["DOECapturePath"] = "capture_file_jpeg"
    metadata["DOESourceJPEGMode"] = "production_direct"

    sidecar_path = Path(f"{source_path}{CAPTURE_METADATA_SUFFIX}")
    sidecar_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return source_path, metadata, "capture_file_jpeg"


def save_source_images(
    *,
    frame: Image.Image,
    metadata: dict[str, Any],
    hostname: str,
    capture_ts: str,
    resolution_key: str,
    image_dir: Path,
    source_modes: list[str],
    source_jpeg_quality: int,
) -> dict[str, Path]:
    width, height = frame.size
    out: dict[str, Path] = {}

    for source_mode in source_modes:
        source_mode = validate_source_mode(source_mode)
        ext = "jpg" if source_mode == "jpeg" else "png"
        source_name = (
            f"{safe_token(hostname)}_{capture_ts}_{safe_token(resolution_key)}_{width}x{height}"
            f"_src-{source_mode}_source.{ext}"
        )
        source_path = image_dir / source_name

        if source_mode == "jpeg":
            frame.save(source_path, format="JPEG", quality=source_jpeg_quality, optimize=True)
        elif source_mode == "png":
            frame.save(source_path, format="PNG", optimize=True)
        else:  # defensive; validate_source_mode should catch this
            raise ValueError(f"Unsupported source mode: {source_mode}")

        sidecar_path = Path(f"{source_path}{CAPTURE_METADATA_SUFFIX}")
        sidecar_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        out[source_mode] = source_path

    return out


def estimate_bm_buffers(image_bytes: bytes) -> tuple[int, int]:
    b64_chars = len(base64.b64encode(image_bytes).decode("ascii"))
    buffers = math.ceil(b64_chars / BUFFER_SIZE) if b64_chars else 0
    return b64_chars, buffers


def estimate_link_budget(
    *,
    base64_chars: int,
    estimated_buffers: int,
    throughput_kbps: float,
    target_minutes: float,
    hard_minutes: float,
    per_buffer_seconds: float,
    start_message_seconds: float,
) -> dict[str, Any]:
    """Estimate whether a HEIC payload fits the BM/Sofar transmission budget.

    Two separate constraints are useful for this project:
      1. Payload throughput budget: base64 payload bits / observed Sofar/API kbps.
      2. Camera-side pacing budget: the legacy sender sleeps between BM messages.

    The pass/fail status uses the slower of the two estimates. This is conservative
    and avoids approving a setting that fits one model but fails the other.
    """
    throughput_kbps = float(throughput_kbps)
    target_minutes = float(target_minutes)
    hard_minutes = float(hard_minutes)
    per_buffer_seconds = float(per_buffer_seconds)
    start_message_seconds = float(start_message_seconds)

    payload_kbits = (int(base64_chars) * 8.0) / 1000.0
    link_minutes = (payload_kbits / throughput_kbps / 60.0) if throughput_kbps > 0 else None

    # Approximate the production sender's intentional pacing: one START sleep plus
    # one sleep after each image chunk. END message overhead is intentionally ignored
    # here because it is small versus the image payload/chunk pacing.
    paced_minutes = (start_message_seconds + int(estimated_buffers) * per_buffer_seconds) / 60.0

    comparable = [v for v in [link_minutes, paced_minutes] if v is not None]
    estimated_minutes = max(comparable) if comparable else None

    if estimated_minutes is None:
        status = "unknown"
        margin_minutes = None
    elif estimated_minutes <= target_minutes:
        status = "pass"
        margin_minutes = target_minutes - estimated_minutes
    elif estimated_minutes <= hard_minutes:
        status = "warn"
        margin_minutes = hard_minutes - estimated_minutes
    else:
        status = "fail"
        margin_minutes = hard_minutes - estimated_minutes

    return {
        "link_throughput_kbps": throughput_kbps,
        "link_payload_kbits": round(payload_kbits, 3),
        "estimated_link_minutes": round(link_minutes, 3) if link_minutes is not None else None,
        "estimated_paced_minutes": round(paced_minutes, 3),
        "estimated_transmit_minutes": round(estimated_minutes, 3) if estimated_minutes is not None else None,
        "target_transmit_minutes": target_minutes,
        "hard_transmit_minutes": hard_minutes,
        "link_budget_status": status,
        "link_budget_margin_minutes": round(margin_minutes, 3) if margin_minutes is not None else None,
        "per_buffer_seconds": per_buffer_seconds,
        "start_message_seconds": start_message_seconds,
    }


def compress_source_to_heic(source_path: Path, output_path: Path, quality: int) -> tuple[int, int, int, float]:
    quality = validate_quality(quality)
    start = time.monotonic()
    with Image.open(source_path) as img:
        img.convert("RGB").save(output_path, format="HEIF", quality=quality)
    duration_sec = time.monotonic() - start
    data = output_path.read_bytes()
    b64_chars, buffers = estimate_bm_buffers(data)
    return len(data), b64_chars, buffers, duration_sec


def read_image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def build_row(
    *,
    hostname: str,
    run_id: str,
    resolution_key: str,
    width: int,
    height: int,
    source_mode: str,
    source_jpeg_quality: int | None,
    quality: int,
    source_path: Path,
    heic_path: Path,
    metadata: dict[str, Any],
    compressed_size_bytes: int,
    base64_chars: int,
    estimated_buffers: int,
    compression_duration_sec: float,
    link_budget: dict[str, Any],
) -> dict[str, Any]:
    source_size_bytes = source_path.stat().st_size
    ratio = round(compressed_size_bytes / source_size_bytes, 6) if source_size_bytes else None

    row: dict[str, Any] = {
        "run_id": run_id,
        "hostname": hostname,
        "timestamp_utc": iso_utc(),
        "resolution_key": resolution_key,
        "width_px": width,
        "height_px": height,
        "source_mode": source_mode,
        "source_format": "JPEG" if source_mode == "jpeg" else "PNG",
        "source_jpeg_quality": source_jpeg_quality if source_mode == "jpeg" else None,
        "quality": quality,
        "source_filename": source_path.name,
        "source_path": str(source_path),
        "source_size_bytes": source_size_bytes,
        "source_size_kb": round(source_size_bytes / 1024, 3),
        "heic_filename": heic_path.name,
        "heic_path": str(heic_path),
        "heic_size_bytes": compressed_size_bytes,
        "heic_size_kb": round(compressed_size_bytes / 1024, 3),
        "base64_chars": base64_chars,
        "estimated_bm_buffer_size_bytes": BUFFER_SIZE,
        "estimated_bm_buffers": estimated_buffers,
        "compression_ratio_heic_to_source": ratio,
        "compression_duration_sec": round(compression_duration_sec, 4),
        **link_budget,
    }

    for key in METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, (list, tuple)):
            value = json.dumps(value)
        row[f"metadata_{key}"] = value

    return row



def append_csv_row(csv_path: Path, row: dict[str, Any]) -> None:
    """Append one result row immediately so partial runs remain useful."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    exists = csv_path.exists() and csv_path.stat().st_size > 0
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def build_error_row(
    *,
    hostname: str,
    run_id: str,
    resolution_key: str,
    width: int | None,
    height: int | None,
    source_mode: str,
    source_jpeg_quality: int | None,
    quality: int,
    source_path: Path | None,
    metadata: dict[str, Any],
    error: str,
    link_budget_defaults: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "run_id": run_id,
        "hostname": hostname,
        "timestamp_utc": iso_utc(),
        "resolution_key": resolution_key,
        "width_px": width,
        "height_px": height,
        "source_mode": source_mode,
        "source_format": "JPEG" if source_mode == "jpeg" else "PNG",
        "source_jpeg_quality": source_jpeg_quality if source_mode == "jpeg" else None,
        "quality": quality,
        "source_filename": source_path.name if source_path else None,
        "source_path": str(source_path) if source_path else None,
        "source_size_bytes": source_path.stat().st_size if source_path and source_path.exists() else None,
        "source_size_kb": round(source_path.stat().st_size / 1024, 3) if source_path and source_path.exists() else None,
        "heic_filename": None,
        "heic_path": None,
        "heic_size_bytes": None,
        "heic_size_kb": None,
        "base64_chars": None,
        "estimated_bm_buffer_size_bytes": BUFFER_SIZE,
        "estimated_bm_buffers": None,
        "compression_ratio_heic_to_source": None,
        "compression_duration_sec": None,
        "link_budget_status": "error",
        "error": error[:500],
        **link_budget_defaults,
    }
    for key in METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, (list, tuple)):
            value = json.dumps(value)
        row[f"metadata_{key}"] = value
    return row


def run_doe(args: argparse.Namespace) -> Path:
    hostname = socket.gethostname().strip() or "unknown"
    run_id = args.run_id or f"{safe_token(hostname)}_{safe_token(args.tag)}_{utc_stamp()}"
    run_dir = Path(args.output_root).expanduser().resolve() / run_id
    image_dir = run_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "results.csv"

    manifest = {
        "run_id": run_id,
        "hostname": hostname,
        "started_at_utc": iso_utc(),
        "output_dir": str(run_dir),
        "resolutions": args.resolutions,
        "source_modes": args.source_modes,
        "source_jpeg_quality": args.source_jpeg_quality,
        "qualities": args.qualities,
        "buffer_size": BUFFER_SIZE,
        "link_budget": {
            "throughput_kbps": args.link_throughput_kbps,
            "target_transmit_minutes": args.target_transmit_min,
            "hard_transmit_minutes": args.hard_transmit_min,
            "per_buffer_seconds": args.per_buffer_sec,
            "start_message_seconds": args.start_message_sec,
        },
        "fallback_array_color_order": args.fallback_array_color_order,
        "jpeg_capture_mode": args.jpeg_capture_mode,
        "transmit": False,
        "row_count": 0,
        "error_count": 0,
        "note": (
            "For JPEG-only runs, captures a production-like source JPEG directly with "
            "Picamera2.capture_file to reduce memory. For PNG or mixed JPEG+PNG runs, "
            "captures one RGB frame so source modes are derived from the same frame. "
            "No BM transmission performed. Results are written incrementally."
        ),
    }
    write_manifest(run_dir, manifest)

    print(f"[DOE] Output: {run_dir}")
    print(f"[DOE] Source modes: {', '.join(args.source_modes)}")
    print(f"[DOE] HEIC qualities: {', '.join(str(q) for q in args.qualities)}")
    print(f"[DOE] JPEG capture mode: {args.jpeg_capture_mode}")

    rows: list[dict[str, Any]] = []
    budget_counts: dict[str, int] = {}
    jpeg_only_direct = args.source_modes == ["jpeg"] and args.jpeg_capture_mode == "direct"

    link_budget_defaults = {
        "link_throughput_kbps": args.link_throughput_kbps,
        "link_payload_kbits": None,
        "estimated_link_minutes": None,
        "estimated_paced_minutes": None,
        "estimated_transmit_minutes": None,
        "target_transmit_minutes": args.target_transmit_min,
        "hard_transmit_minutes": args.hard_transmit_min,
        "link_budget_margin_minutes": None,
        "per_buffer_seconds": args.per_buffer_sec,
        "start_message_seconds": args.start_message_sec,
    }

    for res_key in args.resolutions:
        requested_w, requested_h = validate_resolution_key(res_key)
        capture_ts = utc_stamp()
        source_paths: dict[str, Path] = {}
        metadata: dict[str, Any] = {}
        width = requested_w
        height = requested_h

        try:
            if jpeg_only_direct:
                source_path, metadata, capture_path = capture_jpeg_direct(
                    res_key,
                    args.settle_sec,
                    hostname=hostname,
                    capture_ts=capture_ts,
                    image_dir=image_dir,
                )
                source_paths = {"jpeg": source_path}
                actual_w, actual_h = read_image_size(source_path)
                width = actual_w or requested_w
                height = actual_h or requested_h
                print(
                    f"[DOE] Captured {res_key}: requested={requested_w}x{requested_h}, "
                    f"actual={width}x{height}, path={capture_path}"
                )
            else:
                frame, metadata, capture_path = capture_rgb_frame(
                    res_key,
                    args.settle_sec,
                    fallback_array_color_order=args.fallback_array_color_order,
                )
                width, height = frame.size
                metadata["DOECapturePath"] = capture_path
                print(
                    f"[DOE] Captured {res_key}: requested={requested_w}x{requested_h}, "
                    f"actual={width}x{height}, path={capture_path}"
                )
                source_paths = save_source_images(
                    frame=frame,
                    metadata=metadata,
                    hostname=hostname,
                    capture_ts=capture_ts,
                    resolution_key=res_key,
                    image_dir=image_dir,
                    source_modes=args.source_modes,
                    source_jpeg_quality=args.source_jpeg_quality,
                )
                # Release the large RGB frame before HEIC compression.
                try:
                    frame.close()
                except Exception:
                    pass
                del frame
        except Exception as exc:
            print(f"[DOE] ERROR: capture failed for {res_key}: {exc}", file=sys.stderr)
            for source_mode in args.source_modes:
                for quality in args.qualities:
                    row = build_error_row(
                        hostname=hostname,
                        run_id=run_id,
                        resolution_key=res_key,
                        width=width,
                        height=height,
                        source_mode=source_mode,
                        source_jpeg_quality=args.source_jpeg_quality,
                        quality=quality,
                        source_path=None,
                        metadata=metadata,
                        error=f"capture failed: {exc}",
                        link_budget_defaults=link_budget_defaults,
                    )
                    append_csv_row(csv_path, row)
                    rows.append(row)
                    budget_counts["error"] = budget_counts.get("error", 0) + 1
            manifest["row_count"] = len(rows)
            manifest["error_count"] = budget_counts.get("error", 0)
            manifest["link_budget_counts"] = budget_counts
            write_manifest(run_dir, manifest)
            continue

        for source_mode in args.source_modes:
            source_path = source_paths[source_mode]
            actual_w, actual_h = read_image_size(source_path)
            width = actual_w or width
            height = actual_h or height
            source_size_kb = source_path.stat().st_size / 1024
            print(f"[DOE] Source {source_mode}: {source_path.name}: {width}x{height}, {source_size_kb:.1f} KB")

            for quality in args.qualities:
                q = validate_quality(quality)
                temp_path = image_dir / (
                    f"{safe_token(hostname)}_{utc_stamp()}_{safe_token(res_key)}_{width}x{height}"
                    f"_src-{source_mode}_q{q:03d}_pending.heic"
                )
                final_path: Path | None = None

                try:
                    heic_bytes, b64_chars, buffers, duration_sec = compress_source_to_heic(source_path, temp_path, q)
                    final_name = (
                        f"{safe_token(hostname)}_{utc_stamp()}_{safe_token(res_key)}_{width}x{height}"
                        f"_src-{source_mode}_q{q:03d}_{heic_bytes / 1024:.1f}KB_{buffers:03d}buf.heic"
                    )
                    final_path = image_dir / final_name
                    temp_path.rename(final_path)

                    link_budget = estimate_link_budget(
                        base64_chars=b64_chars,
                        estimated_buffers=buffers,
                        throughput_kbps=args.link_throughput_kbps,
                        target_minutes=args.target_transmit_min,
                        hard_minutes=args.hard_transmit_min,
                        per_buffer_seconds=args.per_buffer_sec,
                        start_message_seconds=args.start_message_sec,
                    )

                    row = build_row(
                        hostname=hostname,
                        run_id=run_id,
                        resolution_key=res_key,
                        width=width,
                        height=height,
                        source_mode=source_mode,
                        source_jpeg_quality=None if jpeg_only_direct else args.source_jpeg_quality,
                        quality=q,
                        source_path=source_path,
                        heic_path=final_path,
                        metadata=metadata,
                        compressed_size_bytes=heic_bytes,
                        base64_chars=b64_chars,
                        estimated_buffers=buffers,
                        compression_duration_sec=duration_sec,
                        link_budget=link_budget,
                    )
                    row["error"] = None
                    append_csv_row(csv_path, row)
                    rows.append(row)
                    status = str(link_budget["link_budget_status"])
                    budget_counts[status] = budget_counts.get(status, 0) + 1

                    print(
                        f"[DOE] {res_key} src-{source_mode} q{q:03d}: {heic_bytes / 1024:.1f} KB, "
                        f"base64={b64_chars}, est_buffers={buffers}, "
                        f"est_tx={link_budget['estimated_transmit_minutes']:.1f}min, "
                        f"budget={link_budget['link_budget_status'].upper()}, "
                        f"compress={duration_sec:.2f}s"
                    )
                except Exception as exc:
                    try:
                        if temp_path.exists():
                            temp_path.unlink()
                    except Exception:
                        pass
                    print(f"[DOE] ERROR: compression failed for {res_key} src-{source_mode} q{q:03d}: {exc}", file=sys.stderr)
                    row = build_error_row(
                        hostname=hostname,
                        run_id=run_id,
                        resolution_key=res_key,
                        width=width,
                        height=height,
                        source_mode=source_mode,
                        source_jpeg_quality=None if jpeg_only_direct else args.source_jpeg_quality,
                        quality=q,
                        source_path=source_path,
                        metadata=metadata,
                        error=f"compression failed: {exc}",
                        link_budget_defaults=link_budget_defaults,
                    )
                    append_csv_row(csv_path, row)
                    rows.append(row)
                    budget_counts["error"] = budget_counts.get("error", 0) + 1

                manifest["row_count"] = len(rows)
                manifest["error_count"] = budget_counts.get("error", 0)
                manifest["link_budget_counts"] = budget_counts
                manifest["last_update_utc"] = iso_utc()
                write_manifest(run_dir, manifest)

    manifest["finished_at_utc"] = iso_utc()
    manifest["row_count"] = len(rows)
    manifest["error_count"] = budget_counts.get("error", 0)
    manifest["link_budget_counts"] = budget_counts
    write_manifest(run_dir, manifest)

    print(f"[DOE] Wrote {csv_path}")
    print(f"DOE_OUTPUT_DIR={run_dir}")
    return run_dir

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture BM camera resolution/source/HEIC-quality DOE images without transmission.")
    parser.add_argument("--resolutions", nargs="+", default=["480p"], help="Resolution keys to capture.")
    parser.add_argument("--source-modes", nargs="+", default=["jpeg", "png"], help="Source modes: jpeg png")
    parser.add_argument("--source-jpeg-quality", type=int, default=95, help="JPEG quality used only for RGB-derived controlled source JPEG files. Ignored by direct JPEG capture mode.")
    parser.add_argument(
        "--jpeg-capture-mode",
        choices=["direct", "rgb"],
        default="direct",
        help=(
            "For JPEG-only DOE runs, use direct production-like Picamera2.capture_file JPEG capture "
            "or the older RGB-frame-derived JPEG path. Mixed jpeg+png runs always use RGB mode "
            "so both source files come from the same frame."
        ),
    )
    parser.add_argument("--qualities", nargs="+", type=int, default=[10, 25, 40, 50, 65, 75], help="HEIC quality values 0-100.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Output root directory on Pi.")
    parser.add_argument("--tag", default="smoke", help="Short run label included in output folder name.")
    parser.add_argument("--run-id", default=None, help="Optional explicit output folder name.")
    parser.add_argument("--settle-sec", type=float, default=2.0, help="Camera warmup/settle time before capture.")
    parser.add_argument("--link-throughput-kbps", type=float, default=0.361, help="Observed Sofar/API payload throughput in kilobits/sec for link-budget pass/fail.")
    parser.add_argument("--target-transmit-min", type=float, default=16.0, help="Target max transmit time in minutes. At or below this is PASS.")
    parser.add_argument("--hard-transmit-min", type=float, default=18.0, help="Hard max transmit time in minutes. Above this is FAIL; between target and hard is WARN.")
    parser.add_argument("--per-buffer-sec", type=float, default=5.0, help="Approximate production pacing sleep per BM image buffer.")
    parser.add_argument("--start-message-sec", type=float, default=5.0, help="Approximate production pacing sleep after START IMG message.")
    parser.add_argument(
        "--fallback-array-color-order",
        choices=["rgb", "bgr"],
        default="bgr",
        help=(
            "Only used if Picamera2 request.make_image fails and the script falls back to make_array. "
            "Use bgr to swap red/blue channels before saving; this fixes the observed bmcam001 color issue."
        ),
    )
    parser.add_argument("--list-resolutions", action="store_true", help="Print supported resolution keys and exit.")
    args = parser.parse_args()

    if args.list_resolutions:
        for key in sorted(RESOLUTIONS):
            w, h = RESOLUTIONS[key]
            print(f"{key}: {w}x{h}")
        raise SystemExit(0)

    # Validate now so mistakes fail before any camera captures.
    for key in args.resolutions:
        validate_resolution_key(key)
    args.source_modes = [validate_source_mode(m) for m in args.source_modes]
    args.source_jpeg_quality = validate_quality(args.source_jpeg_quality)
    args.qualities = [validate_quality(q) for q in args.qualities]
    return args


if __name__ == "__main__":
    run_doe(parse_args())
