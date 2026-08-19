#!/usr/bin/env python3
# filename: video_settings.py
# description: Sprint15 settings GUI backend — read/patch camera_schedule.yaml safely.
"""
Sprint15 settings GUI (Nick request 2026-08-17): the /settings page of
videoui_server edits camera_schedule.yaml through THIS module instead of
a customer editing YAML by hand.

Design rules:
  - FIELDS is the single source of truth: only listed keys are editable,
    only listed choice values are writable (a dropdown cannot fat-finger
    a value, same doctrine as the command tables).
  - patch_yaml() is a LINE-based editor: it rewrites only the value part
    of existing `key: value` lines, preserving every comment, blank line
    and the file's structure. Keys that don't exist in the file are
    REFUSED (this GUI edits configs, it does not author them).
  - Every save: timestamped backup first, full validation after
    (load_camera_schedule + validate_schedule, + the video island when
    capture_mode is video). Validation failure restores the backup and
    raises — the file on disk is never left invalid.
  - Changes apply at the camera's NEXT restart (same next-boot doctrine
    as remote commands); the GUI offers a restart button.

Coordinate note: focus lens_position is dioptres (1/metres) — the
choice labels translate to distances so customers pick "1 m", not 1.0.
"""

import os
import shutil
import time

# ---------------------------------------------------------------------------
# Editable fields. key = dot-joined YAML path (indent-nesting defines the
# path). kind: "choice" renders a dropdown; "toggle" renders on/off.
# quote: write the value quoted (matches the file's existing style).
# ---------------------------------------------------------------------------

