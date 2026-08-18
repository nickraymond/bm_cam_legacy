#!/usr/bin/env python3
# filename: video_geometry.py
# description: Sprint17 video-only geometry — sensor modes, presets, no-upscale rule.
"""
Sprint17 (D-S17-1..3): video geometry that is INDEPENDENT of the stills
geometry, and that can never silently upscale.

WHY THIS MODULE EXISTS (the Sprint15 defect it replaces)
--------------------------------------------------------
Sprint15's D-S15-3 derived video geometry from the stills keys and stated
that `--roi` fractions are "fractions of the 4608x2592 sensor". Measured on
bmcam000 2026-08-18 (runs/sprint17_sensor_mode_probe_20260818/), both halves
of that are wrong:

  1. rpicam-vid picks the SENSOR MODE from --width/--height ALONE. `--roi` is
     a digital zoom applied afterwards, on the already-chosen mode. The
     shipped argv (1000x562 out, roi 0.347) selected mode 1536x864 and left
     533x299 real pixels behind the output -> a 1.88x ISP UPSCALE.
  2. `--roi` fractions are relative to THAT MODE's field of view, not the
     sensor. Mode 1536x864 reads only the centre 3072x1728 of the array, so
     the intended crop (1504,846,1600,900) actually landed at
     (1770,996,1066,599) — video framing never matched stills framing.

So this module does three things the old path did not:
  - always names the sensor mode explicitly (never lets rpicam-vid choose),
  - converts a native crop to --roi against THAT MODE's field of view,
  - refuses, at config time, any combination that would upscale.

COORDINATE SYSTEMS (manifesto rule 12) — every function below is labelled:
  native px   : 4608x2592 IMX708 sensor-equivalent. `crop_native_xywh` and
                every FOV rectangle are in these.
  mode px     : what a sensor mode actually reads out (e.g. 2304x1296).
  fov         : the native-coordinate rectangle a mode covers. NOT always the
                whole sensor — see SENSOR_MODES.
  available px: mode px inside the crop = the REAL detail behind the output.
  roi fraction: 0..1 of the mode's fov, what --roi takes.
  output px   : encoded --width/--height.

Inputs  : the `video:` config island (video_recorder.load_video_config).
Outputs : resolve_geometry() -> a dict every caller (encoder argv, sidecar,
          boot log, GUI) reads instead of re-deriving anything.

Known limitations:
  - SENSOR_MODES is the IMX708 wide module as measured on bmcam000. A
    different sensor needs its own table; nothing here auto-discovers.
  - The 1920x1080 encoder ceiling is a VC4 hardware limit, not a policy.
"""

# Native sensor-equivalent frame every crop is expressed in (IMX708 full).
NATIVE_W, NATIVE_H = 4608, 2592

# Hardware H.264 encoder ceiling (VC4). Measured: 2304x1296 fails at pipeline
# start with "failed to start output streaming" (probe modes/wide_full_1440_wide).
MAX_ENCODE_W, MAX_ENCODE_H = 1920, 1080

# 1080p30 aborted on teardown (rc=134, "double free or corruption") after 570
# good frames — and a nonzero encoder rc makes record_one_clip drop the whole
# clip, so every clip would be lost. ONE observation; blocked until reproduced.
# See SPEC Finding 5 / gate 7.
FPS30_BLOCK_ABOVE_PIXELS = 1280 * 720

# Sensor modes, MEASURED on bmcam000 2026-08-18 (probe -v 2 output):
#   mode_wh  : the readout size
#   fov_xywh : the NATIVE-coordinate rectangle that mode covers. Read straight
#              off libcamera's "ScalerCrop : [...]" upper bound. The 1536x864
#              mode covers only the CENTRE 3072x1728 — this is the trap that
#              shifted video framing for a month.
#   max_fps  : from the mode-selection scoring lines ("...,1536x864/120.135").
SENSOR_MODES = {
    "1536x864":  {"mode_wh": (1536, 864),   "fov_xywh": (768, 432, 3072, 1728),
                  "max_fps": 120.1},
    "2304x1296": {"mode_wh": (2304, 1296),  "fov_xywh": (0, 0, 4608, 2592),
                  "max_fps": 56.0},
    "4608x2592": {"mode_wh": (4608, 2592),  "fov_xywh": (0, 0, 4608, 2592),
                  "max_fps": 14.3},
}

