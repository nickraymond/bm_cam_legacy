from pathlib import Path
import re

p = Path("BM_Devel_Pi/process_image_v2.py")
s = p.read_text()

# ---------------------------------------------------------------------
# 1) Add focus-control helpers before _without_metadata_args.
# ---------------------------------------------------------------------
marker = "\ndef _without_metadata_args(cmd):"
helper = r'''
CAMERA_CONTROL_OPTIONS_WITH_VALUES = {
    "--autofocus-mode",
    "--lens-position",
    "--autofocus-range",
    "--autofocus-speed",
}


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

'''
if "def _focus_camera_controls_from_settings(" not in s:
    if marker not in s:
        raise SystemExit("Could not find insertion point before _without_metadata_args")
    s = s.replace(marker, helper + marker, 1)

# ---------------------------------------------------------------------
# 2) Add settings parameter to native capture function.
# ---------------------------------------------------------------------
old_sig = "def _run_native_full_capture(command, native_image_path, source_width, source_height, jpeg_quality, log_prefix):"
new_sig = "def _run_native_full_capture(command, native_image_path, source_width, source_height, jpeg_quality, log_prefix, settings=None):"
if old_sig in s:
    s = s.replace(old_sig, new_sig, 1)

# ---------------------------------------------------------------------
# 3) Insert focus args into native capture command before output path.
# ---------------------------------------------------------------------
old_cmd = '''    base_cmd = [
        command,
        "-n",
        "--timeout", "2000",
        "--width", str(source_width),
        "--height", str(source_height),
        "--quality", str(jpeg_quality),
        "--metadata", metadata_json_path,
        "-o", native_image_path,
    ]
'''
new_cmd = '''    base_cmd = [
        command,
        "-n",
        "--timeout", "2000",
        "--width", str(source_width),
        "--height", str(source_height),
        "--quality", str(jpeg_quality),
        "--metadata", metadata_json_path,
    ]

    camera_control_args, requested_camera_controls = _focus_camera_controls_from_settings(settings)
    if camera_control_args:
        base_cmd.extend(camera_control_args)
        debug_print(
            "Applying requested focus camera controls: "
            f"args={' '.join(camera_control_args)}; "
            f"requested={requested_camera_controls}"
        )

    base_cmd.extend(["-o", native_image_path])
'''
if "requested_camera_controls = _focus_camera_controls_from_settings(settings)" not in s:
    if old_cmd not in s:
        raise SystemExit("Could not find native base_cmd block with --metadata")
    s = s.replace(old_cmd, new_cmd, 1)

# ---------------------------------------------------------------------
# 4) Add retry variants without camera controls.
# ---------------------------------------------------------------------
if "WITHOUT camera controls" not in s:
    seen_marker = "                seen = set()\n"
    insert = '''                if camera_control_args and _command_has_camera_control_args(cmd):
                    retry_variants.append((
                        _without_camera_control_args(cmd),
                        "WITHOUT camera controls",
                    ))

                    if "--metadata" in cmd:
                        retry_variants.append((
                            _without_metadata_args(_without_camera_control_args(cmd)),
                            "WITHOUT camera controls AND --metadata",
                        ))

'''
    # Insert only inside first native retry block, before seen = set().
    idx = s.find(seen_marker)
    if idx < 0:
        raise SystemExit("Could not find retry seen=set insertion point")
    s = s[:idx] + insert + s[idx:]

# ---------------------------------------------------------------------
# 5) Add camera-control metadata to capture_info return dict.
# ---------------------------------------------------------------------
old_return = '''                return {
                    "capture_command": final_cmd,
                    "stdout_log": stdout_log,
                    "stderr_log": stderr_log,
                    "metadata_json": metadata_json_path if os.path.exists(metadata_json_path) else None,
                }
'''
new_return = '''                return {
                    "capture_command": final_cmd,
                    "stdout_log": stdout_log,
                    "stderr_log": stderr_log,
                    "metadata_json": metadata_json_path if os.path.exists(metadata_json_path) else None,
                    "camera_control_args": camera_control_args,
                    "camera_control_args_used": _command_has_camera_control_args(final_cmd),
                    "camera_controls_fallback_used": bool(camera_control_args and not _command_has_camera_control_args(final_cmd)),
                    "requested_camera_controls": requested_camera_controls,
                }
'''
if '"camera_control_args": camera_control_args,' not in s:
    if old_return not in s:
        raise SystemExit("Could not find capture_info return dict")
    s = s.replace(old_return, new_return, 1)

# ---------------------------------------------------------------------
# 6) Pass settings into _run_native_full_capture call using tolerant regex.
# ---------------------------------------------------------------------
if "settings=settings" not in s:
    pattern = re.compile(
        r'(?P<prefix>capture_info\s*=\s*_run_native_full_capture\(\n)'
        r'(?P<body>.*?)'
        r'(?P<close>\n\s*\))',
        re.S,
    )
    m = pattern.search(s)
    if not m:
        raise SystemExit("Could not find _run_native_full_capture call")

    body = m.group("body")
    if "settings=" not in body:
        body = body.rstrip() + "\n        settings=settings,"
        s = s[:m.start("body")] + body + s[m.start("close"):]

# ---------------------------------------------------------------------
# 7) Save requested controls into sidecar metadata.
# ---------------------------------------------------------------------
old_sidecar = '''        "capture_metadata_json": capture_info.get("metadata_json"),
        "native_image_path": native_image_path,
'''
new_sidecar = '''        "capture_metadata_json": capture_info.get("metadata_json"),
        "camera_control_args": capture_info.get("camera_control_args"),
        "camera_control_args_used": capture_info.get("camera_control_args_used"),
        "camera_controls_fallback_used": capture_info.get("camera_controls_fallback_used"),
        "requested_camera_controls": capture_info.get("requested_camera_controls"),
        "native_image_path": native_image_path,
'''
if '"requested_camera_controls": capture_info.get("requested_camera_controls"),' not in s:
    if old_sidecar not in s:
        raise SystemExit("Could not find sidecar metadata insert point")
    s = s.replace(old_sidecar, new_sidecar, 1)

old_load = '''    libcamera_metadata = _load_libcamera_metadata_json(capture_info.get("metadata_json"))
'''
new_load = '''    requested_camera_controls = capture_info.get("requested_camera_controls")
    if isinstance(requested_camera_controls, dict):
        metadata.update(requested_camera_controls)

    libcamera_metadata = _load_libcamera_metadata_json(capture_info.get("metadata_json"))
'''
if 'requested_camera_controls = capture_info.get("requested_camera_controls")' not in s:
    if old_load not in s:
        raise SystemExit("Could not find libcamera metadata load point")
    s = s.replace(old_load, new_load, 1)

p.write_text(s)
print("Patched YAML-driven focus controls.")