FIELDS = [
    {
        "key": "capture_mode",
        "label": "Camera mode",
        "kind": "choice",
        "quote": True,
        # Sprint18 (Nick 2026-08-19): HEIC is not a supported customer
        # choice any more. The runtime heic path is untouched — a config
        # already set to heic still runs, and shows honestly on the page.
        "choices": [("video", "Video"),
                    ("progressive_jpeg", "Image")],
        "help": "What the camera does every time it wakes.",
    },
    {
        "key": "video.clip_minutes",
        "label": "Clip length",
        "kind": "choice",
        "choices": [("0.25", "15 seconds (bench test)"),
                    ("1", "1 minute"),
                    ("5", "5 minutes (standard)"),
                    ("10", "10 minutes"),
                    ("15", "15 minutes")],
        "help": "Each video file covers this much time.",
    },
    {
        "key": "video.fps",
        "label": "Frame rate",
        "kind": "choice",
        "choices": [("5", "5 fps (timelapse-like)"),
                    ("10", "10 fps"),
                    ("15", "15 fps (standard)"),
                    ("24", "24 fps"),
                    ("30", "30 fps (smoothest)")],
        "help": "Frames per second. Higher = smoother motion, bigger files.",
    },
    {
        "key": "video.bitrate_mbps",
        "label": "Video quality (bitrate)",
        "kind": "choice",
        "choices": [("1", "1 Mbps (small files)"),
                    ("2", "2 Mbps (standard)"),
                    ("2.5", "2.5 Mbps"),
                    ("4", "4 Mbps (high)"),
                    ("6", "6 Mbps (very high)"),
                    ("6.5", "6.5 Mbps"),
                    ("8", "8 Mbps"),
                    ("9.3", "9.3 Mbps (1080p quality class)"),
                    ("12", "12 Mbps (maximum)")],
        "help": "How many bits per second of video. Higher = crisper "
                "motion detail, bigger files (2 Mbps ≈ 75 MB per 5 min).",
    },
    {
        "key": "progressive_jpeg.output_width",
        "label": "Photo resolution (stills only)",
        "kind": "choice",
        "choices": [("800", "800 px wide"),
                    ("1000", "1000 px wide (standard)"),
                    ("1280", "1280 px wide"),
                    ("1600", "1600 px wide (sharpest, 1:1 with crop)")],
        "help": "Width of the transmitted PHOTO; height follows the crop "
                "shape. Cannot exceed the crop width (no upscaling). Since "
                "Sprint17 this no longer affects video — video has its own "
                "quality preset below.",
    },
    {
        "key": "image_pipeline.camera_controls.focus.mode",
        "label": "Focus mode",
        "kind": "choice",
        "quote": True,
        "choices": [("manual", "Manual (fixed distance below)"),
                    ("auto", "Autofocus")],
        "help": "Underwater rigs usually use manual focus at a known "
                "subject distance.",
    },
    {
        "key": "image_pipeline.camera_controls.focus.lens_position",
        "label": "Focus distance (manual mode)",
        "kind": "choice",
        "choices": [("0.0", "Infinity / far"),
                    ("0.5", "2 m"),
                    ("1.0", "1 m"),
                    ("1.82", "55 cm"),
                    ("3.33", "30 cm"),
                    ("5.0", "20 cm")],
        "help": "Only used when focus mode is Manual. (Stored as "
                "dioptres = 1/distance.)",
    },
    {
        "key": "video.session_minutes",
        "label": "Recording session",
        "kind": "choice",
        "choices": [("0", "Continuous (until power off)"),
                    ("30", "30 min, then sleep"),
                    ("60", "1 hour, then sleep"),
                    ("120", "2 hours, then sleep"),
                    ("360", "6 hours, then sleep")],
        "help": "Continuous is the standard deployment mode; timed "
                "sessions use the normal power-halt path.",
    },
    {
        "key": "video.storage.max_used_pct",
        "label": "Storage cap (ring buffer)",
        "kind": "choice",
        "choices": [("50", "50 % of the card"),
                    ("60", "60 %"),
                    ("75", "75 % (standard)"),
                    ("85", "85 %"),
                    ("90", "90 %")],
        "help": "When the card passes this, the oldest clips are deleted "
                "to make room. Newest footage always wins.",
    },
    {
        "key": "video.storage.min_free_gb",
        "label": "Minimum free space",
        "kind": "choice",
        "choices": [("5", "5 GB"), ("10", "10 GB (standard)"),
                    ("20", "20 GB")],
        "help": "Hard floor — recording pauses rather than fill the card "
                "past this.",
    },
    {
        "key": "video.storage.ring_dry_run",
        "label": "Ring buffer dry-run",
        "kind": "toggle",
        "choices": [("false", "Off — really delete old clips (standard)"),
                    ("true", "On — only report what WOULD be deleted")],
        "help": "Diagnostic mode. Leave Off for deployments.",
    },
    {
        # Sprint17 D-S17-1: video geometry is now independent of the stills
        # keys. Each preset carries its own crop, sensor mode and output size;
        # video_geometry refuses any combination that would upscale.
        "key": "video.preset",
        "label": "Video quality preset",
        "kind": "choice",
        "quote": True,
        "choices": [
            # Sprint18: no day estimates in these labels. They were static
            # guesses; the settings page now MEASURES the retention window
            # from the unit's actual burn rate, and two different numbers
            # for the same thing on one screen destroys trust.
            ("wide_1080p", "1080p wide — full view, best quality"),
            ("wide_1080p_lean", "1080p wide, lean — full view"),
            ("wide_720p", "720p wide — full view"),
            ("wide_720p_lean", "720p wide, lean — full view"),
            ("stills_roi_1000p", "1000 px, photo framing — matches the photo crop"),
            ("stills_roi_1600p", "1600 px, photo framing — sharpest at that framing"),
        ],
        "help": "Sets the video field of view, sensor readout and recorded "
                "size together. Wider presets see the whole camera view; the "
                "photo-framing presets match the still-image crop. Higher "
                "quality fills the card faster — the Storage panel shows how "
                "much recent footage this camera is actually keeping.",
    },
    {
        "key": "video.encoder.denoise",
        "label": "Video noise reduction (engineering)",
        "kind": "choice",
        "quote": True,
        "choices": [("", "Camera default"),
                    ("cdn_off", "Off — keep all detail and all noise"),
                    ("cdn_fast", "Fast"),
                    ("cdn_hq", "High quality — slowest, cleanest")],
        "help": "Colour denoise mode. Likely the biggest lever for murky or "
                "particulate-heavy water; untested underwater as of Sprint17.",
    },
    {
        "key": "video.encoder.sharpness",
        "label": "Video sharpness (engineering)",
        "kind": "choice",
        "choices": [("1.0", "1.0 — normal (camera default)"),
                    ("0.0", "0.0 — none"),
                    ("1.5", "1.5 — moderate"),
                    ("2.0", "2.0 — strong")],
        "help": "ISP sharpening applied before encoding. Over-sharpening "
                "makes compression work harder for no real detail.",
    },
    {
        "key": "video.encoder.profile",
        "label": "H.264 profile (engineering)",
        "kind": "choice",
        "quote": True,
        "choices": [("", "Camera default"),
                    ("baseline", "baseline"),
                    ("main", "main"),
                    ("high", "high — best compression efficiency")],
        "help": "Higher profiles pack more quality into the same bitrate. "
                "Every modern player handles high.",
    },
    {
        "key": "video.encoder.level",
        "label": "H.264 level (engineering)",
        "kind": "choice",
        "quote": True,
        "choices": [("", "Camera default"), ("4", "4"), ("4.1", "4.1"),
                    ("4.2", "4.2")],
        "help": "Decoder capability ceiling. Leave at default unless a "
                "player refuses the file.",
    },
    {
        "key": "video.encoder.intra",
        "label": "Keyframe interval (engineering)",
        "kind": "choice",
        "choices": [("0", "Camera default"),
                    ("15", "15 frames (~1 s) — fastest seeking"),
                    ("30", "30 frames"),
                    ("60", "60 frames"),
                    ("150", "150 frames — smallest files")],
        "help": "How often a full frame is written. Frequent keyframes make "
                "scrubbing snappier and cost bitrate.",
    },
    {
    # Sprint16 D-S16-4: the ship switch. Session-only wap/join flips
        # never touch this; every power cycle returns here.
        "key": "network.default",
        "label": "WiFi at power-on (boot default)",
        "kind": "choice",
        "choices": [("ap", "Hotspot (open, named after the camera) — "
                           "ship setting"),
                    ("nereus_hq", "Nereus HQ office WiFi (dev/bench)")],
        "help": "What network the camera provides or joins every time it "
                "powers on. Temporary switches (commands, the join form "
                "below) always fall back to this at the next power cycle. "
                "If the office WiFi can't be found, the hotspot comes up "
                "instead.",
    },
]

