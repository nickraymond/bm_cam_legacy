#!/usr/bin/env python3
# filename: config_registry.py
# description: Sprint26 S2a — config v2 schema registry: every setting, one entry each.
"""
The single list of camera settings for config v2 (DESIGN_supervisor.md §5).

Every key the camera reads is one `Key` below: its dotted v2 path, type,
default, range/enum, guard class, apply timing, optional short name, help line,
dashboard presets and where it came from in the v1 `camera_schedule.yaml`.
The v2 loader, the v1→v2 migrator, the (S4) `set` validation and `help`, the
bench GUI and the command reference are all generated from this list.

Inputs:  none (pure data + small helpers; stdlib only, safe to import at boot).
Outputs: KEYS (tuple of Key), BY_PATH, REMOVED_V1_KEYS, helpers below.

Rules that bind this file (§5.1, REVIEW K8/X3, S2 plan):
  - default = what v1 did when the key (or its whole block) was ABSENT, so a
    hand-written v2 file behaves like the v1 file it resembles. Exceptions are
    listed in DEFAULT_EXCEPTIONS with the reason. Migrated files spell out
    every key, so defaults only matter for new hand-written files.
  - W7 is NOT done here: the camera `*.enabled` switches and
    `camera.exposure.mode` keep their v1 meaning until their own wire commit.
  - No key segment ends in len/length/chunks/buffer/buffers/filename (the
    S2a naming rule; those words named the wrong thing in v1).
  - Only per_boot + transmit are runnable until S3 (`mode.run`, `mode.output`
    list the future values so the file format does not change later).

Known limitations: validation here is per key. Cross-key rules (crop inside
the native frame, manual WB needs gains, ...) live in the loader (S2d) and the
whole-config validator (S4).

Example:
  python3 -c "import config_registry as r; print(len(r.KEYS), r.BY_PATH['uplink.chunk_chars'])"
"""

from dataclasses import dataclass, field

SCHEMA_VERSION = 2
REGISTRY_VERSION = 1          # bump when a key is added/removed/retyped

# Guard classes (§6.3).
NONE = "none"
GUARDED_REVERT = "guarded_revert"    # applies at once, reverts without cfm
GUARDED_STAGE = "guarded_stage"      # staged until cfm (value-dependent, see guard_when)
LOCKED = "locked"                    # file paths: never settable remotely
SERVICE = "service"                  # locked for customers; HMAC-signed service set only

# Apply timing (§4 decision points).
NEXT_ACTION = "next_action"
NEXT_BOOT = "next_boot"

# Types understood by check_value().
BOOL, INT, FLOAT, STR, ENUM = "bool", "int", "float", "str", "enum"
HHMM = "hhmm"                # "HH:MM", 00:00..23:59
TZ = "tz"                    # IANA zone name (checked with zoneinfo by the loader)
ISO_UTC = "iso_utc"          # ISO-8601 timestamp string
PATH = "path"                # absolute filesystem path (always LOCKED)
CROP = "crop"                # [x, y, w, h] native-sensor px ints
WXH = "wxh"                  # "WxH" string, e.g. "480x270"
LADDER = "ladder"            # list of ints 1..95, strictly descending
GAINS = "gains"              # [red, blue] floats > 0
NETWORK_TYPE = "network_type"  # 1 (0x01 sat/cell fallback) or 2 (0x02 cellular only)
BOOL_OR_STR = "bool_or_str"  # v1 image_processing.hdr passes either through

# Key segments that named the wrong thing in v1 (S2a rule).
FORBIDDEN_SEGMENT_SUFFIXES = ("len", "length", "chunks", "buffer", "buffers", "filename")


@dataclass(frozen=True)
class Key:
    path: str
    type: str
    default: object
    help: str
    enum: tuple = ()
    range: tuple = None              # (min, max) inclusive; None = unbounded
    nullable: bool = False           # None allowed (= "not stated": v1 passes no flag)
    required: bool = False           # no silent default in a migrated file
    guard: str = NONE
    guard_when: object = None        # guarded_stage/revert only for these values (None = any)
    apply: str = NEXT_ACTION
    short: str = None                # 1-3 char name for byte-tight commands (S4)
    presets: tuple = ()              # dashboard/GUI presets: ((label, value), ...)
    v1_sources: tuple = ()           # v1 dotted paths this key is read from
    validate_when: str = None        # judged only when this mode.media is active
    wire_visible: bool = False       # value appears in a wire message (START/END/...)
    since: int = 2                   # schema version that introduced the key
    runnable: tuple = None           # values the S2 runtime can execute (None = all)

    @property
    def group(self):
        return self.path.rsplit(".", 1)[0]


