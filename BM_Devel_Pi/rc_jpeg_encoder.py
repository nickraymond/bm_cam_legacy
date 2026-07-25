#!/usr/bin/env python3
# filename: rc_jpeg_encoder.py
# description: Sprint08 M2 — progressive-JPEG encoder + message estimate for the RC.
"""
Sprint08 M2 — progressive-JPEG encoder (sprint spec section 2).

Byte-for-byte replica of the Sprint07-validated encode path
(tools/bm_pi_jpeg_encode.py, run p1_grid_20260724T165653Z: 108/108 cells
sha256-identical Pi vs Mac DOE). Two responsibilities, both pure:

  prepare_source()     native full JPEG -> RGB -> fixed crop (native coords)
                       -> lanczos downsample. Runs ONCE per cycle (~2.4 s on
                       Pi); the returned image is reused by every ladder
                       encode attempt.
  encode_progressive() the exact validated Pillow call
                       (quality=q, progressive=True, optimize=True) into
                       memory, plus the transmit estimate:
                       message_count = ceil(base64_len / chunk_b64_chars).

No hardware, no serial, no file writes — the caller persists accepted bytes.
A unit test pins that the in-memory encode is byte-identical to a file save.

Coordinate systems: crop x/y/w/h are NATIVE sensor-equivalent coords
(4608x2592). Output size is (output_width, round(output_width * h / w)) —
same rounding as the sweep/reference tools. RC default geometry is the S07
frozen scene crop 1504,846,1600,900 -> 1000x562 (the exact center crop; no
on-device tag detection — the crop is fixed config constants).

Known limitations: sources must be exactly native-sized (the RC capture
always is); quality range 1..95 matches the validated encoder tools.
"""

import base64
import hashlib
import io
import math

from PIL import Image

# Native IMX708 sensor-equivalent size; the RC capture path always produces this.
NATIVE_SIZE = (4608, 2592)

JPEG_QUALITY_MIN = 1
JPEG_QUALITY_MAX = 95


def output_size_for_crop(crop_w, crop_h, output_width):
    """Output (w, h) for a crop at output_width — same rounding as the S07 tools."""
    crop_w, crop_h, output_width = int(crop_w), int(crop_h), int(output_width)
    if not 1 <= output_width <= crop_w:
        raise ValueError(
            f"output_width must be 1..crop_w ({crop_w}), got {output_width} (no upsampling)"
        )
    return (output_width, round(output_width * crop_h / crop_w))


def prepare_source(native_path, crop_xywh, output_width, native_size=NATIVE_SIZE):
    """Load native JPEG, apply the fixed crop, lanczos-downsample. Pure; one call per cycle.

    Returns the RGB PIL Image every ladder attempt re-encodes from.
    Raises ValueError on wrong native size or out-of-bounds crop (fail loud
    before any encode attempt is charged against the budget).
    """
    x, y, w, h = [int(v) for v in crop_xywh]
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise ValueError(f"crop x/y must be >= 0 and w/h > 0, got {(x, y, w, h)}")
    if x + w > native_size[0] or y + h > native_size[1]:
        raise ValueError(
            f"crop {(x, y, w, h)} exceeds native {native_size[0]}x{native_size[1]}"
        )
    out_size = output_size_for_crop(w, h, output_width)

    with Image.open(native_path) as im:
        im = im.convert("RGB")
        if im.size != tuple(native_size):
            raise ValueError(
                f"expected native {native_size[0]}x{native_size[1]}, "
                f"got {im.size[0]}x{im.size[1]}: {native_path}"
            )
        cropped = im.crop((x, y, x + w, y + h))
        source = cropped.resize(out_size, Image.Resampling.LANCZOS)
    return source


def encode_progressive(source_img, quality, chunk_b64_chars):
    """Encode the prepared source at `quality` and return bytes + transmit estimate.

    The Pillow call is byte-identical to the validated reference
    (tools/bm_pi_jpeg_encode.py): progressive=True, optimize=True, default
    4:2:0 subsampling. ~0.03-0.07 s per call on the Pi — cheap ladder retries.
    """
    quality = int(quality)
    if not JPEG_QUALITY_MIN <= quality <= JPEG_QUALITY_MAX:
        raise ValueError(
            f"quality must be {JPEG_QUALITY_MIN}..{JPEG_QUALITY_MAX}, got {quality}"
        )
    chunk_b64_chars = int(chunk_b64_chars)
    if chunk_b64_chars < 1:
        raise ValueError(f"chunk_b64_chars must be >= 1, got {chunk_b64_chars}")

    buf = io.BytesIO()
    source_img.save(
        buf,
        format="JPEG",
        quality=quality,
        progressive=True,
        optimize=True,
    )
    raw = buf.getvalue()
    base64_len = len(base64.b64encode(raw))

    return {
        "quality": quality,
        "jpeg_data": raw,
        "jpeg_bytes": len(raw),
        "base64_len": base64_len,
        "message_count": math.ceil(base64_len / chunk_b64_chars),
        "jpeg_sha256": hashlib.sha256(raw).hexdigest(),
    }