# The 1536x864 mode is never selected by a preset: its narrower field of view
# means the same crop numbers describe a different picture than on the other
# two modes. Reachable only by an explicit `sensor_mode` override, with a warn.
DEFAULT_SENSOR_MODE_ORDER = ("2304x1296", "4608x2592")

# libcamera rounds the applied crop DOWN by up to a pixel (a 1600 px request
# lands as 1599). That is not upscaling in any meaningful sense.
UPSCALE_SLACK_PX = 2

FULL_SENSOR_CROP = (0, 0, NATIVE_W, NATIVE_H)
STILLS_ROI_CROP = (1504, 846, 1600, 900)   # the RC's frozen stills box (S07)

# Preset table — LOCKED at spec review (Nick 2026-08-18, SPEC §4).
#   crop     : NATIVE px field of view
#   mode     : sensor mode key (explicit, always)
#   output   : encoded output px
#   max_fps  : ceiling for this preset (mode readout limit, rounded down)
#   bitrate  : the recommended Mbps (0.3 bits/px/frame Hero8 class; ~0.18 for
#              the _lean rows). ADVISORY — video.bitrate_mbps still wins.
PRESETS = {
    "wide_1080p": {
        "crop": FULL_SENSOR_CROP, "mode": "2304x1296", "output": (1920, 1080),
        "max_fps": 15, "bitrate_mbps": 9.3,
        "note": "full sensor field, honest 1080p (0.83x downscale)",
    },
    "wide_1080p_lean": {
        "crop": FULL_SENSOR_CROP, "mode": "2304x1296", "output": (1920, 1080),
        "max_fps": 15, "bitrate_mbps": 6.0,
        "note": "full sensor field, 1080p at a leaner bitrate",
    },
    "wide_720p": {
        "crop": FULL_SENSOR_CROP, "mode": "2304x1296", "output": (1280, 720),
        "max_fps": 30, "bitrate_mbps": 4.0,
        "note": "full sensor field, 720p (0.56x downscale)",
    },
    "wide_720p_lean": {
        "crop": FULL_SENSOR_CROP, "mode": "2304x1296", "output": (1280, 720),
        "max_fps": 30, "bitrate_mbps": 2.5,
        "note": "full sensor field, 720p at a leaner bitrate",
    },
    "stills_roi_1000p": {
        "crop": STILLS_ROI_CROP, "mode": "4608x2592", "output": (1000, 562),
        "max_fps": 14, "bitrate_mbps": 2.5,
        "note": "the stills ROI, done honestly (migration default)",
    },
    "stills_roi_1600p": {
        "crop": STILLS_ROI_CROP, "mode": "4608x2592", "output": (1600, 900),
        "max_fps": 14, "bitrate_mbps": 6.5,
        "note": "the stills ROI at true 1:1",
    },
}

# What a unit with no video geometry keys resolves to (SPEC §5.1 option A,
# Nick 2026-08-18): the geometry its YAML always MEANT, rendered honestly.
# Not byte-identical to shipped behaviour — shipped behaviour is the bug.
MIGRATION_PRESET = "stills_roi_1000p"


class GeometryError(ValueError):
    """Config-time geometry refusal. Loud and named, per the SPEC's no-upscale
    rule — a unit must never record fake resolution."""


# ---------------------------------------------------------------------------
# Parsing helpers (the field units have no PyYAML; values arrive as strings)
# ---------------------------------------------------------------------------

def parse_crop(value):
    """'1504,846,1600,900' or '[0, 0, 4608, 2592]' -> (x, y, w, h) NATIVE px."""
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = str(value).strip().strip("[]").split(",")
    if len(parts) != 4:
        raise GeometryError(
            f"video.crop_native_xywh needs 4 values x,y,w,h; got {value!r}")
    try:
        x, y, w, h = (int(str(p).strip()) for p in parts)
    except ValueError:
        raise GeometryError(
            f"video.crop_native_xywh values must be integers; got {value!r}")
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise GeometryError(
            f"video.crop_native_xywh x/y must be >= 0 and w/h > 0; got {(x, y, w, h)}")
    if x + w > NATIVE_W or y + h > NATIVE_H:
        raise GeometryError(
            f"video.crop_native_xywh {(x, y, w, h)} exceeds the native "
            f"{NATIVE_W}x{NATIVE_H} frame")
    return (x, y, w, h)