# ---------------------------------------------------------------------------
# Sprint18: which capture mode each setting belongs to. The settings page
# shows only the fields the CURRENT mode actually uses, so a customer in
# photo mode is never asked about clip length or the video ring buffer.
# One table beats an "applies" key scattered across 18 dicts.
#   video  -> only meaningful while capture_mode is "video"
#   stills -> only meaningful while capture_mode is "progressive_jpeg"
# Anything unlisted applies to both.
# ---------------------------------------------------------------------------
_FIELDS_BY_KEY = {f["key"]: f for f in FIELDS}

_APPLIES = {
    "video.clip_minutes": ("video",),
    "video.fps": ("video",),
    "video.bitrate_mbps": ("video",),
    "video.session_minutes": ("video",),
    "video.preset": ("video",),
    "video.encoder.denoise": ("video",),
    "video.encoder.sharpness": ("video",),
    "video.encoder.profile": ("video",),
    "video.encoder.level": ("video",),
    "video.encoder.intra": ("video",),
    "video.storage.max_used_pct": ("video",),
    "video.storage.min_free_gb": ("video",),
    "video.storage.ring_dry_run": ("video",),
    # Sprint17 made this stills-only (it no longer sizes video output).
    "progressive_jpeg.output_width": ("stills",),
}
BOTH_MODES = ("video", "stills")
for _field in FIELDS:
    _field["applies"] = _APPLIES.get(_field["key"], BOTH_MODES)

# Engineering knobs live behind the "Advanced" disclosure on the page.
ADVANCED_KEYS = ("video.encoder.denoise", "video.encoder.sharpness",
                 "video.encoder.profile", "video.encoder.level",
                 "video.encoder.intra", "video.storage.ring_dry_run")

# Page grouping (customer-facing order). Keys absent from the current
# mode are dropped; a group left with no fields is not rendered.
GROUPS = (
    ("Recording", ("capture_mode", "video.clip_minutes",
                   "video.session_minutes")),
    ("Picture quality", ("video.preset", "video.bitrate_mbps", "video.fps",
                         "progressive_jpeg.output_width")),
    ("Focus", ("image_pipeline.camera_controls.focus.mode",
               "image_pipeline.camera_controls.focus.lens_position")),
    ("Storage limits", ("video.storage.max_used_pct",
                        "video.storage.min_free_gb")),
    ("Network", ("network.default",)),
)


def mode_class(capture_mode):
    """'video' or 'stills' — which field set the page should show."""
    return "video" if str(capture_mode).strip().strip('"\'') == "video" \
        else "stills"


def applies_to(key, mode):
    f = _FIELDS_BY_KEY.get(key)
    return bool(f) and mode in f["applies"]


def field_for(key):
    return _FIELDS_BY_KEY[key]


def normalize_choice(key, value):
    """Map a raw value onto its canonical choice string, or None.

    Numeric choices match NUMERICALLY: a YAML `2.0` is the same setting
    as the dropdown's "2" (bmcam000 2026-08-18: the form echoes every
    current value back, so ONE float-formatted value poisoned every
    save with 'invalid value ... 2.0' until normalized here)."""
    f = _FIELDS_BY_KEY.get(key)
    if f is None:
        return None
    text = str(value).strip()
    for choice, _label in f["choices"]:
        if text == choice:
            return choice
    try:
        num = float(text)
    except ValueError:
        return None
    for choice, _label in f["choices"]:
        try:
            if float(choice) == num:
                return choice
        except ValueError:
            continue
    return None


def valid_choice(key, value):
    return normalize_choice(key, value) is not None


