#!/usr/bin/env python3
# filename: rc_capture.py
# description: Sprint26 S1 — native camera capture (watchdog + retries + controls) and capture metadata sidecars.
"""
Native full-frame capture for the RC runtime, moved verbatim out of
process_image_v2.py in Sprint26 S1 (tests/golden pins the camera command line
and every capture side effect).

  _select_camera_command(backend)   rpicam-still / libcamera-still on PATH
  _run_native_full_capture(...)     hard timeout, cleanup, cooldown, retries with
                                    progressively simpler command lines, parseable
                                    <WS> error telemetry before giving up
  _camera_controls_from_settings()  camera_controls YAML island -> CLI flags (the
                                    video recorder reuses it)
  generate_filename()               "<UTC>_image.jpg"
  save/load/update_capture_metadata sidecar JSON next to an image
  _load_libcamera_metadata_json()   the --metadata file libcamera wrote

Hardware notes: native capture is 4608x2592 via libcamera-still/rpicam-still
(CMA limits, see CLAUDE.md); bmcam000 showed occasional stalls under repeated
full cycles, hence the watchdog.
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

from rc_telemetry import (
    _num,
    compact_kv_message,
    debug_print,
    get_cpu_temperature,
    get_hostname,
    get_software_sha,
    send_compact_text_message,
)


CAPTURE_METADATA_SUFFIX = ".capture_metadata.json"


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
    "--ev",
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

        # EV compensation in stops (Sprint10 exp command; auto exposure
        # stays engaged — --ev biases it, unlike shutter/gain overrides).
        ev = exposure.get("ev")
        if ev not in (None, ""):
            try:
                ev_f = float(ev)
                requested["requested_ev"] = round(ev_f, 2)
                args.extend(["--ev", _num(ev_f, digits=2)])
            except Exception as exc:
                requested["requested_ev_error"] = str(exc)
                debug_print(f"Invalid exposure.ev={ev!r}; ignoring: {exc}")

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