# Groups whose v8 command replaces the WHOLE group and forces it on (§5
# `group_replace`): foc/awb/exp replace focus/white_balance/exposure.
GROUP_REPLACE = ("camera.focus", "camera.white_balance", "camera.exposure")

_MEDIA_STILL = "still"
_MEDIA_VIDEO = "video"           # one clip per boot, sent over the uplink (v1 video_tx)
_MEDIA_LOGGER = "video_logger"   # interim (X6): continuous SD recorder until N5

_V1_FOCUS = "image_pipeline.camera_controls.focus"
_V1_WB = "image_pipeline.camera_controls.white_balance"
_V1_EXP = "image_pipeline.camera_controls.exposure"
_V1_IP = "image_pipeline.camera_controls.image_processing"

KEYS = (
    # ---- mode ---------------------------------------------------------------
    Key("mode.media", ENUM, None, "What one action captures: a still, a sent video clip, "
        "or the continuous SD recorder (interim).",
        enum=(_MEDIA_STILL, _MEDIA_VIDEO, _MEDIA_LOGGER), required=True, short="m",
        apply=NEXT_BOOT, v1_sources=("capture_mode", "video_tx.enabled"), wire_visible=True),
    Key("mode.run", ENUM, "per_boot", "per_boot: one action per power-on then halt; "
        "stay_on: keep running (S3).", enum=("per_boot", "stay_on"), short="r",
        apply=NEXT_BOOT, runnable=("per_boot",)),
    Key("mode.output", ENUM, "transmit", "transmit over the BM uplink, or save_local to SD (S3).",
        enum=("transmit", "save_local"), guard=GUARDED_REVERT, guard_when=("save_local",),
        apply=NEXT_BOOT, runnable=("transmit",)),

    # ---- schedule -----------------------------------------------------------
    Key("schedule.timezone", TZ, "America/Los_Angeles", "Time zone of the transmit window.",
        v1_sources=("timezone_preset", "timezone"),
        presets=(("USA West Coast", "America/Los_Angeles"),
                 ("USA East Coast", "America/New_York"), ("UTC", "UTC"))),
    Key("schedule.window.enabled", BOOL, True, "Only act inside the daily window.",
        v1_sources=("enforce_time_window", "enforce_spotter_time_window")),
    Key("schedule.window.start", HHMM, "12:00", "Window start (local). start == end = all day.",
        v1_sources=("transmit_window.start",),
        presets=(("10:00-15:00", "10:00"), ("all day", "00:00"), ("08:00-12:00", "08:00"),
                 ("11:00-14:00", "11:00"))),
    Key("schedule.window.end", HHMM, "15:00", "Window end (local).",
        v1_sources=("transmit_window.end",)),

    # ---- time ---------------------------------------------------------------
    Key("time.source", ENUM, "spotter_utc", "Where UTC comes from.",
        enum=("spotter_utc", "rtc", "system"), apply=NEXT_BOOT, v1_sources=("time_source",)),
    Key("time.set_system_clock", BOOL, True, "Step the Pi clock from Spotter UTC.",
        v1_sources=("set_system_clock_from_spotter",)),
    Key("time.timeout_s", INT, 60, "Seconds to wait for Spotter UTC.", range=(1, 3600),
        v1_sources=("spotter_time_timeout_seconds",)),
    Key("time.allow_system_fallback", BOOL, False, "Use the Pi clock if Spotter UTC times out.",
        v1_sources=("allow_system_clock_fallback",)),
    Key("time.rtc.hwclock_path", PATH, "/usr/sbin/hwclock", "hwclock binary (time.source rtc).",
        guard=LOCKED, v1_sources=("rtc.hwclock_path",)),
    Key("time.rtc.set_system_clock", BOOL, True, "Step the Pi clock from the RTC.",
        v1_sources=("rtc.set_system_clock_from_rtc",)),
    Key("time.rtc.plausible_after_utc", ISO_UTC, "2026-01-01T00:00:00+00:00",
        "An RTC time before this is rejected.", v1_sources=("rtc.require_plausible_after_utc",)),

    # ---- power --------------------------------------------------------------
    Key("power.halt.enabled", BOOL, False, "Halt the Pi after the action (per_boot).",
        guard=GUARDED_STAGE, guard_when=(True,), v1_sources=("power_halt.enabled",)),
    Key("power.halt.dry_run", BOOL, True, "Log the halt instead of halting.",
        v1_sources=("power_halt.dry_run",)),
    Key("power.halt.mode", ENUM, "halt", "halt or poweroff.", enum=("halt", "poweroff"),
        v1_sources=("power_halt.mode",)),
    Key("power.halt.script_path", PATH, "/home/pi/BM_Devel_Pi/tuned_halt.sh", "Halt script.",
        guard=LOCKED, v1_sources=("power_halt.script_path",)),

    # ---- camera (shared by still and video) ---------------------------------
    Key("camera.backend", ENUM, "rpicam", "Capture backend.",
        enum=("auto", "rpicam", "libcamera", "picamera2", "legacy"), apply=NEXT_BOOT,
        v1_sources=("image_pipeline.capture_backend",)),
    Key("camera.native.width", INT, 4608, "Native capture width (px).", range=(16, 9152),
        v1_sources=("image_pipeline.source.width",)),
    Key("camera.native.height", INT, 2592, "Native capture height (px).", range=(16, 6944),
        v1_sources=("image_pipeline.source.height",)),
    Key("camera.native.jpeg_quality", INT, 95, "Native JPEG quality.", range=(1, 100),
        v1_sources=("image_pipeline.source.jpeg_quality",)),
    # Master switch for every control below (v1 camera_controls.enabled; W7 reshapes).
    Key("camera.controls_enabled", BOOL, False, "Pass any camera controls at all.",
        v1_sources=("image_pipeline.camera_controls.enabled",)),
    Key("camera.focus.enabled", BOOL, True, "Pass focus controls.",
        v1_sources=(_V1_FOCUS + ".enabled",)),
    Key("camera.focus.mode", ENUM, None, "Autofocus mode.", nullable=True, short="f",
        enum=("manual", "auto", "continuous"), v1_sources=(_V1_FOCUS + ".mode",),
        wire_visible=True,
        presets=(("autofocus", "auto"), ("manual", "manual"))),
    Key("camera.focus.lens_position", FLOAT, None, "Manual lens position (dioptres).",
        nullable=True, range=(0.0, 32.0), v1_sources=(_V1_FOCUS + ".lens_position",),
        wire_visible=True,
        presets=(("infinity", 0.0), ("2 m", 0.5), ("1 m", 1.0), ("0.5 m", 2.0),
                 ("0.25 m", 4.0))),
    Key("camera.focus.range", ENUM, None, "Autofocus range.", nullable=True,
        enum=("normal", "macro", "full"), v1_sources=(_V1_FOCUS + ".range",)),
    Key("camera.focus.speed", ENUM, None, "Autofocus speed.", nullable=True,
        enum=("normal", "fast"), v1_sources=(_V1_FOCUS + ".speed",)),
    Key("camera.white_balance.enabled", BOOL, False, "Pass white-balance controls.",
        v1_sources=(_V1_WB + ".enabled",)),
    Key("camera.white_balance.mode", ENUM, None, "AWB mode (manual = use gains).", nullable=True, short="b",
        enum=("auto", "daylight", "cloudy", "indoor", "fluorescent", "tungsten",
              "incandescent", "custom", "manual"),
        v1_sources=(_V1_WB + ".mode",), wire_visible=True,
        presets=(("auto", "auto"), ("daylight", "daylight"), ("cloudy", "cloudy"))),
    Key("camera.white_balance.gains", GAINS, None, "Manual [red, blue] gains.", nullable=True,
        v1_sources=(_V1_WB + ".red_gain", _V1_WB + ".blue_gain")),
    Key("camera.exposure.enabled", BOOL, False, "Pass exposure controls.",
        v1_sources=(_V1_EXP + ".enabled",)),
    Key("camera.exposure.mode", STR, None, "Reported as END rem only; builds no flag (W7).",
        nullable=True, v1_sources=(_V1_EXP + ".mode",), wire_visible=True),
    Key("camera.exposure.ev", FLOAT, None, "Exposure bias (EV).", nullable=True, short="e",
        range=(-8.0, 8.0), v1_sources=(_V1_EXP + ".ev",),
        presets=(("auto", None), ("-2 EV", -2.0), ("-1 EV", -1.0), ("-0.5 EV", -0.5),
                 ("+0.5 EV", 0.5), ("+1 EV", 1.0), ("+2 EV", 2.0))),
    Key("camera.exposure.shutter_us", INT, None, "Fixed shutter (us).", nullable=True,
        range=(1, 120_000_000), v1_sources=(_V1_EXP + ".shutter_us",), wire_visible=True),
    Key("camera.exposure.analogue_gain", FLOAT, None, "Fixed analogue gain.", nullable=True,
        range=(0.0, 64.0), v1_sources=(_V1_EXP + ".analogue_gain",), wire_visible=True),
    Key("camera.image_processing.enabled", BOOL, False, "Pass image-processing controls.",
        v1_sources=(_V1_IP + ".enabled",)),
    Key("camera.image_processing.sharpness", FLOAT, None, "Sharpness.", nullable=True,
        v1_sources=(_V1_IP + ".sharpness",)),
    Key("camera.image_processing.contrast", FLOAT, None, "Contrast.", nullable=True,
        v1_sources=(_V1_IP + ".contrast",)),
    Key("camera.image_processing.saturation", FLOAT, None, "Saturation.", nullable=True,
        v1_sources=(_V1_IP + ".saturation",)),
    Key("camera.image_processing.brightness", FLOAT, None, "Brightness.", nullable=True,
        v1_sources=(_V1_IP + ".brightness",)),
    Key("camera.image_processing.denoise", STR, None, "Denoise mode (passed through).",
        nullable=True, v1_sources=(_V1_IP + ".denoise",)),
    Key("camera.image_processing.hdr", BOOL_OR_STR, None, "HDR (passed through).",
        nullable=True, v1_sources=(_V1_IP + ".hdr",)),

    # ---- still --------------------------------------------------------------
    Key("still.crop", CROP, [1504, 846, 1600, 900], "Still crop [x, y, w, h], native px.",
        short="c", v1_sources=("progressive_jpeg.crop.x", "progressive_jpeg.crop.y",
                               "progressive_jpeg.crop.w", "progressive_jpeg.crop.h"),
        wire_visible=True, validate_when=_MEDIA_STILL,
        presets=(("1600x900 default", [1504, 846, 1600, 900]),
                 ("full frame", [0, 0, 4608, 2592]), ("3072x1728 wide", [768, 432, 3072, 1728]),
                 ("2304x1296 mid", [1152, 648, 2304, 1296]),
                 ("1000x562 max detail", [1804, 1015, 1000, 562]),
                 ("800x450 reef A", [1904, 1071, 800, 450]),
                 ("640x360 reef B", [1984, 1116, 640, 360]))),
    Key("still.output_width", INT, 1000, "Sent still width (px); height follows the crop.",
        range=(16, 4608), v1_sources=("progressive_jpeg.output_width",), wire_visible=True,
        validate_when=_MEDIA_STILL),
    Key("still.quality_ladder", LADDER, [15, 13, 11, 9], "JPEG qualities tried, best first.",
        v1_sources=("progressive_jpeg.quality.ladder", "progressive_jpeg.quality.q_max",
                    "progressive_jpeg.quality.q_min", "progressive_jpeg.quality.step"),
        validate_when=_MEDIA_STILL),
    Key("still.message_cap", INT, 195, "Most messages one still may use.", range=(1, 2000),
        v1_sources=("progressive_jpeg.message_cap",), validate_when=_MEDIA_STILL,
        presets=(("195 default", 195), ("100", 100), ("150", 150), ("250", 250), ("300", 300))),
    Key("still.budget_min", INT, 18, "Cycle budget for a still action (min).", range=(1, 240),
        v1_sources=("progressive_jpeg.max_run_time_min",), validate_when=_MEDIA_STILL,
        presets=(("12 min", 12), ("5 min", 5), ("8 min", 8), ("16 min", 16))),

    # ---- video: recording (clip source and the continuous recorder) ---------
    Key("video.record.framing", STR, None, "Named geometry preset (video_geometry.PRESETS); "
        "null + no crop/output = stills_roi_1000p.", nullable=True,
        v1_sources=("video.preset",)),
    Key("video.record.crop", CROP, None, "Recording crop [x, y, w, h], native px.",
        nullable=True, v1_sources=("video.crop_native_xywh",)),
    Key("video.record.output", WXH, None, "Recording output size.", nullable=True,
        v1_sources=("video.output",)),
    Key("video.record.sensor_mode", STR, None, "Explicit sensor readout mode.", nullable=True,
        v1_sources=("video.sensor_mode",)),
    Key("video.record.fps", INT, 15, "Recording frame rate.", range=(1, 30),
        v1_sources=("video.fps",)),
    Key("video.record.bitrate_mbps", FLOAT, 2.0, "Recording bitrate.", range=(0.1, 25.0),
        v1_sources=("video.bitrate_mbps",)),
    Key("video.record.dir", PATH, "/home/pi/BM_Devel_Pi/videos", "Clip folder.", guard=LOCKED,
        v1_sources=("video.dir",)),
    Key("video.record.encoder.profile", ENUM, "", "H.264 profile ('' = encoder default).",
        enum=("", "baseline", "main", "high"), v1_sources=("video.encoder.profile",)),
    Key("video.record.encoder.level", ENUM, "", "H.264 level ('' = default).",
        enum=("", "4", "4.1", "4.2"), v1_sources=("video.encoder.level",)),
    Key("video.record.encoder.intra", INT, 0, "GOP length (0 = default).", range=(0, 3000),
        v1_sources=("video.encoder.intra",)),
    Key("video.record.encoder.denoise", ENUM, "", "Denoise ('' = default).",
        enum=("", "auto", "off", "cdn_off", "cdn_fast", "cdn_hq"),
        v1_sources=("video.encoder.denoise",)),
    Key("video.record.encoder.sharpness", FLOAT, None, "Sharpness 0..16 (null = default).",
        nullable=True, range=(0.0, 16.0), v1_sources=("video.encoder.sharpness",)),

    # ---- video: the one clip sent per action (v1 video_tx) -------------------
    Key("video.send.duration_s", FLOAT, 5.0, "Clip length sent (s).", range=(1.0, 30.0),
        short="d", v1_sources=("video_tx.duration_s",), wire_visible=True,
        validate_when=_MEDIA_VIDEO),
    Key("video.send.lead_in_s", FLOAT, 2.0, "Recorded and discarded before the clip (s).",
        range=(0.0, 10.0), v1_sources=("video_tx.lead_in_s",), validate_when=_MEDIA_VIDEO),
    Key("video.send.fps", INT, 10, "Sent frame rate.", range=(1, 30),
        v1_sources=("video_tx.fps",), wire_visible=True, validate_when=_MEDIA_VIDEO),
    Key("video.send.message_cap", INT, 126, "Most messages one clip may use.", range=(8, 1000),
        v1_sources=("video_tx.message_cap",), validate_when=_MEDIA_VIDEO),
    Key("video.send.keyframe_repeat_max", INT, 30, "Keyframe repeats after the clip.",
        range=(1, 200), v1_sources=("video_tx.keyframe_repeat_max",),
        validate_when=_MEDIA_VIDEO),
    Key("video.send.size", WXH, "480x270", "Sent clip size.", v1_sources=("video_tx.output",),
        wire_visible=True, validate_when=_MEDIA_VIDEO),
    Key("video.send.x264_preset", ENUM, "medium", "x264 speed preset for the fit.",
        enum=("ultrafast", "superfast", "veryfast", "faster", "fast", "medium"),
        v1_sources=("video_tx.preset",), validate_when=_MEDIA_VIDEO),
    Key("video.send.budget_min", INT, 18, "Cycle budget for a video action (min).",
        range=(1, 240), v1_sources=("progressive_jpeg.max_run_time_min",),
        validate_when=_MEDIA_VIDEO),

    # ---- video: continuous recorder (N5), storage, recorder UI --------------
    Key("video.logger.clip_minutes", FLOAT, 5.0, "Recorder clip length (min).",
        range=(0.05, 60.0), v1_sources=("video.clip_minutes",)),
    Key("video.logger.session_minutes", INT, 0, "Recorder session (min); 0 = until power loss.",
        range=(0, 1440), v1_sources=("video.session_minutes",)),
    Key("video.storage.max_used_pct", FLOAT, 75.0, "Ring cap: most SD used (%).",
        range=(10.0, 95.0), v1_sources=("video.storage.max_used_pct",)),
    Key("video.storage.min_free_gb", FLOAT, 10.0, "Ring floor: least SD free (GB).",
        range=(0.0, 1000.0), v1_sources=("video.storage.min_free_gb",)),
    Key("video.storage.ring_dry_run", BOOL, False, "Log ring deletions only.",
        v1_sources=("video.storage.ring_dry_run",)),
    Key("video.ui.enabled", BOOL, True, "Recorder web UI.", v1_sources=("video.ui.enabled",)),
    Key("video.ui.port", INT, 8080, "Recorder web UI port.", range=(1, 65535),
        v1_sources=("video.ui.port",)),

    # ---- uplink -------------------------------------------------------------
    Key("uplink.uart.port", PATH, "/dev/ttyAMA0", "BM UART device.", guard=GUARDED_REVERT,
        apply=NEXT_BOOT, v1_sources=("uart_port",)),
    Key("uplink.uart.baudrate", INT, 115200, "BM UART baud rate.", range=(1200, 4_000_000),
        guard=GUARDED_REVERT, apply=NEXT_BOOT, v1_sources=("baudrate",)),
    Key("uplink.network_type", NETWORK_TYPE, 2, "0x02 cellular only; 0x01 adds Iridium "
        "fallback (satellite credits). Stills only; video is always cellular.",
        enum=(1, 2), guard=SERVICE, v1_sources=("bm_serial.network_type",)),
    Key("uplink.chunk_chars", INT, 300, "Base64 characters per chunk message (the backend "
        "decoder assumes it).", range=(1, 1200), guard=SERVICE,
        v1_sources=("bm_serial.image_buffer_size",), wire_visible=True),
    Key("uplink.msg_interval_s", FLOAT, 5.0, "Seconds between uplink messages.",
        range=(0.0, 120.0), v1_sources=("bm_serial.image_transmit_delay_seconds",),
        presets=(("1.0 s", 1.0), ("1.3 s", 1.3), ("1.5 s", 1.5), ("2.0 s", 2.0),
                 ("3.0 s", 3.0), ("5.0 s zero-loss", 5.0))),
    Key("uplink.lane.enabled", BOOL, False, "Keep sends out of the 5-min boundary blackout.",
        v1_sources=("transmit_phase.enabled",)),
    Key("uplink.lane.grid_s", FLOAT, 300.0, "Boundary grid (s).", range=(0.0, 3600.0),
        v1_sources=("transmit_phase.grid_seconds",)),
    Key("uplink.lane.pre_guard_s", FLOAT, 20.0, "Quiet before a boundary (s).",
        range=(0.0, 3600.0), v1_sources=("transmit_phase.pre_boundary_guard_s",)),
    Key("uplink.lane.post_guard_s", FLOAT, 30.0, "Quiet after a boundary (s).",
        range=(0.0, 3600.0), v1_sources=("transmit_phase.post_boundary_guard_s",)),
    Key("uplink.lane.max_wait_s", FLOAT, 300.0, "Longest wait for a clear slot (s).",
        range=(0.0, 3600.0), v1_sources=("transmit_phase.max_wait_s",)),
    Key("uplink.media_key.enabled", BOOL, False, "Keyed media wire (rev 5); off = legacy.",
        v1_sources=("media_key.enabled",), wire_visible=True),
    Key("uplink.media_key.retain_days", FLOAT, 14.0, "Keep sent records this long (days).",
        range=(0.001, 30.0), v1_sources=("media_key.retain_days",)),

    # ---- commands -----------------------------------------------------------
    Key("commands.enabled", BOOL, False, "Listen for commands on the BM bus.",
        guard=GUARDED_REVERT, apply=NEXT_BOOT, v1_sources=("bm_commands.enabled",)),
    Key("commands.topic", STR, "bmcam/cmd", "Command topic.", guard=GUARDED_REVERT,
        apply=NEXT_BOOT, v1_sources=("bm_commands.topic",)),
    Key("commands.listen_tail_s", FLOAT, 150.0, "Listen after the action (s).",
        range=(0.0, 3600.0), v1_sources=("bm_commands.post_transmit_listen_s",)),
    Key("commands.state_path", PATH, "/home/pi/BM_Devel_Pi/bm_command_state_v2.json",
        "Command state file (v2).", guard=LOCKED, apply=NEXT_BOOT,
        v1_sources=("bm_commands.state_path",)),

    # ---- network ------------------------------------------------------------
    Key("network.default", ENUM, "none", "WiFi at boot: none (leave as is), ap (open hotspot "
        "named after the unit) or nereus_hq.", enum=("none", "ap", "nereus_hq"),
        apply=NEXT_BOOT, v1_sources=("network.default",)),
    Key("network.ap_fallback_s", INT, 90, "Raise the AP if HQ WiFi is not up after (s).",
        range=(10, 600), v1_sources=("network.ap_fallback_s",)),
    Key("network.ap_timeout_min", INT, 60, "AP turns itself off after (min).",
        range=(5, 1440), v1_sources=("network.ap_timeout_min",)),
)