def parse_output(value):
    """'1920x1080' -> (1920, 1080) OUTPUT px, evened for H.264."""
    text = str(value).strip().lower().replace(" ", "")
    if "x" not in text:
        raise GeometryError(f"video.output must look like 1920x1080; got {value!r}")
    a, b = text.split("x", 1)
    try:
        w, h = int(a), int(b)
    except ValueError:
        raise GeometryError(f"video.output must be WxH integers; got {value!r}")
    if w <= 0 or h <= 0:
        raise GeometryError(f"video.output must be positive; got {value!r}")
    if w > MAX_ENCODE_W or h > MAX_ENCODE_H:
        raise GeometryError(
            f"video.output {w}x{h} exceeds the {MAX_ENCODE_W}x{MAX_ENCODE_H} "
            "hardware encoder ceiling (2304x1296 fails to start streaming)")
    w2, h2 = w - (w % 2), h - (h % 2)
    if (w2, h2) != (w, h):
        print(f"[VID][WARN] output {w}x{h} -> {w2}x{h2} "
              "(H.264 needs even dimensions; rounded down)")
    return (w2, h2)


# ---------------------------------------------------------------------------
# The two calculations the Sprint15 path got wrong
# ---------------------------------------------------------------------------

def crop_to_roi(crop_native_xywh, sensor_mode):
    """NATIVE crop box -> `--roi` fractions OF THAT MODE'S FIELD OF VIEW.

    This is the D-S15-3 fix. The old code divided by 4608x2592 unconditionally,
    which is only correct for modes whose fov IS the full sensor. On the
    1536x864 mode (fov = centre 3072x1728) it silently moved the picture.

    Raises GeometryError if the crop falls outside the mode's field — asking
    for a corner of the sensor on a centre-only mode is a config error, not
    something to quietly clamp.
    """
    spec = _mode_spec(sensor_mode)
    fx, fy, fw, fh = spec["fov_xywh"]
    x, y, w, h = crop_native_xywh
    if x < fx or y < fy or x + w > fx + fw or y + h > fy + fh:
        raise GeometryError(
            f"crop {(x, y, w, h)} (native px) falls outside sensor mode "
            f"{sensor_mode}'s field of view {(fx, fy, fw, fh)} (native px) — "
            f"pick a wider mode or a smaller crop")
    return "{:.6f},{:.6f},{:.6f},{:.6f}".format(
        (x - fx) / fw, (y - fy) / fh, w / fw, h / fh)


def available_pixels(crop_native_xywh, sensor_mode):
    """Real detail inside the crop, in MODE px — the number that decides
    whether an output size is honest.

        available = crop_native * (mode_px / fov_native)

    Worked example (the shipped defect): crop (1770,996,1066,599) on mode
    1536x864 whose fov is 3072x1728 -> 1066 * 1536/3072 = 533 px behind a
    1000 px output. That is the 1.88x upscale, in one line.
    """
    spec = _mode_spec(sensor_mode)
    mw, mh = spec["mode_wh"]
    _fx, _fy, fw, fh = spec["fov_xywh"]
    _x, _y, w, h = crop_native_xywh
    return (int(w * mw / fw), int(h * mh / fh))


def _mode_spec(sensor_mode):
    spec = SENSOR_MODES.get(str(sensor_mode).strip())
    if spec is None:
        raise GeometryError(
            f"unknown video.sensor_mode {sensor_mode!r}; known: "
            f"{', '.join(sorted(SENSOR_MODES))}")
    return spec


def mode_argument(sensor_mode):
    """Sensor mode key -> the `--mode W:H:10:P` argv value (D-S17-2: we always
    name the mode; auto-selection is what caused the defect)."""
    mw, mh = _mode_spec(sensor_mode)["mode_wh"]
    return f"{mw}:{mh}:10:P"


def pick_sensor_mode(crop_native_xywh, output_wh):
    """Cheapest mode that covers the crop AND supplies >= the output px.

    Prefers the binned 2304x1296 readout over the full 4608x2592 one: same
    field of view, far less CMA and CPU (the full mode ran CmaFree to 292 kB
    and caps at ~14.3 fps). Only used when a config names no mode.
    """
    out_w, out_h = output_wh
    for key in DEFAULT_SENSOR_MODE_ORDER:
        try:
            avail_w, avail_h = available_pixels(crop_native_xywh, key)
        except GeometryError:
            continue                      # crop outside this mode's field
        if (avail_w >= out_w - UPSCALE_SLACK_PX
                and avail_h >= out_h - UPSCALE_SLACK_PX):
            return key
    raise GeometryError(
        f"no sensor mode can supply {out_w}x{out_h} output px from crop "
        f"{tuple(crop_native_xywh)} (native px) without upscaling — widen the "
        f"crop or lower video.output")


