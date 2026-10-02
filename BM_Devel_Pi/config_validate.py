#!/usr/bin/env python3
# filename: config_validate.py
# description: Sprint26 S4 a.3 — the pure whole-config validator: per-key + cross-key + environment rules over one effective config.
"""
Whole-config validation (DESIGN_supervisor.md §6.1 "validation is of the whole
result, not one key"; PLAN_S4.md a.3, G3, G12, G14).

Pure: no subprocess, no file I/O, no clock, no time-zone database. Anything
that needs the machine (ffmpeg on PATH, which zones resolve) is probed ONCE by
the caller and passed in as `env` (probe_env() below does that; the S4b
supervisor calls it outside the validator).

Inputs:  values {registry path: value} (a base, an effective, or a per-action
         copy); scope; env (None = skip the environment rules).
Outputs: validate(...) -> [Violation(code, paths, message)]
           code  "val" (one key's type/range) or "xk" (cross-key / environment)
           paths every registry path the rule involved, primary first (the
                 boot drops only the overlay-sourced ones, G3)
         base_rules(values) -> [(path, message)] — exactly the S2/S3 cross-key
           rules, for config_v2.parse_values (unchanged output)
         derived_output_width(values) -> int (G3: never wider than the crop)

Scopes:
  "base"      today's cross-key rules only (a plain boot load; never bricks)
  "strict"    base + the S4 rules (deploy / migrate strict load)
  "effective" every rule for a config the unit would RUN (set, boot overlay,
              one-shot copy). still.output_width wider than the crop is not an
              error here: the sent width is derived (G3).

Rules added in S4 (strict + effective):
  - camera.white_balance.mode manual needs camera.white_balance.gains
  - video.record.crop, when set, fits the native frame
  - mode.media video: video.send.message_cap >= VIDEO_CAP_FLOOR (80; S3b bench
    F1: cap 40 failed x264 pass 2 on bmcam003, 80 worked; nothing between
    measured, so the floor lives here, not in the registry range)
  - env: mode.media video needs ffmpeg; schedule.timezone must resolve

Rules added in Sprint27 (strict + effective; SPEC §3.4, REVIEW_r1 row 1):
  - mode.media video / video_logger: the recording geometry resolves
    (video_geometry.resolve_geometry, the loader's own check)
  - mode.media video: video.send.size even, >= 16x16, not larger than the
    recording output
  - video / video_logger with image processing on: an image-processing denoise /
    sharpness and the matching video.record.encoder knob are not both set
    (rpicam-vid refuses the repeated option; measured, Sprint27 P0)

Example:
  python3 -c "import config_validate as V, config_registry as R; \\
      d = R.defaults(); d['mode.media'] = 'still'; print(V.validate(d, 'effective'))"

Known limitations: per-key checks reuse config_registry.check_value except for
time zones (resolved through env); a video.record.framing name is checked
against video_geometry.PRESETS by check_value (an import, no I/O at runtime).
"""

from collections import namedtuple

import config_registry as R

VIDEO_CAP_FLOOR = 80

Violation = namedtuple("Violation", "code paths message")


# ---------------------------------------------------------------------------
# the S2/S3 cross-key rules (moved verbatim from config_v2._cross_key_errors)
# ---------------------------------------------------------------------------

def _rule_crop(values, derive_width):
    out = []
    crop, w, h = values.get("still.crop"), values.get("camera.native.width"), \
        values.get("camera.native.height")
    if isinstance(crop, list) and len(crop) == 4 and isinstance(w, int) and isinstance(h, int):
        x, y, cw, ch = crop
        if x + cw > w or y + ch > h:
            out.append(Violation("xk", ("still.crop", "camera.native.width",
                                        "camera.native.height"),
                                 f"{crop} does not fit the native frame {w}x{h}"))
        ow = values.get("still.output_width")
        if not derive_width and isinstance(ow, int) and ow > cw:
            out.append(Violation("xk", ("still.output_width", "still.crop"),
                                 f"{ow} is wider than still.crop w {cw} (no upscale)"))
    return out


