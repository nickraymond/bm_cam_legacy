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

    mem_before = _meminfo_kb()
    t0 = time.monotonic()

    with Image.open(input_path) as img:
        native_w, native_h = img.size

        if crop_x + crop_w > native_w or crop_y + crop_h > native_h:
            raise ValueError(
                "crop exceeds native image bounds: "
                f"crop=({crop_x},{crop_y},{crop_w},{crop_h}) native={native_w}x{native_h}"
            )

        # Crop before RGB conversion to reduce peak memory versus converting the
        # full 4608x2592 native frame in the parent process.
        cropped = img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h)).convert("RGB")

        if (out_w, out_h) != (crop_w, crop_h):
            cropped = cropped.resize((out_w, out_h), Image.Resampling.LANCZOS)

        cropped.save(tmp_path, format="JPEG", quality=jpeg_quality, subsampling=0)

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
    }, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