# ---------------------------------------------------------------------------
# YAML walking — indent-stack path tracker (handles the 3-deep
# camera_controls.focus nesting the tiny loaders special-case).
# ---------------------------------------------------------------------------

def _walk(lines):
    """Yield (index, path_tuple, key, line) for every `key: value` line.
    path_tuple is the enclosing section path, e.g.
    ("image_pipeline", "camera_controls", "focus")."""
    stack = []  # list of (indent, name)
    for i, raw in enumerate(lines):
        code = raw.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip(" \t"))
        stripped = code.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if stripped.endswith(":") and ":" not in stripped[:-1]:
            stack.append((indent, stripped[:-1].strip()))
            continue
        if ":" not in stripped:
            continue
        key = stripped.split(":", 1)[0].strip()
        yield i, tuple(name for _, name in stack), key, raw


def read_current(config_path):
    """{dotted_key: raw string value} for every FIELDS key present."""
    with open(config_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    wanted = set(_FIELDS_BY_KEY)
    current = {}
    for _i, path, key, raw in _walk(lines):
        dotted = ".".join(path + (key,))
        if dotted in wanted:
            value = raw.split("#", 1)[0].split(":", 1)[1].strip()
            current[dotted] = value.strip('"').strip("'")
    return current


def _render_value(field, value):
    return f'"{value}"' if field.get("quote") else str(value)


def patch_yaml(config_path, changes, *, validate=True):
    """Apply {dotted_key: value} edits in place. Returns
    {"backup": path, "changed": [dotted...]}.

    Refuses unknown keys/values and keys missing from the file. Backs up
    first; validates after; restores the backup on ANY validation
    failure so the config on disk is never invalid.
    """
    on_disk = None
    normalized = {}
    for key, value in changes.items():
        if key not in _FIELDS_BY_KEY:
            raise ValueError(f"not an editable setting: {key}")
        canon = normalize_choice(key, value)
        if canon is None:
            # The form echoes EVERY field back on submit. A value that is
            # no longer on the menu but still matches the file is a no-op,
            # not an edit — dropping it keeps unrelated saves working.
            # Without this, retiring a choice (Sprint18 dropped "heic")
            # would make every save fail on a unit still set to it: the
            # same save-poison shape as the bmcam000 float echo, 2026-08-18.
            if on_disk is None:
                on_disk = read_current(config_path)
            if key in on_disk and str(value).strip() == str(on_disk[key]).strip():
                continue
            raise ValueError(f"invalid value for {key}: {value!r}")
        normalized[key] = canon
    changes = normalized

    with open(config_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    targets = {}  # dotted -> line index
    for i, path, key, _raw in _walk(lines):
        dotted = ".".join(path + (key,))
        if dotted in changes:
            targets[dotted] = i
    missing = sorted(set(changes) - set(targets))
    if missing:
        raise ValueError(
            f"key(s) not present in {os.path.basename(config_path)}: "
            f"{', '.join(missing)} — add them to the YAML once by hand")

    changed = []
    for dotted, value in changes.items():
        i = targets[dotted]
        raw = lines[i]
        newline = "\n" if raw.endswith("\n") else ""
        code = raw.rstrip("\n")
        head, rest = code.split(":", 1)
        comment = ""
        if "#" in rest:
            rest, comment_text = rest.split("#", 1)
            comment = "   # " + comment_text.strip()
        # Compare VALUES, not formatting: an unchanged value must leave
        # its line (alignment, quoting, inline comment) byte-identical.
        # Numeric equivalence counts as unchanged (2.0 == "2").
        current_value = rest.strip().strip('"').strip("'")
        if (current_value == str(value)
                or normalize_choice(dotted, current_value) == value):
            continue
        rendered = _render_value(_FIELDS_BY_KEY[dotted], value)
        lines[i] = f"{head}: {rendered}{comment}{newline}"
        changed.append(dotted)

    backup = (f"{config_path}.before_gui_"
              f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    shutil.copy2(config_path, backup)
    with open(config_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    if validate:
        try:
            _validate_config(config_path)
        except Exception:
            shutil.copy2(backup, config_path)
            raise
    return {"backup": backup, "changed": changed}


def _validate_config(config_path):
    """Full-file validation with the SAME loaders the runtime boots with.
    Lazy imports: videoui_server stays stdlib-importable without them."""
    from spotter_time_sync import load_camera_schedule, validate_schedule

    cfg = load_camera_schedule(config_path)
    validate_schedule(cfg)
    if cfg.capture_mode == "video":
        import video_recorder

        video_recorder.load_video_config(config_path)
    # Sprint16: a present-but-invalid network island must also block the
    # save (absent island is fine — load returns None).
    import network_config

    network_config.load_network_config(config_path)
