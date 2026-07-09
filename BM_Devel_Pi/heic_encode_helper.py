#!/usr/bin/env python3
"""
heic_encode_helper.py

Lightweight HEIC encoder helper for BM camera Pi Zero 2W tests.

Purpose:
- Keep HEIC encoding out of the heavier production process_image_v2.py module
  context, which imports camera/serial support for the capture/transmit path.
- Match the validated stable manual process:
    JPEG input -> Pillow open -> RGB conversion -> temp HEIC -> nonzero check -> atomic rename.

This script does not transmit, split buffers, open BM serial, import Picamera2,
or import OpenCV. It writes a small JSON summary to stdout for the caller log.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pillow_heif
from PIL import Image

pillow_heif.register_heif_opener()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encode a JPEG/PNG image to HEIC using a safe temp-file flow.")
    parser.add_argument("--input", required=True, help="Input image path, usually final cropped/downsampled JPEG.")
    parser.add_argument("--output", required=True, help="Final HEIC output path.")
    parser.add_argument("--quality", type=int, required=True, help="HEIC quality. Lower means smaller/more compressed.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    quality = int(args.quality)

    if not input_path.exists():
        raise FileNotFoundError(f"Input image not found: {input_path}")
    if not (0 <= quality <= 100):
        raise ValueError(f"Quality must be 0-100, got {quality}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.stem + ".tmp" + output_path.suffix)

    for p in (tmp_path, output_path):
        if p.exists():
            p.unlink()

    t0 = time.monotonic()
    with Image.open(input_path) as img:
        opened_size = img.size
        opened_mode = img.mode
        img = img.convert("RGB")
        converted_size = img.size
        converted_mode = img.mode
        img.save(tmp_path, format="HEIF", quality=quality)

    if not tmp_path.exists():
        raise RuntimeError(f"Temporary HEIC was not created: {tmp_path}")
    tmp_size = tmp_path.stat().st_size
    if tmp_size <= 0:
        try:
            tmp_path.unlink()
        finally:
            raise RuntimeError(f"Temporary HEIC is zero bytes: {tmp_path}")

    os.replace(tmp_path, output_path)
    output_size = output_path.stat().st_size
    if output_size <= 0:
        raise RuntimeError(f"Final HEIC is zero bytes: {output_path}")

    dt = time.monotonic() - t0
    print(json.dumps({
        "ok": True,
        "input": str(input_path),
        "output": str(output_path),
        "quality": quality,
        "opened_size": list(opened_size),
        "opened_mode": opened_mode,
        "converted_size": list(converted_size),
        "converted_mode": converted_mode,
        "output_bytes": output_size,
        "encode_seconds": round(dt, 3),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
