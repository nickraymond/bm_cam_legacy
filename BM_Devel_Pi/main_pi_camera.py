# filename: main_pi_camera.py
# description: take a picture, split it up and send

# Copyright 2025 Nick Raymond
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
#
# http://www.apache.org/licenses/LICENSE-2.0

# Update: added flag to control when to transmit image over UART

import argparse
import subprocess
import time
from datetime import datetime, timezone

from process_image_v2 import (
    BUFFER_SIZE,
    DEBUG,
    IMAGE_DIRECTORY,
    IMAGE_QUALITY,
    RESOLUTION_KEY,
    RESOLUTIONS,
    capture_image,
    close_bm_serial,
    compress_and_send_image,
    debug_print,
    get_cpu_temperature,
    get_file_size,
    log_message,
    send_wake_status,
)
from spotter_time_sync import (
    load_camera_schedule,
    resolve_timezone,
    should_transmit_now_from_schedule,
)


# ==== CONFIGURATION ====
USE_RTC = False  # Set to True if using a hardware RTC; False will use the Pi's system clock.

# Reef shipment safety gate:
# Read Spotter UTC from the BM bus, convert it using camera_schedule.yaml,
# and only continue capture/transmit inside the configured local window.
USE_SPOTTER_TIME_WINDOW = True
SCHEDULE_CONFIG_PATH = "/home/pi/BM_Devel_Pi/camera_schedule.yaml"

# Legacy local time window in military format (e.g., 00:00 to 23:59 means "always run").
# Keep this permissive. The Spotter UTC schedule is the real deployment gate.
time_start = (0, 0)
time_end = (23, 59)


def get_rtc_time():
    """Retrieve the current time from the RTC."""
    try:
        result = subprocess.run(["sudo", "hwclock", "-r"], capture_output=True, text=True)
        rtc_time_str = result.stdout.strip()
        rtc_time = datetime.strptime(
            rtc_time_str.split('.')[0],
            '%Y-%m-%d %H:%M:%S',
        ).replace(tzinfo=timezone.utc)
        debug_print(f"RTC Time: {rtc_time}")
        return rtc_time
    except Exception as e:
        debug_print(f"Error reading RTC time: {e}")
        return None


def is_within_time_window(current_time, time_start, time_end):
    """Check if the current time is within the legacy local time window."""
    start_time = datetime(
        current_time.year,
        current_time.month,
        current_time.day,
        time_start[0],
        time_start[1],
    ).time()
    end_time = datetime(
        current_time.year,
        current_time.month,
        current_time.day,
        time_end[0],
        time_end[1],
    ).time()

    is_within = start_time <= current_time.time() < end_time
    debug_print(f"Time is within window: {is_within}")
    return is_within



def _parse_crop_arg(value):
    """Parse x,y,w,h CLI crop override."""
    if value is None:
        return None
    parts = [p.strip() for p in str(value).split(',')]
    if len(parts) != 4:
        raise ValueError("--crop must be x,y,w,h, for example 768,432,3072,1728")
    x, y, w, h = [int(p) for p in parts]
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise ValueError("--crop requires x/y >= 0 and w/h > 0")
    return x, y, w, h


def _parse_output_size_arg(value):
    """Parse WIDTHxHEIGHT CLI output-size override."""
    if value is None:
        return None
    text = str(value).lower().replace('×', 'x')
    if 'x' not in text:
        raise ValueError("--output-size must be WIDTHxHEIGHT, for example 3072x1728")
    w, h = text.split('x', 1)
    w, h = int(w), int(h)
    if w <= 0 or h <= 0:
        raise ValueError("--output-size width/height must be > 0")
    return w, h


