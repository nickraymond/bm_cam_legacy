#!/usr/bin/env python3
# filename: video_recorder.py
# description: Sprint15 video mode — config island, geometry, per-clip recorder loop.
"""
Sprint15 video mode (capture_mode: video) — D-S15-1..4.

This module owns:
  - the `video:` YAML island (loader + loud validation; defaults are the
    SPEC-locked values, so a missing island resolves to the shipped config)
  - geometry DERIVED from the stills keys (SPEC constraint 4): the
    progressive_jpeg.crop native-coordinate box -> libcamera --roi
    fractions, and the stills output size -> --width/--height. There are
    NO video geometry keys.
  - the libcamera-vid/rpicam-vid argv builder (camera controls ride the
    SAME builder the stills path uses: _camera_controls_from_settings)
  - run_video_mode(): the per-clip record loop (D-S15-2), filled in by
    tracker chunk 2.

Coordinate systems (manifesto rule 12):
  - progressive_jpeg.crop x/y/w/h: NATIVE 4608x2592 sensor-equivalent px
  - libcamera --roi x,y,w,h: fractions of the full sensor field (0..1)
  - --width/--height: encoded OUTPUT pixels (H.264 requires even values)

Example (defaults): crop (1504, 846, 1600, 900) ->
  --roi 0.326389,0.326389,0.347222,0.347222  --width 1000 --height 562

Known limitations:
  - Encoder/muxer subprocesses are injected for tests; only the bench
    proves the real ones (SPEC gates 2-3).
"""

import copy
import os
import shutil

from process_image_v2 import _camera_controls_from_settings

# Native sensor-equivalent frame every crop is expressed in (IMX708 full).
NATIVE_W, NATIVE_H = 4608, 2592

DEFAULT_VIDEO_DIR = "/home/pi/BM_Devel_Pi/videos"

# SPEC-locked defaults (Nick 2026-08-17). A missing/partial island resolves
# to exactly these, so `capture_mode: video` alone is a valid production
# switch (constraint 10).
DEFAULT_VIDEO_CONFIG = {
    "clip_minutes": 5.0,     # per-clip length (constraint 7); fractional OK for bench
    "fps": 15,
    "bitrate_mbps": 2.0,
    "session_minutes": 0,    # 0 = record until power loss; >0 = N min then normal halt
    "dir": DEFAULT_VIDEO_DIR,
    "storage": {
        "max_used_pct": 75.0,   # ring cap primary knob (D-S15-5)
        "min_free_gb": 10.0,    # absolute floor backstop; stricter wins
        "ring_dry_run": False,
    },
    "ui": {
        "enabled": True,
        "port": 8080,
    },
}


# ---------------------------------------------------------------------------
# video: island — loader + validation
# ---------------------------------------------------------------------------

def _strip_yaml_value(value):
    return value.strip().strip('"').strip("'")


def _parse_number(name, value, lo, hi, integer=False):
    """Strict numeric parse with a loud, named failure (SPEC: no silent
    fallback on nonsense values — a typo must fail at config time)."""
    try:
        num = float(value)
    except Exception:
        raise ValueError(f"video.{name} must be a number, got {value!r}")
    if integer:
        if num != int(num):
            raise ValueError(f"video.{name} must be an integer, got {value!r}")
        num = int(num)
    if not (lo <= num <= hi):
        raise ValueError(f"video.{name} must be in {lo}..{hi}, got {num}")
    return num


def _parse_strict_bool(name, value):
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"video.{name} must be true or false, got {value!r}")