def _rule_runnable(values):
    out = []
    for path in ("mode.run", "mode.output"):
        key = R.BY_PATH[path]
        if key.runnable and values.get(path) not in key.runnable:
            out.append(Violation("xk", (path,), f"{values.get(path)!r} is not runnable "
                                                f"(runnable: {', '.join(key.runnable)})"))
    return out


def _rule_supervisor_modes(values):
    # S3b (PLAN_S3b H1): stay_on is the supervisor's loop and needs the daemon.
    # S3c (J1, review #2): save_local needs the supervisor, and the daemon is its
    # only way back to transmit (S4 cfm / revert).
    out = []
    if values.get("mode.run") == "stay_on":
        if values.get("commands.runtime") != "supervisor":
            out.append(Violation("xk", ("mode.run", "commands.runtime"),
                                 "stay_on needs commands.runtime: supervisor"))
        if values.get("commands.enabled") is not True:
            out.append(Violation("xk", ("mode.run", "commands.enabled"),
                                 "stay_on needs commands.enabled: true"))
    if values.get("mode.output") == "save_local":
        if values.get("commands.runtime") != "supervisor":
            out.append(Violation("xk", ("mode.output", "commands.runtime"),
                                 "save_local needs commands.runtime: supervisor"))
        if values.get("commands.enabled") is not True:
            out.append(Violation("xk", ("mode.output", "commands.enabled"),
                                 "save_local needs commands.enabled: true"))
    return out


def _rule_intervals(values):
    out = []
    for path in ("mode.interval_s", "mode.heartbeat_s"):
        v = values.get(path)
        if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 60:
            out.append(Violation("xk", (path,), f"{v} must be 0 (off) or at least 60 s"))
    return out


# ---------------------------------------------------------------------------
# S4 rules
# ---------------------------------------------------------------------------

def _rule_manual_wb(values):
    if values.get("camera.white_balance.mode") == "manual" and \
            values.get("camera.white_balance.gains") is None:
        return [Violation("xk", ("camera.white_balance.mode", "camera.white_balance.gains"),
                          "manual white balance needs camera.white_balance.gains [red, blue]")]
    return []


def _rule_video_crop(values):
    crop, w, h = values.get("video.record.crop"), values.get("camera.native.width"), \
        values.get("camera.native.height")
    if isinstance(crop, list) and len(crop) == 4 and isinstance(w, int) and isinstance(h, int):
        x, y, cw, ch = crop
        if x + cw > w or y + ch > h:
            return [Violation("xk", ("video.record.crop", "camera.native.width",
                                     "camera.native.height"),
                              f"{crop} does not fit the native frame {w}x{h}")]
    return []


def _rule_video_cap_floor(values):
    cap = values.get("video.send.message_cap")
    if values.get("mode.media") == "video" and isinstance(cap, int) and cap < VIDEO_CAP_FLOOR:
        return [Violation("xk", ("video.send.message_cap", "mode.media"),
                          f"{cap} is below {VIDEO_CAP_FLOOR}: the x264 fit cannot encode a "
                          "clip that small (S3b bench F1)")]
    return []


def _rule_env(values, env):
    out = []
    if values.get("mode.media") == "video" and not env.get("ffmpeg", True):
        out.append(Violation("xk", ("mode.media",), "video needs ffmpeg (not found on PATH)"))
    zones = env.get("timezones_ok")
    tz = values.get("schedule.timezone")
    if zones is not None and tz not in zones:
        out.append(Violation("xk", ("schedule.timezone",),
                             f"{tz!r} does not resolve on this unit (tzdata)"))
    return out


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def _per_key(values):
    """Registry type/range per key; TZ keys are only shape-checked here (the
    zone itself is an env fact, _rule_env)."""
    out = []
    for key in R.KEYS:
        if key.path not in values:
            continue
        value = values[key.path]
        if key.type == R.TZ:
            why = None if value is None and key.nullable else (
                None if isinstance(value, str) and value and not R._unwritable(value)
                else "must be an IANA time zone (e.g. America/New_York)")
        else:
            why = R.check_value(key, value)
        if why:
            out.append(Violation("val", (key.path,), f"{value!r}: {why}"))
    for path in values:
        if path not in R.BY_PATH:
            out.append(Violation("val", (path,), "unknown key"))
    return out