def _build_image_pipeline_settings(
    cfg,
    *,
    capture_backend_override=None,
    crop_override=None,
    output_size_override=None,
    source_jpeg_quality_override=None,
    heic_quality_override=None,
):
    """Return explicit image_pipeline settings resolved from YAML + CLI.

    DEV note: these settings are parsed by spotter_time_sync.py for this branch
    only because that is the current legacy config entry point. Future cleanup
    should move this into a dedicated config loader.
    """
    if not getattr(cfg, 'image_pipeline_enabled', False):
        return {"enabled": False}

    crop = crop_override or (
        cfg.image_pipeline_crop_x,
        cfg.image_pipeline_crop_y,
        cfg.image_pipeline_crop_w,
        cfg.image_pipeline_crop_h,
    )
    output_size = output_size_override or (
        cfg.image_pipeline_spatial_output_width,
        cfg.image_pipeline_spatial_output_height,
    )
    source_jpeg_quality = (
        source_jpeg_quality_override
        if source_jpeg_quality_override is not None
        else cfg.image_pipeline_source_jpeg_quality
    )
    heic_quality = (
        heic_quality_override
        if heic_quality_override is not None
        else cfg.image_pipeline_heic_quality
    )

    settings = {
        "enabled": True,
        "capture_backend": capture_backend_override or cfg.image_pipeline_capture_backend,
        "source_width": int(cfg.image_pipeline_source_width),
        "source_height": int(cfg.image_pipeline_source_height),
        "source_jpeg_quality": int(source_jpeg_quality),
        "crop_mode": cfg.image_pipeline_crop_mode,
        "crop_x": int(crop[0]),
        "crop_y": int(crop[1]),
        "crop_w": int(crop[2]),
        "crop_h": int(crop[3]),
        "output_width": int(output_size[0]),
        "output_height": int(output_size[1]),
        "resample": cfg.image_pipeline_spatial_resample,
        "heic_quality": int(heic_quality),
    }

    if not (0 <= settings["heic_quality"] <= 100):
        raise ValueError("HEIC/image quality must be between 0 and 100")
    if not (1 <= settings["source_jpeg_quality"] <= 100):
        raise ValueError("source JPEG quality must be between 1 and 100")
    if settings["crop_x"] + settings["crop_w"] > settings["source_width"]:
        raise ValueError("image_pipeline crop exceeds source width")
    if settings["crop_y"] + settings["crop_h"] > settings["source_height"]:
        raise ValueError("image_pipeline crop exceeds source height")

    return settings


def get_runtime_image_settings(
    config_path,
    resolution_key_override=None,
    image_quality_override=None,
    capture_backend_override=None,
    crop_override=None,
    output_size_override=None,
    source_jpeg_quality_override=None,
    heic_quality_override=None,
):
    """Return runtime image settings from YAML defaults with optional CLI overrides."""
    cfg = load_camera_schedule(config_path)

    # New libcamera/rpicam path: explicit native crop + spatial output + HEIC.
    if getattr(cfg, 'image_pipeline_enabled', False):
        if resolution_key_override:
            debug_print(
                "Ignoring --resolution-key because image_pipeline.enabled=true; "
                "use --output-size for spatial density override."
            )

        # Preserve --image-quality as a convenient legacy alias for HEIC quality,
        # but let --heic-quality win if both are provided.
        resolved_heic_quality_override = (
            heic_quality_override
            if heic_quality_override is not None
            else image_quality_override
        )

        image_pipeline = _build_image_pipeline_settings(
            cfg,
            capture_backend_override=capture_backend_override,
            crop_override=crop_override,
            output_size_override=output_size_override,
            source_jpeg_quality_override=source_jpeg_quality_override,
            heic_quality_override=resolved_heic_quality_override,
        )

        output_w = image_pipeline["output_width"]
        output_h = image_pipeline["output_height"]
        runtime_resolution_key = f"{output_w}x{output_h}"
        runtime_image_quality = image_pipeline["heic_quality"]
        return runtime_resolution_key, int(runtime_image_quality), cfg, image_pipeline

    # Legacy Picamera2 path: keep historical resolution_key/image_quality behavior.
    resolution_key = resolution_key_override or cfg.resolution_key or RESOLUTION_KEY
    image_quality = image_quality_override if image_quality_override is not None else cfg.image_quality

    if image_quality is None:
        image_quality = IMAGE_QUALITY

    if resolution_key not in RESOLUTIONS:
        raise ValueError(
            f"Invalid resolution key '{resolution_key}'. "
            f"Choose from: {', '.join(RESOLUTIONS.keys())}"
        )

    if not (0 <= int(image_quality) <= 100):
        raise ValueError(
            "image_quality must be between 0 and 100. "
            "Lower = smaller/more compressed; higher = better/larger."
        )

    return resolution_key, int(image_quality), cfg, {"enabled": False}

def _compact_reason(reason):
    """Map verbose schedule reasons to short heartbeat reason codes."""
    reason = str(reason or "").lower()
    if "outside" in reason:
        return "window"
    if "spotter time unavailable" in reason:
        return "time"
    if "disabled" in reason:
        return "disabled"
    if "failed" in reason:
        return "error"
    return None


def _schedule_metadata(schedule_cfg, schedule_info=None):
    """Return common schedule metadata for wake and image messages."""
    schedule_info = schedule_info or {}

    try:
        timezone_name = schedule_info.get("timezone") or resolve_timezone(schedule_cfg)
    except Exception:
        timezone_name = schedule_cfg.timezone

    return {
        "timezone": timezone_name,
        "local_time": schedule_info.get("local_time"),
        "window_start": schedule_cfg.transmit_start,
        "window_end": schedule_cfg.transmit_end,
    }