def validate_video_config(cfg):
    """Range-check a video config dict in place; raises ValueError, loudly
    named per key. Bounds are sanity guards, not tuning advice — the locked
    defaults sit well inside every range."""
    cfg["clip_minutes"] = _parse_number(
        "clip_minutes", cfg["clip_minutes"], 0.05, 60)
    cfg["fps"] = _parse_number("fps", cfg["fps"], 1, 30, integer=True)
    cfg["bitrate_mbps"] = _parse_number(
        "bitrate_mbps", cfg["bitrate_mbps"], 0.1, 25)
    cfg["session_minutes"] = _parse_number(
        "session_minutes", cfg["session_minutes"], 0, 1440, integer=True)
    if not str(cfg["dir"]).strip():
        raise ValueError("video.dir must not be empty")
    st = cfg["storage"]
    st["max_used_pct"] = _parse_number(
        "storage.max_used_pct", st["max_used_pct"], 10, 95)
    st["min_free_gb"] = _parse_number(
        "storage.min_free_gb", st["min_free_gb"], 0, 1000)
    st["ring_dry_run"] = _parse_strict_bool(
        "storage.ring_dry_run", st["ring_dry_run"])
    ui = cfg["ui"]
    ui["enabled"] = _parse_strict_bool("ui.enabled", ui["enabled"])
    ui["port"] = _parse_number("ui.port", ui["port"], 1, 65535, integer=True)
    return cfg