def base_rules(values):
    """The S2/S3 cross-key rules as config_v2.parse_values reported them:
    [(primary path, message)], same order, same text."""
    rules = (_rule_crop(values, derive_width=False) + _rule_runnable(values)
             + _rule_supervisor_modes(values) + _rule_intervals(values))
    return [(v.paths[0], v.message) for v in rules]


# ---------------------------------------------------------------------------
# Sprint27 rules: a video config the v1 loaders would refuse never gets stored
# ---------------------------------------------------------------------------

# Keys whose values the video loaders read for the recording geometry
# (config_v2.render_v1_text: framing->preset, crop->crop_native_xywh, output,
# sensor_mode, fps). mode.media is named too: the rules only apply to a video
# unit, so switching INTO video with bad values must be refused as well.
VIDEO_GEOMETRY_KEYS = ("video.record.framing", "video.record.crop", "video.record.output",
                       "video.record.sensor_mode", "video.record.fps")
VIDEO_SEND_SIZE_MIN_PX = 16     # rc_video_tx.validate_video_tx_config: even WxH, both >= 16


def _video_geometry(values):
    """-> (geometry dict, None) or (None, reason). Pure: video_geometry is
    arithmetic only. Its one print (an odd output rounded to even, with a
    [VID][WARN] line) is avoided by evening the output here first, exactly as
    parse_output would, so a validate() on every ack hash stays silent
    (REVIEW_r1 A10) without swapping sys.stdout under other threads."""
    import video_geometry
    vcfg = {"fps": values.get("video.record.fps", 15)}
    for path, name in (("video.record.framing", "preset"), ("video.record.crop", "crop_native_xywh"),
                       ("video.record.output", "output"), ("video.record.sensor_mode", "sensor_mode")):
        if values.get(path) is not None:
            vcfg[name] = values[path]
    if "output" in vcfg:
        try:
            a, b = str(vcfg["output"]).strip().lower().replace(" ", "").split("x", 1)
            w, h = int(a), int(b)
            if 0 < w <= video_geometry.MAX_ENCODE_W and 0 < h <= video_geometry.MAX_ENCODE_H:
                vcfg["output"] = f"{w - w % 2}x{h - h % 2}"
        except ValueError:
            pass                 # resolve_geometry refuses it with its own message
    try:
        return video_geometry.resolve_geometry(vcfg), None
    except Exception as exc:     # GeometryError, and TypeError for crop xor output (REVIEW_r1 A2)
        return None, f"{type(exc).__name__}: {exc}"


def _rule_video_geometry(values):
    """Sprint27 (REVIEW_r1 A1/A2): on a video unit the recording geometry must
    resolve, exactly as video_recorder.load_video_config resolves it at the
    start of every run. Without this a bad remote set is acked ok and the next
    start exits 2 before the command daemon runs (rc_progressive_jpeg.py
    video config load) — unreachable until SSH. As an effective-scope rule the
    boot also DROPS such an overlay (supervisor_config.resolve, G3)."""
    if values.get("mode.media") not in ("video", "video_logger"):
        return []
    _geo, why = _video_geometry(values)
    if why:
        return [Violation("xk", VIDEO_GEOMETRY_KEYS + ("mode.media",),
                          f"video recording geometry does not resolve: {why}")]
    return []


