#!/usr/bin/env python3
"""
BM camera image quality DOE capture/compression benchmark.

Runs on the Raspberry Pi camera. For each requested resolution, captures one
camera-processed RGB frame, saves source references from that exact same frame,
then compresses each source reference to HEIC at each requested quality value.

Default source modes:
  jpeg  = controlled JPEG source created from the captured RGB frame
  png   = lossless PNG source created from the same captured RGB frame

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

    rows: list[dict[str, Any]] = []
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
        "fallback_array_color_order": args.fallback_array_color_order,
        "transmit": False,
        "note": (
            "For each resolution, captured one RGB frame, saved source JPEG/PNG references "
            "from that same frame, then compressed each source across all HEIC quality levels. "
            "No BM transmission performed."
        ),
    }

    print(f"[DOE] Output: {run_dir}")
    print(f"[DOE] Source modes: {', '.join(args.source_modes)}")
    print(f"[DOE] HEIC qualities: {', '.join(str(q) for q in args.qualities)}")

    for res_key in args.resolutions:
        requested_w, requested_h = validate_resolution_key(res_key)
        capture_ts = utc_stamp()
        frame, metadata, capture_path = capture_rgb_frame(
            res_key,
            args.settle_sec,
            fallback_array_color_order=args.fallback_array_color_order,
        )
        width, height = frame.size
        metadata["DOECapturePath"] = capture_path
        print(f"[DOE] Captured {res_key}: requested={requested_w}x{requested_h}, actual={width}x{height}, path={capture_path}")

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

        for source_mode in args.source_modes:
            source_path = source_paths[source_mode]
            actual_w, actual_h = read_image_size(source_path)
            width = actual_w or width
            height = actual_h or height
            source_size_kb = source_path.stat().st_size / 1024
            print(f"[DOE] Source {source_mode}: {source_path.name}: {width}x{height}, {source_size_kb:.1f} KB")

            for quality in args.qualities:
                q = validate_quality(quality)
                heic_name = (
                    f"{safe_token(hostname)}_{utc_stamp()}_{safe_token(res_key)}_{width}x{height}"
                    f"_src-{source_mode}_q{q:03d}_pending.heic"
                )
                temp_path = image_dir / heic_name
                heic_bytes, b64_chars, buffers, duration_sec = compress_source_to_heic(source_path, temp_path, q)
                final_name = (
                    f"{safe_token(hostname)}_{utc_stamp()}_{safe_token(res_key)}_{width}x{height}"
                    f"_src-{source_mode}_q{q:03d}_{heic_bytes / 1024:.1f}KB_{buffers:03d}buf.heic"
                )
                final_path = image_dir / final_name
                temp_path.rename(final_path)

                row = build_row(
                    hostname=hostname,
                    run_id=run_id,
                    resolution_key=res_key,
                    width=width,
                    height=height,
                    source_mode=source_mode,
                    source_jpeg_quality=args.source_jpeg_quality,
                    quality=q,
                    source_path=source_path,
                    heic_path=final_path,
                    metadata=metadata,
                    compressed_size_bytes=heic_bytes,
                    base64_chars=b64_chars,
                    estimated_buffers=buffers,
                    compression_duration_sec=duration_sec,
                )
                rows.append(row)
                print(
                    f"[DOE] {res_key} src-{source_mode} q{q:03d}: {heic_bytes / 1024:.1f} KB, "
                    f"base64={b64_chars}, est_buffers={buffers}, compress={duration_sec:.2f}s"
                )

    csv_path = run_dir / "results.csv"
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    manifest["finished_at_utc"] = iso_utc()
    manifest["row_count"] = len(rows)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(f"[DOE] Wrote {csv_path}")
    print(f"DOE_OUTPUT_DIR={run_dir}")
    return run_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture BM camera resolution/source/HEIC-quality DOE images without transmission.")
    parser.add_argument("--resolutions", nargs="+", default=["480p"], help="Resolution keys to capture.")
    parser.add_argument("--source-modes", nargs="+", default=["jpeg", "png"], help="Source modes: jpeg png")
    parser.add_argument("--source-jpeg-quality", type=int, default=95, help="JPEG quality used for controlled source JPEG files.")
    parser.add_argument("--qualities", nargs="+", type=int, default=[10, 25, 40, 50, 65, 75], help="HEIC quality values 0-100.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Output root directory on Pi.")
    parser.add_argument("--tag", default="smoke", help="Short run label included in output folder name.")
    parser.add_argument("--run-id", default=None, help="Optional explicit output folder name.")
    parser.add_argument("--settle-sec", type=float, default=2.0, help="Camera warmup/settle time before capture.")
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