BY_PATH = {k.path: k for k in KEYS}

# Registry defaults that deliberately differ from v1 absent-key behaviour.
DEFAULT_EXCEPTIONS = {
    "mode.media": "required: v1 absent capture_mode meant heic (retired); the migrator "
                  "stops for a human instead of guessing (REVIEW R2).",
    "uplink.network_type": "0x02, not v1's 0x01-by-absence: one bad default would send "
                           "bursts on satellite credits; the migrator stops if a unit "
                           "resolved 0x01 by absence (REVIEW X3).",
    "commands.state_path": "v2 uses a NEW state file so the v1 file stays untouched "
                           "(REVIEW K7).",
}

# v1 keys with no v2 key, and why (§5.2 table). The migrator reads them, checks
# them where noted, and reports each one it drops.
REMOVED_V1_KEYS = {
    "capture_mode": "folded into mode.media",
    "video_tx.enabled": "folded into mode.media (video vs video_logger)",
    "timezone_preset": "folded into schedule.timezone (the preset silently won in v1)",
    "enforce_spotter_time_window": "mirror of enforce_time_window; folded into "
                                   "schedule.window.enabled (last one in the file won)",
    "image.resolution_key": "HEIC-only, validation-only",
    "image.image_quality": "HEIC-only, validation-only",
    "image_pipeline.enabled": "HEIC-only validation gate",
    "image_pipeline.crop.mode": "dead",
    "image_pipeline.crop.x": "HEIC-only, validation-only",
    "image_pipeline.crop.y": "HEIC-only, validation-only",
    "image_pipeline.crop.w": "HEIC-only, validation-only",
    "image_pipeline.crop.h": "HEIC-only, validation-only",
    "image_pipeline.spatial.output_width": "HEIC-only, validation-only",
    "image_pipeline.spatial.output_height": "HEIC-only, validation-only",
    "image_pipeline.spatial.resample": "dead",
    "image_pipeline.heic.quality": "HEIC-only, validation-only",
    "progressive_jpeg.quality.q_max": "folded into still.quality_ladder (the resolved ladder)",
    "progressive_jpeg.quality.q_min": "folded into still.quality_ladder",
    "progressive_jpeg.quality.step": "folded into still.quality_ladder",
    "bm_commands.pre_capture_listen_s": "retired in Sprint11 (v1 warns and ignores it)",
    "bm_commands.defer_acks_during_transmit": "always on from W2 (S3); the migrator refuses "
                                              "true until then",
    "media_gid.enabled": "retired by wire rev 5; refused if true",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def groups():
    """Top-level group names in registry order (the v2 file's block order)."""
    seen = []
    for k in KEYS:
        top = k.path.split(".", 1)[0]
        if top not in seen:
            seen.append(top)
    return seen


def keys_in(prefix):
    """Keys at or under a dotted prefix ("camera.focus" -> its 5 keys)."""
    return [k for k in KEYS if k.path == prefix or k.path.startswith(prefix + ".")]


def defaults():
    """{path: default} (lists copied so callers can't mutate the registry)."""
    return {k.path: (list(k.default) if isinstance(k.default, list) else k.default)
            for k in KEYS}


def nest(flat):
    """{"a.b.c": v} -> {"a": {"b": {"c": v}}} in registry order."""
    out = {}
    order = {k.path: i for i, k in enumerate(KEYS)}
    for path in sorted(flat, key=lambda p: order.get(p, len(order))):
        node = out
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = flat[path]
    return out


def flatten(tree, prefix=""):
    """Inverse of nest(): nested dicts -> {"a.b.c": v}. Lists are values."""
    flat = {}
    for name, value in tree.items():
        path = f"{prefix}.{name}" if prefix else str(name)
        if isinstance(value, dict):
            flat.update(flatten(value, path))
        else:
            flat[path] = value
    return flat


def _unwritable(text):
    """Characters no value may carry: the v1 hand parsers cut at '#', strip
    quotes, and PyYAML reads '\\' as an escape (a bad escape makes the whole
    file unparseable, which three v1 loaders turn into silent defaults —
    network_type 0x01). Control characters are never meaningful."""
    for ch in ('#', '"', '\\'):
        if ch in text:
            return f"may not contain {ch!r}"
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        return "may not contain control characters"
    return None


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _runtime_choices(path):
    """Values the runtime itself accepts for a free-string key (the v1 loaders
    reject anything else, which would fail a boot after the v2 load)."""
    if path not in ("video.record.framing", "video.record.sensor_mode"):
        return None
    try:
        import video_geometry
    except ImportError:
        return None
    table = video_geometry.PRESETS if path.endswith("framing") else video_geometry.SENSOR_MODES
    return set(table)


def check_value(key, value):
    """Return None if `value` is valid for `key`, else a one-line reason.

    Strict: no bool-as-int, no str-as-number, finite numbers only. Used at
    migrate / deploy / (S4) set; the boot loader reports instead of raising.
    """
    import math

    if value is None:
        return None if key.nullable else "may not be null"
    t = key.type
    if t == BOOL:
        if not isinstance(value, bool):
            return "must be true or false"
    elif t in (INT,):
        if not isinstance(value, int) or isinstance(value, bool):
            return "must be an integer"
    elif t == FLOAT:
        if not _is_num(value) or not math.isfinite(value):
            return "must be a finite number"
    elif t in (STR, TZ, ISO_UTC):
        if not isinstance(value, str) or not value:
            return "must be a non-empty string"
        if t == TZ:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(value)
            except Exception:
                return "must be an IANA time zone (e.g. America/New_York)"
        if t == ISO_UTC:
            import datetime as _dt
            try:
                _dt.datetime.fromisoformat(value)
            except ValueError:
                return "must be an ISO-8601 timestamp"
        choices = _runtime_choices(key.path)
        if choices is not None and value not in choices:
            return f"must be one of {', '.join(sorted(choices))}"
    elif t == PATH:
        if not isinstance(value, str) or not value.startswith("/"):
            return "must be an absolute path"
    elif t == ENUM:
        if value not in key.enum or isinstance(value, bool):
            return f"must be one of {', '.join(repr(e) for e in key.enum)}"
    elif t == NETWORK_TYPE:
        if value not in (1, 2) or isinstance(value, bool):
            return "must be 1 (0x01) or 2 (0x02)"
    elif t == HHMM:
        ok = isinstance(value, str) and len(value) == 5 and value[2] == ":"
        if ok:
            try:
                hh, mm = int(value[:2]), int(value[3:])
                ok = 0 <= hh <= 23 and 0 <= mm <= 59
            except ValueError:
                ok = False
        if not ok:
            return "must be HH:MM"
    elif t == CROP:
        if (not isinstance(value, list) or len(value) != 4
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
                or value[0] < 0 or value[1] < 0 or value[2] <= 0 or value[3] <= 0):
            return "must be [x, y, w, h] integers (x, y >= 0; w, h > 0)"
    elif t == WXH:
        parts = value.lower().split("x") if isinstance(value, str) else []
        if len(parts) != 2 or not all(p.isdigit() and int(p) > 0 for p in parts):
            return "must be WxH, e.g. 480x270"
    elif t == LADDER:
        if (not isinstance(value, list) or not value
                or not all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 95
                           for v in value)
                or any(a <= b for a, b in zip(value, value[1:]))):
            return "must be a strictly descending list of integers 1..95"
    elif t == GAINS:
        if (not isinstance(value, list) or len(value) != 2
                or not all(_is_num(v) and math.isfinite(v) and v > 0 for v in value)):
            return "must be [red, blue], both > 0"
    elif t == BOOL_OR_STR:
        if not isinstance(value, (bool, str)):
            return "must be true/false or a string"
    else:
        return f"unknown registry type {t!r}"

    if isinstance(value, str):
        bad = _unwritable(value)
        if bad:
            return bad
    if key.enum and t != ENUM and value not in key.enum:
        return f"must be one of {', '.join(repr(e) for e in key.enum)}"
    if key.range and _is_num(value):
        lo, hi = key.range
        if not lo <= value <= hi:
            return f"must be in {lo}..{hi}"
    return None