def _rule_video_send_size(values):
    """Sprint27 (REVIEW_r1 A3): the sent clip size must be what
    rc_video_tx.load_video_tx_config accepts (even W and H, both >= 16; else
    the start exits 2) and no larger than the recording it is cut from
    (rc_video_clip refuses to upscale: the clip is lost every action)."""
    if values.get("mode.media") != "video":
        return []
    size = values.get("video.send.size")
    paths = ("video.send.size", "mode.media")
    try:
        w, h = (int(v) for v in str(size).lower().split("x"))
    except ValueError:
        return [Violation("xk", paths, f"video.send.size {size!r} is not WxH")]
    if w < VIDEO_SEND_SIZE_MIN_PX or h < VIDEO_SEND_SIZE_MIN_PX or w % 2 or h % 2:
        return [Violation("xk", paths, f"video.send.size {size} must be even and at least "
                                       f"{VIDEO_SEND_SIZE_MIN_PX}x{VIDEO_SEND_SIZE_MIN_PX}")]
    geo, _why = _video_geometry(values)
    if geo is not None:
        ow, oh = geo["output_wh"]
        if w > ow or h > oh:
            return [Violation("xk", ("video.send.size",) + VIDEO_GEOMETRY_KEYS + ("mode.media",),
                              f"video.send.size {size} is larger than the recording {ow}x{oh} "
                              "(the clip fit refuses to upscale)")]
    return []


# Camera controls and encoder knobs that emit the SAME rpicam-vid option (rc_capture image
# processing vs video_recorder.build_encoder_knob_args). Measured on bmcam004 (Sprint27 P0):
# a repeated option exits 255 "cannot be specified more than once", 0 bytes.
VIDEO_DUPLICATE_FLAGS = (("camera.image_processing.denoise", "video.record.encoder.denoise", "--denoise"),
                         ("camera.image_processing.sharpness", "video.record.encoder.sharpness",
                          "--sharpness"))


def _rule_video_duplicate_flags(values):
    """Sprint27: on a video unit, an image-processing control and the matching encoder knob
    may not both be set: rpicam-vid refuses the repeated option and the clip is lost (the
    video retry without camera controls would save it, but without ANY control)."""
    if values.get("mode.media") not in ("video", "video_logger"):
        return []
    if values.get("camera.controls_enabled") is not True or \
            values.get("camera.image_processing.enabled") is not True:
        return []
    out = []
    for ip_key, enc_key, flag in VIDEO_DUPLICATE_FLAGS:
        if values.get(ip_key) is not None and values.get(enc_key) not in (None, ""):
            out.append(Violation("xk", (ip_key, enc_key, "camera.image_processing.enabled",
                                        "camera.controls_enabled", "mode.media"),
                                 f"{ip_key} and {enc_key} would both pass {flag} to rpicam-vid, which "
                                 "refuses a repeated option: set one of them"))
    return out


def s4_rules(values):
    return (_rule_manual_wb(values) + _rule_video_crop(values) + _rule_video_cap_floor(values)
            + _rule_video_geometry(values) + _rule_video_send_size(values)
            + _rule_video_duplicate_flags(values))


def validate(values, scope="effective", env=None):
    """-> [Violation]. See the module doc for scopes. Never raises on bad values."""
    if scope not in ("base", "strict", "effective"):
        raise ValueError(f"unknown scope {scope!r}")
    out = _per_key(values)
    out += _rule_crop(values, derive_width=(scope == "effective"))
    out += _rule_runnable(values) + _rule_supervisor_modes(values) + _rule_intervals(values)
    if scope in ("strict", "effective"):
        out += s4_rules(values)
    if env is not None:
        out += _rule_env(values, env)
    return out


def derived_output_width(values):
    """The still width actually sent: still.output_width, never wider than the
    crop (G3; roi 5/6 = 800/640 crops under the 1000 px default)."""
    ow = values.get("still.output_width")
    crop = values.get("still.crop")
    if isinstance(crop, list) and len(crop) == 4 and isinstance(ow, int):
        return min(ow, crop[2])
    return ow


def probe_env(zones=()):
    """The environment facts validate() may use, probed once by the caller
    (NOT pure: PATH lookup and tzdata). `zones` = the configured zone plus any
    requested by a pending set (probing all of tzdata costs ~1 s on a Pi Zero)."""
    import shutil
    ok = set()
    for zone in zones:
        if not isinstance(zone, str):
            continue
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(zone)
            ok.add(zone)
        except Exception:
            pass
    return {"ffmpeg": shutil.which("ffmpeg") is not None, "timezones_ok": ok}
