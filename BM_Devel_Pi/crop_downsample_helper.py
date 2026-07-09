#!/usr/bin/env python3
"""
crop_downsample_helper.py

Lightweight crop/downsample helper for BM camera Pi Zero 2W tests.

Purpose:
- Keep native JPEG crop/downsample out of the long-running parent camera process.
- Bound the chunky PIL/libjpeg step with a parent-process timeout/retry.
- Use an atomic temp-file flow so failed attempts do not leave a fake final image.

This script does not capture, transmit, split buffers, open BM serial, import
Picamera2, or import OpenCV. It writes a small JSON summary to stdout.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import time
from pathlib import Path

from PIL import Image


def _meminfo_kb() -> dict:
    wanted = {"MemAvailable", "CmaTotal", "CmaFree", "SwapTotal", "SwapFree"}
    out = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key = line.split(":", 1)[0]
                if key in wanted:
                    parts = line.split()
                    if len(parts) >= 2:
                        out[f"{key}_kB"] = int(parts[1])
    except Exception:
        pass
    return out


def _write_progress(progress_log, stage, **data):
    """Append helper progress to a JSONL file.

    This is intentionally tiny and syncs each line so if the Pi wedges, the last
    completed stage is still useful after reboot.
    """
    if not progress_log:
        return
    try:
        row = {
            "stage": stage,
            "time_monotonic": round(time.monotonic(), 3),
            "mem": _meminfo_kb(),
        }
        row.update(data)
        with open(progress_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def _close_image(img):
    try:
        if img is not None:
            img.close()
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crop/downsample a native JPEG using a safe temp-file flow.")
    parser.add_argument("--input", required=True, help="Input native full JPEG path.")
    parser.add_argument("--output", required=True, help="Final cropped/downsampled JPEG path.")
    parser.add_argument("--crop-x", type=int, required=True)
    parser.add_argument("--crop-y", type=int, required=True)
    parser.add_argument("--crop-w", type=int, required=True)
    parser.add_argument("--crop-h", type=int, required=True)
    parser.add_argument("--output-width", type=int, required=True)
    parser.add_argument("--output-height", type=int, required=True)
    parser.add_argument("--jpeg-quality", type=int, required=True)
    parser.add_argument("--resample", default="lanczos")
    parser.add_argument("--progress-log", default=None, help="Optional JSONL progress log for debugging helper wedges.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input native image not found: {input_path}")

    crop_x = int(args.crop_x)
    crop_y = int(args.crop_y)
    crop_w = int(args.crop_w)
    crop_h = int(args.crop_h)
    out_w = int(args.output_width)
    out_h = int(args.output_height)
    jpeg_quality = int(args.jpeg_quality)

    if crop_x < 0 or crop_y < 0 or crop_w <= 0 or crop_h <= 0:
        raise ValueError(f"Invalid crop: {(crop_x, crop_y, crop_w, crop_h)}")
    if out_w <= 0 or out_h <= 0:
        raise ValueError(f"Invalid output size: {(out_w, out_h)}")
    if not (1 <= jpeg_quality <= 100):
        raise ValueError(f"JPEG quality must be 1-100, got {jpeg_quality}")

    resample_name = str(args.resample or "lanczos").lower()
    if resample_name != "lanczos":
        resample_name = "lanczos"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.stem + ".tmp" + output_path.suffix)

    for p in (tmp_path, output_path):
        if p.exists():
            p.unlink()

    progress_log = args.progress_log
    if progress_log:
        try:
            Path(progress_log).parent.mkdir(parents=True, exist_ok=True)
            if os.path.exists(progress_log):
                os.remove(progress_log)
        except Exception:
            pass

    mem_before = _meminfo_kb()
    t0 = time.monotonic()
    _write_progress(
        progress_log,
        "start",
        input=str(input_path),
        output=str(output_path),
        crop=[crop_x, crop_y, crop_w, crop_h],
        output_size=[out_w, out_h],
        jpeg_quality=jpeg_quality,
    )

    native_w = native_h = None
    cropped = None
    output_image = None

    try:
        # Stage 1: open native image and make a real cropped copy.
        # Important: close the full native image before RGB conversion/resize to
        # reduce peak memory on Pi Zero 2W.
        with Image.open(input_path) as img:
            native_w, native_h = img.size
            opened_mode = img.mode
            _write_progress(
                progress_log,
                "opened",
                native_width=native_w,
                native_height=native_h,
                opened_mode=opened_mode,
            )

            if crop_x + crop_w > native_w or crop_y + crop_h > native_h:
                raise ValueError(
                    "crop exceeds native image bounds: "
                    f"crop=({crop_x},{crop_y},{crop_w},{crop_h}) native={native_w}x{native_h}"
                )

            cropped = img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
            cropped.load()
            _write_progress(
                progress_log,
                "cropped",
                cropped_size=list(cropped.size),
                cropped_mode=cropped.mode,
            )

        _write_progress(progress_log, "source_closed")
        gc.collect()

        # Stage 2: convert only the cropped region to RGB.
        if cropped.mode != "RGB":
            rgb_image = cropped.convert("RGB")
            _close_image(cropped)
            cropped = rgb_image
            gc.collect()
            _write_progress(
                progress_log,
                "converted_rgb",
                converted_size=list(cropped.size),
                converted_mode=cropped.mode,
            )
        else:
            _write_progress(
                progress_log,
                "already_rgb",
                converted_size=list(cropped.size),
                converted_mode=cropped.mode,
            )

        # Stage 3: resize after full native image is closed.
        if (out_w, out_h) != (crop_w, crop_h):
            output_image = cropped.resize((out_w, out_h), Image.Resampling.LANCZOS)
            _close_image(cropped)
            cropped = None
            gc.collect()
            _write_progress(
                progress_log,
                "resized",
                output_size=list(output_image.size),
                output_mode=output_image.mode,
            )
        else:
            output_image = cropped
            cropped = None
            _write_progress(
                progress_log,
                "resize_skipped",
                output_size=list(output_image.size),
                output_mode=output_image.mode,
            )

        # Stage 4: save temp JPEG atomically.
        output_image.save(tmp_path, format="JPEG", quality=jpeg_quality, subsampling=0)
        _write_progress(
            progress_log,
            "saved_tmp",
            tmp_path=str(tmp_path),
            tmp_bytes=tmp_path.stat().st_size if tmp_path.exists() else None,
        )

    finally:
        _close_image(cropped)
        _close_image(output_image)
        gc.collect()

    if not tmp_path.exists():
        raise RuntimeError(f"Temporary output was not created: {tmp_path}")

    tmp_size = tmp_path.stat().st_size
    if tmp_size <= 0:
        try:
            tmp_path.unlink()
        finally:
            raise RuntimeError(f"Temporary output is zero bytes: {tmp_path}")

    os.replace(tmp_path, output_path)

    output_size = output_path.stat().st_size
    if output_size <= 0:
        raise RuntimeError(f"Final output is zero bytes: {output_path}")

    dt = time.monotonic() - t0
    mem_after = _meminfo_kb()

    print(json.dumps({
        "ok": True,
        "input": str(input_path),
        "output": str(output_path),
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
        "output_bytes": output_size,
        "duration_seconds": round(dt, 3),
        "mem_before": mem_before,
        "mem_after": mem_after,
        "progress_log": progress_log,
    }, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