def load_video_config(config_path):
    """Parse the `video:` island from camera_schedule.yaml.

    Same tiny hand parser style as load_camera_schedule (no PyYAML on the
    field unit). Missing file or island -> SPEC defaults. Invalid values
    raise ValueError naming the exact video.<key>.
    """
    cfg = copy.deepcopy(DEFAULT_VIDEO_CONFIG)
    cfg["source"] = "defaults"
    if not config_path or not os.path.exists(config_path):
        return validate_video_config(cfg)

    in_video = False
    subsection = None
    saw_key = False

    with open(config_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip("\n").rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            stripped = line.strip()

            if stripped.endswith(":") and ":" not in stripped[:-1]:
                name = stripped[:-1].strip()
                if indent == 0:
                    in_video = (name == "video")
                    subsection = None
                elif in_video:
                    subsection = name
                continue

            if ":" not in stripped:
                continue
            if indent == 0:
                # Top-level key: value line ends any open section.
                in_video = False
                subsection = None
                continue
            if not in_video:
                continue

            key, value = stripped.split(":", 1)
            key = key.strip()
            value = _strip_yaml_value(value)

            if indent <= 2:
                subsection = None
                if key == "clip_minutes":
                    cfg["clip_minutes"] = value
                elif key == "fps":
                    cfg["fps"] = value
                elif key == "bitrate_mbps":
                    cfg["bitrate_mbps"] = value
                elif key == "session_minutes":
                    cfg["session_minutes"] = value
                elif key == "dir":
                    cfg["dir"] = value
                else:
                    print(f"[VID][WARN] unknown key video.{key} ignored")
                    continue
                saw_key = True
                continue

            if subsection == "storage":
                if key in ("max_used_pct", "min_free_gb", "ring_dry_run"):
                    cfg["storage"][key] = value
                    saw_key = True
                else:
                    print(f"[VID][WARN] unknown key video.storage.{key} ignored")
                continue

            if subsection == "ui":
                if key in ("enabled", "port"):
                    cfg["ui"][key] = value
                    saw_key = True
                else:
                    print(f"[VID][WARN] unknown key video.ui.{key} ignored")
                continue

    if saw_key:
        cfg["source"] = "yaml"
    return validate_video_config(cfg)


def print_video_settings(vcfg):
    """One loud line per fact, print-config style."""
    print(f"[VID] video island (source={vcfg['source']}): "
          f"clip_minutes={vcfg['clip_minutes']} fps={vcfg['fps']} "
          f"bitrate_mbps={vcfg['bitrate_mbps']} "
          f"session_minutes={vcfg['session_minutes']}")
    st, ui = vcfg["storage"], vcfg["ui"]
    print(f"[VID] storage ring: max_used_pct={st['max_used_pct']} "
          f"min_free_gb={st['min_free_gb']} dry_run={st['ring_dry_run']} "
          f"dir={vcfg['dir']}")
    print(f"[VID] ui: enabled={ui['enabled']} port={ui['port']}")


# ---------------------------------------------------------------------------
# Geometry — derived from the stills keys (D-S15-3), never restated
# ---------------------------------------------------------------------------

def crop_xywh_to_roi(crop_xywh, native_wh=(NATIVE_W, NATIVE_H)):
    """NATIVE-coordinate crop box -> libcamera --roi normalized fractions.

    Input coords are native 4608x2592 sensor-equivalent pixels — the SAME
    progressive_jpeg.crop values the stills path crops with, so a YAML (or
    roi-command) crop change moves both paths together (constraint 4).
    """
    x, y, w, h = [int(v) for v in crop_xywh]
    nw, nh = int(native_wh[0]), int(native_wh[1])
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise ValueError(
            f"crop x/y must be >= 0 and w/h > 0, got {(x, y, w, h)}")
    if x + w > nw or y + h > nh:
        raise ValueError(
            f"crop {(x, y, w, h)} exceeds native frame {nw}x{nh}")
    return f"{x / nw:.6f},{y / nh:.6f},{w / nw:.6f},{h / nh:.6f}"


def even_video_output_size(output_size):
    """Round the stills output size DOWN to even dims (H.264 requirement).

    Returns ((w, h), adjusted). Today's 1000x562 passes through untouched;
    a crop/output combination that lands odd is corrected (never upscaled)
    with a loud note instead of failing the boot — a video unit must
    record, not brick, on a geometry nit (constraint 5 doctrine).
    """
    w, h = int(output_size[0]), int(output_size[1])
    w2, h2 = w - (w % 2), h - (h % 2)
    if w2 <= 0 or h2 <= 0:
        raise ValueError(f"output size {w}x{h} too small for H.264")
    adjusted = (w2, h2) != (w, h)
    if adjusted:
        print(f"[VID][WARN] output {w}x{h} -> {w2}x{h2} "
              f"(H.264 needs even dimensions; rounded down)")
    return (w2, h2), adjusted


def _select_video_command(capture_backend):
    """rpicam-vid/libcamera-vid resolution, mirroring the stills
    _select_camera_command backend semantics (bmcam000: libcamera-vid
    verified present, Sprint15 setup)."""
    backend = (capture_backend or "auto").strip().lower()
    if backend in {"auto", "rpicam"}:
        cmd = shutil.which("rpicam-vid")
        if cmd:
            return cmd, "rpicam"
    if backend in {"auto", "rpicam", "libcamera"}:
        cmd = shutil.which("libcamera-vid")
        if cmd:
            return cmd, "libcamera"
    raise RuntimeError(
        "No supported video command found. Expected rpicam-vid or "
        f"libcamera-vid for capture_backend={capture_backend!r}.")


def build_encoder_command(settings, vcfg, h264_path, *, binary=None,
                          controls=None):
    """argv for ONE clip's encoder process (D-S15-2).

    - geometry: settings['crop_native_xywh'] -> --roi, settings
      ['output_size'] -> --width/--height (evened). BOTH come from the
      stills keys via resolve_rc_settings — no video geometry exists.
    - camera controls: the SAME _camera_controls_from_settings builder the
      stills capture uses (focus/AWB/exposure parity, constraint 4).
    - --inline repeats SPS/PPS headers so the raw .h264.part muxes cleanly
      even when a clip is cut mid-stream.

    Returns (argv, requested_controls_metadata).
    """
    if binary is None:
        binary, _ = _select_video_command(settings["capture_backend"])
    duration_ms = int(round(float(vcfg["clip_minutes"]) * 60 * 1000))
    (w, h), _ = even_video_output_size(settings["output_size"])
    roi = crop_xywh_to_roi(settings["crop_native_xywh"])
    argv = [
        binary,
        "-n",                       # no preview (headless field unit)
        "-t", str(duration_ms),
        "--codec", "h264",
        "--inline",
        "--width", str(w),
        "--height", str(h),
        "--framerate", str(int(vcfg["fps"])),
        "--bitrate", str(int(round(float(vcfg["bitrate_mbps"]) * 1_000_000))),
        "--roi", roi,
        "-o", h264_path,
    ]
    controls_args, requested = _camera_controls_from_settings(
        {"camera_controls": controls} if controls else None)
    argv.extend(controls_args)
    return argv, requested


# ---------------------------------------------------------------------------
# Clip loop (D-S15-2) — tracker chunk 2
# ---------------------------------------------------------------------------

def run_video_mode(settings, **kwargs):
    """Per-clip record loop. Implemented in tracker chunk 2."""
    raise NotImplementedError("video clip loop lands in tracker chunk 2")