# ---------------------------------------------------------------------------
# The resolver every caller uses
# ---------------------------------------------------------------------------

def clamp_fps(fps, sensor_mode, output_wh):
    """Clamp fps to what the mode can actually read out, and apply the 1080p30
    block. Returns (fps, notes[]).

    Clamped LOUDLY rather than refused: rule 5 says a field unit must record,
    not brick, on a geometry nit. The 30 fps block IS a refusal, though — it
    would cost every clip (Finding 5).
    """
    notes = []
    fps = int(fps)
    out_w, out_h = output_wh
    if fps >= 30 and out_w * out_h > FPS30_BLOCK_ABOVE_PIXELS:
        raise GeometryError(
            f"video.fps {fps} at {out_w}x{out_h} is blocked: 1080p30 aborted "
            "on encoder teardown (rc=134) in the Sprint17 probe, and a nonzero "
            "encoder rc drops the whole clip. Use 15 fps at this size, or "
            "30 fps at 1280x720 or smaller.")
    cap = int(_mode_spec(sensor_mode)["max_fps"])
    if fps > cap:
        notes.append(
            f"fps {fps} exceeds sensor mode {sensor_mode}'s {cap} fps readout "
            f"limit; clamped to {cap}")
        fps = cap
    return fps, notes


def resolve_geometry(vcfg):
    """The `video:` island -> one resolved geometry dict.

    Resolution order (most explicit wins):
      1. explicit crop_native_xywh / output / sensor_mode keys
      2. the named `preset`
      3. MIGRATION_PRESET (SPEC §5.1 option A) when the island names none

    Returns a dict with, all labelled by coordinate system:
      preset, source, crop_native_xywh, output_wh, sensor_mode, mode_wh,
      mode_fov_xywh, roi, available_px, scale, fps, notes[]

    `scale` is output_w / available_w: <1.0 downscale (honest), 1.00 exactly
    1:1, >1.0 would be upscaling and never returns — it raises first.
    """
    notes = []
    preset_name = str(vcfg.get("preset") or "").strip() or None
    has_explicit = any(vcfg.get(k) for k in ("crop_native_xywh", "output"))

    if preset_name and preset_name not in PRESETS:
        raise GeometryError(
            f"unknown video.preset {preset_name!r}; known: "
            f"{', '.join(sorted(PRESETS))}")

    if preset_name:
        base = PRESETS[preset_name]
        source = "preset"
    elif has_explicit:
        base = None
        source = "explicit"
    else:
        preset_name = MIGRATION_PRESET
        base = PRESETS[preset_name]
        source = "migration-default"
        notes.append(
            f"no video geometry keys in the config; resolved to the "
            f"{MIGRATION_PRESET} preset (Sprint17 migration default). The "
            f"picture WILL change: Sprint15 recorded an unintended "
            f"(1770,996,1066,599) native box at a 1.88x upscale.")

    crop = (parse_crop(vcfg["crop_native_xywh"])
            if vcfg.get("crop_native_xywh") else tuple(base["crop"]))
    output = (parse_output(vcfg["output"])
              if vcfg.get("output") else tuple(base["output"]))

    mode = str(vcfg.get("sensor_mode") or "").strip()
    if not mode and base is not None:
        mode = base["mode"]
    if not mode:
        mode = pick_sensor_mode(crop, output)
        notes.append(f"sensor_mode not set; picked {mode} (cheapest that "
                     f"supplies {output[0]}x{output[1]} without upscaling)")
    if mode == "1536x864":
        notes.append(
            "sensor mode 1536x864 covers only the CENTRE 3072x1728 of the "
            "sensor — the same crop numbers frame a different picture here "
            "than on the wider modes. This is the Sprint15 trap; be sure.")

    avail_w, avail_h = available_pixels(crop, mode)
    out_w, out_h = output

    # THE no-upscale rule (D-S17-3). Refuse at config time, named, with the
    # arithmetic in the message so the fix is obvious from the boot log.
    if avail_w < out_w - UPSCALE_SLACK_PX or avail_h < out_h - UPSCALE_SLACK_PX:
        raise GeometryError(
            f"refusing to upscale: crop {crop} (native px) on sensor mode "
            f"{mode} supplies only {avail_w}x{avail_h} available px, but "
            f"video.output asks for {out_w}x{out_h}. Widen the crop, pick a "
            f"denser sensor mode, or lower the output size. (This is exactly "
            f"the Sprint15 defect: 533x299 behind a 1000x562 output.)")

    roi = crop_to_roi(crop, mode)
    fps, fps_notes = clamp_fps(vcfg.get("fps", 15), mode, output)
    notes.extend(fps_notes)

    spec = _mode_spec(mode)
    return {
        "preset": preset_name,
        "source": source,
        "crop_native_xywh": crop,
        "output_wh": output,
        "sensor_mode": mode,
        "mode_wh": spec["mode_wh"],
        "mode_fov_xywh": spec["fov_xywh"],
        "mode_arg": mode_argument(mode),
        "roi": roi,
        "available_px": (avail_w, avail_h),
        "scale": round(out_w / avail_w, 3) if avail_w else None,
        "fps": fps,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Field-visible arithmetic (SPEC §4 columns; also the A/B CSV)
# ---------------------------------------------------------------------------

GIB = 1024 ** 3


def storage_math(bitrate_mbps, disk_total_gb=116.0, max_used_pct=75.0):
    """GB/day and ring window for a bitrate — the number that decides what is
    field-viable, not what looks best (SPEC scope item 5).

    Ring window is the NEWEST-footage window the D-S15-5 ring holds at the cap.
    """
    gb_per_day = float(bitrate_mbps) * 86400.0 / 8.0 / 1000.0
    usable_gb = float(disk_total_gb) * float(max_used_pct) / 100.0
    return {
        "gb_per_day": round(gb_per_day, 1),
        "ring_usable_gb": round(usable_gb, 1),
        "ring_days": round(usable_gb / gb_per_day, 2) if gb_per_day else None,
    }


def bits_per_pixel_frame(bitrate_mbps, output_wh, fps):
    """The quality-class metric. GoPro Hero8 1080p sits near 0.3; the shipped
    2 Mbps at 1000x562/15 is 0.237 — near class, which is why bitrate was never
    the reason the footage looked soft."""
    w, h = output_wh
    denom = float(w) * float(h) * float(fps)
    if denom <= 0:
        return None
    return round(float(bitrate_mbps) * 1_000_000 / denom, 3)


def describe(geo, bitrate_mbps=None):
    """The loud boot lines (D-S17-1: a unit that changed its framing announces
    it). One fact per line, print-config style."""
    lines = [
        f"[VID] geometry preset={geo['preset'] or 'custom'} "
        f"(source={geo['source']})",
        f"[VID] crop (NATIVE 4608x2592 px): {tuple(geo['crop_native_xywh'])}",
        f"[VID] sensor mode: {geo['sensor_mode']} "
        f"(readout {geo['mode_wh'][0]}x{geo['mode_wh'][1]} px, "
        f"field {tuple(geo['mode_fov_xywh'])} native px)",
        f"[VID] --roi (fractions of that mode's field): {geo['roi']}",
        f"[VID] available detail: {geo['available_px'][0]}x{geo['available_px'][1]} px "
        f"-> output {geo['output_wh'][0]}x{geo['output_wh'][1]} px "
        f"(scale {geo['scale']}x "
        f"{'1:1' if geo['scale'] == 1.0 else 'downscale' if geo['scale'] < 1 else 'UPSCALE'})",
        f"[VID] fps: {geo['fps']}",
    ]
    if bitrate_mbps is not None:
        sm = storage_math(bitrate_mbps)
        bpp = bits_per_pixel_frame(bitrate_mbps, geo["output_wh"], geo["fps"])
        lines.append(
            f"[VID] bitrate {bitrate_mbps} Mbps = {bpp} bits/px/frame "
            f"(Hero8 class ~0.3); {sm['gb_per_day']} GB/day, "
            f"ring window ~{sm['ring_days']} days")
    for note in geo["notes"]:
        lines.append(f"[VID][WARN] {note}")
    return lines