def _send_wake_status_safe(
    *,
    action,
    schedule_cfg,
    schedule_info=None,
    runtime_resolution_key=None,
    runtime_image_quality=None,
    reason=None,
):
    """Send compact wake status and never let telemetry break the capture path."""
    try:
        meta = _schedule_metadata(schedule_cfg, schedule_info=schedule_info)
        send_wake_status(
            action=action,
            timezone_name=meta.get("timezone"),
            local_time=meta.get("local_time"),
            window_start=meta.get("window_start"),
            window_end=meta.get("window_end"),
            image_res_key=runtime_resolution_key,
            image_quality=runtime_image_quality,
            reason=reason,
        )
    except Exception as exc:
        debug_print(f"Wake status send failed, continuing safely: {exc}")


def main(
    transmit_image=False,
    resolution_key=None,
    image_quality=None,
    skip_time_window=False,
    config_path=SCHEDULE_CONFIG_PATH,
    capture_backend=None,
    crop=None,
    output_size=None,
    source_jpeg_quality=None,
    heic_quality=None,
):
    """Main function to orchestrate the camera workflow."""
    start_time = time.time()

    try:
        runtime_resolution_key, runtime_image_quality, schedule_cfg, image_pipeline = get_runtime_image_settings(
            config_path,
            resolution_key_override=resolution_key,
            image_quality_override=image_quality,
            capture_backend_override=capture_backend,
            crop_override=_parse_crop_arg(crop),
            output_size_override=_parse_output_size_arg(output_size),
            source_jpeg_quality_override=source_jpeg_quality,
            heic_quality_override=heic_quality,
        )
    except Exception as e:
        debug_print(f"Image/config setup failed: {e}")
        close_bm_serial()
        return

    debug_print(f"Runtime resolution/output key: {runtime_resolution_key}")
    debug_print(f"Runtime HEIC/image quality: {runtime_image_quality}")
    if image_pipeline.get("enabled"):
        debug_print(
            "Runtime image_pipeline: "
            f"backend={image_pipeline.get('capture_backend')} "
            f"source={image_pipeline.get('source_width')}x{image_pipeline.get('source_height')} "
            f"crop=({image_pipeline.get('crop_x')},{image_pipeline.get('crop_y')},"
            f"{image_pipeline.get('crop_w')},{image_pipeline.get('crop_h')}) "
            f"output={image_pipeline.get('output_width')}x{image_pipeline.get('output_height')} "
            f"heic_q={image_pipeline.get('heic_quality')}"
        )

    schedule_info = {}
    schedule_allowed = True
    action = "cap"

    if skip_time_window:
        debug_print("Skipping Spotter UTC transmit-window check due to CLI flag --skip-time-window.")
        schedule_info = {
            "reason": "Spotter UTC transmit-window check skipped by CLI",
            "timezone": _schedule_metadata(schedule_cfg).get("timezone"),
        }
        schedule_allowed = True
        action = "cap"

    elif USE_SPOTTER_TIME_WINDOW and schedule_cfg.enforce_spotter_time_window:
        try:
            schedule_allowed, schedule_info = should_transmit_now_from_schedule(config_path)

            debug_print(f"Schedule check: {schedule_info.get('reason')}")
            debug_print(f"Schedule source_time: {schedule_info.get('source_time')}")
            debug_print(f"Schedule UTC: {schedule_info.get('utc_time')}")
            debug_print(f"Schedule local: {schedule_info.get('local_time')}")
            debug_print(f"Schedule set_system_clock: {schedule_info.get('set_system_clock')}")

            if schedule_allowed:
                action = "cap"
            else:
                action = "skip_win"

        except Exception as e:
            debug_print(f"Spotter-time schedule check failed closed: {e}")
            schedule_info = {
                "reason": f"schedule exception: {e}",
                "timezone": _schedule_metadata(schedule_cfg).get("timezone"),
            }
            if transmit_image:
                _send_wake_status_safe(
                    action="skip_err",
                    schedule_cfg=schedule_cfg,
                    schedule_info=schedule_info,
                    runtime_resolution_key=runtime_resolution_key,
                    runtime_image_quality=runtime_image_quality,
                    reason="schedule",
                )
            close_bm_serial()
            return

    else:
        debug_print("Spotter UTC transmit-window check disabled by config/code. Continuing.")
        schedule_info = {
            "reason": "Spotter UTC transmit-window check disabled",
            "timezone": _schedule_metadata(schedule_cfg).get("timezone"),
        }
        schedule_allowed = True
        action = "cap"

    # Emit compact wake heartbeat only for transmit/scheduled runs.
    # Manual capture-only and compression-only development runs must not touch
    # the BM bus unless explicitly transmitting.
    if transmit_image:
        _send_wake_status_safe(
            action=action,
            schedule_cfg=schedule_cfg,
            schedule_info=schedule_info,
            runtime_resolution_key=runtime_resolution_key,
            runtime_image_quality=runtime_image_quality,
            reason=_compact_reason(schedule_info.get("reason")),
        )
    else:
        debug_print("Transmit disabled; skipping compact wake telemetry send.")

    if not schedule_allowed:
        debug_print("Outside configured Spotter-time transmit window. Skipping capture/transmit.")
        close_bm_serial()
        return

    schedule_meta = _schedule_metadata(schedule_cfg, schedule_info=schedule_info)

    # Choose the source for current time based on the USE_RTC flag.
    current_time = get_rtc_time() if USE_RTC else datetime.now()

    if current_time:
        within_window = is_within_time_window(current_time, time_start, time_end)

        if within_window:
            # Capture the image. In image_pipeline mode this is native full
            # rpicam/libcamera capture -> explicit crop -> spatial output JPEG.
            # In legacy mode this remains the historical Picamera2 capture.
            image_path = capture_image(
                resolution_key=runtime_resolution_key,
                image_pipeline=image_pipeline,
            )
            file_size_raw = get_file_size(image_path)
            cpu_temp = get_cpu_temperature()

            if transmit_image:
                # Compress and transmit image
                compressed_file_name, num_buffers, file_size_compressed = compress_and_send_image(
                    image_path,
                    image_quality=runtime_image_quality,
                    image_res_key=runtime_resolution_key,
                    schedule_metadata=schedule_meta,
                )
            else:
                compressed_file_name = "N/A"
                num_buffers = 0
                file_size_compressed = 0

            # Calculate execution time
            end_time = time.time()
            execution_time = (end_time - start_time) / 60

            # Log the details; using 'within_window' for legacy record-keeping.
            log_message(
                current_time,
                compressed_file_name,
                file_size_raw,
                file_size_compressed,
                runtime_image_quality,
                num_buffers,
                execution_time,
                within_window,
                cpu_temp,
            )

            close_bm_serial()

        else:
            debug_print("Not within the legacy local time window. Skipping capture.")
            if transmit_image:
                _send_wake_status_safe(
                    action="skip_legacy",
                    schedule_cfg=schedule_cfg,
                    schedule_info=schedule_info,
                    runtime_resolution_key=runtime_resolution_key,
                    runtime_image_quality=runtime_image_quality,
                    reason="legacy",
                )
            close_bm_serial()
            return

    else:
        debug_print("Failed to retrieve time.")
        if transmit_image:
            _send_wake_status_safe(
                action="skip_err",
                schedule_cfg=schedule_cfg,
                schedule_info=schedule_info,
                runtime_resolution_key=runtime_resolution_key,
                runtime_image_quality=runtime_image_quality,
                reason="rtc",
            )
        close_bm_serial()
        return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Camera capture script with optional UART transmission.")
    parser.add_argument('--transmit', action='store_true', help='Enable transmission over UART after capture')
    parser.add_argument(
        '--resolution-key',
        choices=sorted(RESOLUTIONS.keys()),
        default=None,
        help='Override image resolution preset for this run',
    )
    parser.add_argument(
        '--image-quality',
        type=int,
        default=None,
        help='Override encoder image quality 0-100. In image_pipeline mode this is a HEIC-quality alias.',
    )
    parser.add_argument(
        '--heic-quality',
        type=int,
        default=None,
        help='Override image_pipeline.heic.quality 0-100 for this run. Wins over --image-quality.',
    )
    parser.add_argument(
        '--capture-backend',
        choices=['auto', 'rpicam', 'libcamera', 'picamera2', 'legacy'],
        default=None,
        help='Override image_pipeline.capture_backend for this run',
    )
    parser.add_argument(
        '--crop',
        default=None,
        help='Override image_pipeline.crop as x,y,w,h in native image coordinates',
    )
    parser.add_argument(
        '--output-size',
        default=None,
        help='Override image_pipeline.spatial output size as WIDTHxHEIGHT, e.g. 3072x1728',
    )
    parser.add_argument(
        '--source-jpeg-quality',
        type=int,
        default=None,
        help='Override native/intermediate JPEG quality 1-100 before HEIC compression',
    )
    parser.add_argument(
        '--skip-time-window',
        action='store_true',
        help='Manual override: skip the Spotter UTC transmit-window check for this run',
    )
    parser.add_argument('--config-path', default=SCHEDULE_CONFIG_PATH, help='Path to camera_schedule.yaml')

    args = parser.parse_args()

    main(
        transmit_image=args.transmit,
        resolution_key=args.resolution_key,
        image_quality=args.image_quality,
        skip_time_window=args.skip_time_window,
        config_path=args.config_path,
        capture_backend=args.capture_backend,
        crop=args.crop,
        output_size=args.output_size,
        source_jpeg_quality=args.source_jpeg_quality,
        heic_quality=args.heic_quality,
    )
