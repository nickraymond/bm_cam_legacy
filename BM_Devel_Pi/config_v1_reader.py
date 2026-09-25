#!/usr/bin/env python3
# filename: config_v1_reader.py
# description: Sprint26 S2a — read a v1 camera_schedule.yaml into v2 registry values, each key through its OWN v1 loader.
"""
Reads a v1 `camera_schedule.yaml` and returns one value per config v2 registry
key (config_registry.KEYS), plus the problems a human must look at before the
file may be migrated.

Why each key goes through its own v1 loader (DESIGN_supervisor.md §5,
REVIEW K8): v1 has three parser families (PyYAML, section hand parsers, flat
island line parsers) that disagree on bools, quotes, `#` and hex. The value a
v1 unit actually RUNS with is whatever its own loader returned, so that is the
value migrated. Where two v1 readers of the same key disagree, or a value is
one v1 silently ignored or coerced, it is reported as a problem instead of
being guessed.

Inputs:  path to a v1 YAML (the file is only read).
Outputs: V1Read(values={v2 path: value}, problems=[str], notes=[str],
         dropped={v1 path: reason}).
Stop conditions (problems, REVIEW R2/X3/R1): missing or `heic` capture_mode;
network_type that resolved to 0x01 because the key was absent or PyYAML was
missing; media_gid enabled; any unknown v1 key; ambiguous values.

Assumptions: runs where the v1 runtime runs (BM_Devel_Pi on sys.path) with
PyYAML installed; without PyYAML it refuses (three v1 loaders silently return
defaults without it).

Example:
  python3 -c "import config_v1_reader as r, json; x = r.read_v1('camera_schedule.yaml'); \
print(json.dumps(x.values, indent=1)); print(x.problems)"
"""

import contextlib
import io
import os
from dataclasses import dataclass, field

import config_registry as R


@dataclass
class V1Read:
    values: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)   # stop: a human must decide
    notes: list = field(default_factory=list)      # informational (report)
    dropped: dict = field(default_factory=dict)    # v1 keys present in the file with no v2 key


# The whole camera_controls subtree is read by rc_capture with these keys.
_CONTROL_KEYS = {
    "enabled": None,
    "focus": ("enabled", "mode", "lens_position", "range", "speed"),
    "white_balance": ("enabled", "mode", "red_gain", "blue_gain"),
    "exposure": ("enabled", "mode", "ev", "shutter_us", "analogue_gain"),
    "image_processing": ("enabled", "sharpness", "contrast", "saturation", "brightness",
                         "denoise", "hdr"),
}
_CC = "image_pipeline.camera_controls"


def _known_v1_paths():
    known = set(R.REMOVED_V1_KEYS)
    for k in R.KEYS:
        known.update(k.v1_sources)
    for group, keys in _CONTROL_KEYS.items():
        if keys is None:
            known.add(f"{_CC}.{group}")
        else:
            known.update(f"{_CC}.{group}.{k}" for k in keys)
    return known


def _quiet(fn, *a, **k):
    """Run a v1 loader without its console chatter; return (result, printed)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*a, **k)
    return result, buf.getvalue()


def _top_level_duplicates(text):
    """Top-level keys that appear more than once (v1 parsers disagree on those:
    hand parsers merge / last-wins, safe_load keeps only the last block)."""
    seen, dups = set(), set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line or line[0] in " \t" or ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if name in seen:
            dups.add(name)
        seen.add(name)
    return sorted(dups)


def _num(value, integer=False):
    """Registry-typed number from a v1-parsed value (bools are not numbers)."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"not a number: {value!r}")
    if integer:
        f = float(value)
        if f != int(f):
            raise ValueError(f"not an integer: {value!r}")
        return int(f)
    return float(value)


def _crop(text):
    parts = [p.strip() for p in str(text).split(",")]
    return [int(p) for p in parts]


def read_v1(config_path):
    """Read every registry key from a v1 YAML. Never raises for bad content:
    everything a human must look at lands in .problems."""
    out = V1Read()
    p = out.problems

    try:
        import yaml  # noqa: F401  (three v1 loaders silently return {} without it)
    except ImportError:
        p.append("PyYAML is not installed: bm_serial, bm_commands and camera_controls would "
                 "silently read defaults (network_type 0x01, 300/5.0 pacing, no controls)")
        return out
    import yaml

    if not os.path.exists(config_path):
        p.append(f"file not found: {config_path}")
        return out
    with open(config_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        p.append(f"YAML does not parse (v1 PyYAML loaders would silently use defaults): {exc}")
        return out
    if not isinstance(raw, dict):
        p.append("YAML top level is not a mapping")
        return out

    # ---- unknown / removed keys, duplicates --------------------------------
    known = _known_v1_paths()
    flat = R.flatten(raw)
    prefixes = {k.rsplit(".", i)[0] for k in known for i in range(1, k.count(".") + 1)}
    for path in sorted(flat):
        if path in known:
            if path in R.REMOVED_V1_KEYS:
                out.dropped[path] = R.REMOVED_V1_KEYS[path]
            continue
        if flat[path] is None and path in prefixes:
            continue        # an empty block (`network:` with nothing under it)
        p.append(f"unknown v1 key {path!r} (no loader maps it; refuse rather than drop)")
    for name in _top_level_duplicates(text):
        p.append(f"top-level key {name!r} appears more than once (v1 parsers disagree)")

    # Imports are the v1 runtime's own loaders (heavy, but migrate-time only).
    import bm_serial
    import command_daemon
    import network_config
    import rc_media_key
    import rc_progressive_jpeg as rc
    import rc_transmit_phase
    import rc_video_tx
    import spotter_time_sync as sts
    import video_recorder
    from rc_capture import _control_bool

    v = out.values

    def attempt(label, fn, *a):
        try:
            return _quiet(fn, *a)[0]
        except Exception as exc:          # a v1 loader error is a problem, not a crash
            p.append(f"{label}: v1 loader raised {type(exc).__name__}: {exc}")
            return None

    # ---- spotter_time_sync hand parser: time, schedule, power, camera native
    cfg = attempt("load_camera_schedule", sts.load_camera_schedule, config_path)
    if cfg is None:
        return out
    try:
        _quiet(sts.validate_schedule, cfg)
    except Exception as exc:
        p.append(f"validate_schedule: {exc} (v1 would fail this boot)")

    mode = cfg.capture_mode
    if "capture_mode" not in raw:
        p.append("capture_mode is missing (v1 default heic = nothing to do): a human must "
                 "choose mode.media (REVIEW R2)")
    elif mode == "heic":
        p.append("capture_mode is heic (retired, nothing to do): a human must choose "
                 "mode.media (REVIEW R2)")

    try:
        v["schedule.timezone"] = sts.resolve_timezone(cfg)
    except Exception as exc:
        p.append(f"timezone: {exc}")
    if (cfg.timezone_preset or "").strip() and "timezone" in raw:
        out.notes.append(f"timezone_preset {cfg.timezone_preset!r} wins over timezone "
                         f"{raw.get('timezone')!r} (v1 rule); migrated as "
                         f"{v.get('schedule.timezone')!r}")
    a, b = raw.get("enforce_time_window"), raw.get("enforce_spotter_time_window")
    if a is not None and b is not None and bool(a) != bool(b):
        p.append("enforce_time_window and enforce_spotter_time_window disagree (v1: the "
                 "later line wins); a human must choose schedule.window.enabled")
    v["schedule.window.enabled"] = bool(cfg.enforce_time_window)
    for key, val in (("start", cfg.transmit_start), ("end", cfg.transmit_end)):
        if R.check_value(R.BY_PATH[f"schedule.window.{key}"], val):
            p.append(f"transmit_window.{key}={val!r} is not HH:MM (v1 keeps the raw string)")
        v[f"schedule.window.{key}"] = val

    v["time.source"] = cfg.time_source.strip().lower()
    v["time.set_system_clock"] = bool(cfg.set_system_clock_from_spotter)
    v["time.timeout_s"] = int(cfg.spotter_time_timeout_seconds)
    v["time.allow_system_fallback"] = bool(cfg.allow_system_clock_fallback)
    v["time.rtc.hwclock_path"] = cfg.rtc_hwclock_path
    v["time.rtc.set_system_clock"] = bool(cfg.set_system_clock_from_rtc)
    v["time.rtc.plausible_after_utc"] = cfg.rtc_require_plausible_after_utc

    v["power.halt.enabled"] = bool(cfg.power_halt_enabled)
    v["power.halt.dry_run"] = bool(cfg.power_halt_dry_run)
    v["power.halt.mode"] = cfg.power_halt_mode
    v["power.halt.script_path"] = cfg.power_halt_script_path

    v["camera.backend"] = cfg.image_pipeline_capture_backend
    v["camera.native.width"] = int(cfg.image_pipeline_source_width)
    v["camera.native.height"] = int(cfg.image_pipeline_source_height)
    v["camera.native.jpeg_quality"] = int(cfg.image_pipeline_source_jpeg_quality)

    # ---- still (resolve_rc_settings = what the stills cycle runs with) -----
    resolved = attempt("resolve_rc_settings", rc.resolve_rc_settings, config_path)
    if resolved is not None:
        v["still.crop"] = [int(x) for x in resolved["crop_native_xywh"]]
        v["still.output_width"] = int(resolved["output_width"])
        v["still.quality_ladder"] = [int(q) for q in resolved["quality_ladder"]]
        v["still.message_cap"] = int(resolved["message_cap"])
        v["still.budget_min"] = int(resolved["max_run_time_min"])
        v["video.send.budget_min"] = int(resolved["max_run_time_min"])
        if resolved["ladder_source"] != "explicit":
            out.notes.append(f"quality ladder computed from q_max/q_min/step -> "
                             f"{v['still.quality_ladder']} (migrated as an explicit ladder)")

    # ---- camera controls (PyYAML island, consumed by rc_capture) -----------
    ctl = attempt("camera_controls", rc._load_camera_controls_island, config_path)
    if ctl is not None:
        _read_controls(ctl, v, p)

    # ---- video (hand parser, validated) and video_tx (strict island) -------
    vid = attempt("load_video_config", video_recorder.load_video_config, config_path)
    if vid is not None:
        try:
            v["video.record.framing"] = vid["preset"]
            v["video.record.crop"] = (None if vid["crop_native_xywh"] is None
                                      else _crop(vid["crop_native_xywh"]))
            v["video.record.output"] = vid["output"]
            v["video.record.sensor_mode"] = vid["sensor_mode"]
            v["video.record.fps"] = _num(vid["fps"], integer=True)
            v["video.record.bitrate_mbps"] = _num(vid["bitrate_mbps"])
            v["video.record.dir"] = str(vid["dir"])
            enc = vid["encoder"]
            v["video.record.encoder.profile"] = str(enc["profile"])
            v["video.record.encoder.level"] = str(enc["level"])
            v["video.record.encoder.intra"] = _num(enc["intra"], integer=True)
            v["video.record.encoder.denoise"] = str(enc["denoise"])
            v["video.record.encoder.sharpness"] = (None if enc["sharpness"] is None
                                                   else _num(enc["sharpness"]))
            v["video.logger.clip_minutes"] = _num(vid["clip_minutes"])
            v["video.logger.session_minutes"] = _num(vid["session_minutes"], integer=True)
            st, ui = vid["storage"], vid["ui"]
            v["video.storage.max_used_pct"] = _num(st["max_used_pct"])
            v["video.storage.min_free_gb"] = _num(st["min_free_gb"])
            v["video.storage.ring_dry_run"] = bool(st["ring_dry_run"])
            v["video.ui.enabled"] = bool(ui["enabled"])
            v["video.ui.port"] = _num(ui["port"], integer=True)
        except (KeyError, ValueError, TypeError) as exc:
            p.append(f"video: unexpected v1 value: {exc}")

    vtx = attempt("load_video_tx_config", rc_video_tx.load_video_tx_config, config_path)
    if vtx is not None:
        v["video.send.duration_s"] = _num(vtx["duration_s"])
        v["video.send.lead_in_s"] = _num(vtx["lead_in_s"])
        v["video.send.fps"] = _num(vtx["fps"], integer=True)
        v["video.send.message_cap"] = _num(vtx["message_cap"], integer=True)
        v["video.send.keyframe_repeat_max"] = _num(vtx["keyframe_repeat_max"], integer=True)
        v["video.send.size"] = str(vtx["output"])
        v["video.send.x264_preset"] = str(vtx["preset"])

    if mode == "progressive_jpeg":
        v["mode.media"] = "still"
    elif mode == "video" and vtx is not None:
        v["mode.media"] = "video" if vtx["enabled"] else "video_logger"
    v["mode.run"] = "per_boot"
    v["mode.output"] = "transmit"

    # ---- uplink: UART (two v1 readers), bm_serial (PyYAML), lane, media key
    uart = attempt("load_uart_config", bm_serial.load_uart_config, config_path)
    if uart is not None:
        port, baud = uart
        if (port, baud) != (cfg.uart_port, cfg.baudrate):
            p.append(f"uart: bm_serial reads {port}@{baud} but spotter_time_sync reads "
                     f"{cfg.uart_port}@{cfg.baudrate} (v1 readers disagree)")
        v["uplink.uart.port"] = port
        v["uplink.uart.baudrate"] = int(baud)

    bms = raw.get("bm_serial") if isinstance(raw.get("bm_serial"), dict) else {}
    if "network_type" not in bms:
        p.append("bm_serial.network_type is absent, so v1 resolved 0x01 (Iridium fallback): "
                 "a human must confirm the network type (REVIEW X3)")
    nt = attempt("network_type", bm_serial.load_network_type_from_config, config_path)
    if nt is not None:
        v["uplink.network_type"] = int.from_bytes(nt, "little")
    pacing = attempt("resolve_pacing", rc.resolve_pacing, config_path)
    if pacing is not None:
        v["uplink.chunk_chars"] = int(pacing["chunk_b64_chars"])
        v["uplink.msg_interval_s"] = float(pacing["delay_seconds"])
        for k in ("image_buffer_size", "image_transmit_delay_seconds"):
            if k in bms and (isinstance(bms[k], bool) or not isinstance(bms[k], (int, float))):
                p.append(f"bm_serial.{k}={bms[k]!r} is not a number (v1 falls back to the "
                         "default silently)")

    lane = attempt("load_transmit_phase_config", rc_transmit_phase.load_transmit_phase_config,
                   config_path)
    if lane is not None:
        v["uplink.lane.enabled"] = bool(lane["enabled"])
        v["uplink.lane.grid_s"] = _num(lane["grid_seconds"])
        v["uplink.lane.pre_guard_s"] = _num(lane["pre_boundary_guard_s"])
        v["uplink.lane.post_guard_s"] = _num(lane["post_boundary_guard_s"])
        v["uplink.lane.max_wait_s"] = _num(lane["max_wait_s"])

    mk = attempt("load_media_key_config", rc_media_key.load_media_key_config, config_path)
    if mk is not None:
        v["uplink.media_key.enabled"] = bool(mk["enabled"])
        v["uplink.media_key.retain_days"] = _num(mk["retain_days"])
    if _quiet(rc_media_key.warn_retired_media_gid, config_path)[0]:
        p.append("media_gid.enabled is true: media_gid was retired by wire rev 5; remove the "
                 "island before migrating (REVIEW R1)")

    # ---- commands (PyYAML, with its own quoted-string traps) ----------------
    bmc_raw = raw.get("bm_commands") if isinstance(raw.get("bm_commands"), dict) else {}
    for k in ("enabled", "defer_acks_during_transmit"):
        if k in bmc_raw and not isinstance(bmc_raw[k], bool):
            p.append(f"bm_commands.{k}={bmc_raw[k]!r} is not a YAML bool (v1 bool() makes a "
                     "quoted 'false' true)")
    bmc = attempt("load_bm_commands_config", command_daemon.load_bm_commands_config,
                  config_path)
    if bmc is not None:
        v["commands.enabled"] = bool(bmc["enabled"])
        v["commands.topic"] = bmc["topic"]
        v["commands.listen_tail_s"] = _num(bmc["post_transmit_listen_s"])
        if bmc["defer_acks_during_transmit"]:
            p.append("bm_commands.defer_acks_during_transmit is true: v2 has no key for it "
                     "until W2 (S3) makes it always on; a human must decide")
        v["commands.state_path"] = _state_path_v2(bmc["state_path"])

    # ---- network (hand parser; absent island = do nothing) -----------------
    net = attempt("load_network_config", network_config.load_network_config, config_path)
    if "network" not in raw or net is not None:
        net = net or {}
        v["network.default"] = net.get("default", "none")
        v["network.ap_fallback_s"] = int(net.get("ap_fallback_s", 90))
        v["network.ap_timeout_min"] = int(net.get("ap_timeout_min", 60))

    # ---- every value must be a valid registry value --------------------------
    for key in R.KEYS:
        if key.path not in v:
            if not any(key.path in s for s in p):
                p.append(f"{key.path}: no value read")
            continue
        why = R.check_value(key, v[key.path])
        if why:
            p.append(f"{key.path}={v[key.path]!r} {why}")
    return out


def _state_path_v2(v1_path):
    """v2 keeps its command state in a NEW file beside the v1 one (K7)."""
    import command_state
    base = v1_path or command_state.DEFAULT_STATE_PATH
    root, ext = os.path.splitext(base)
    if root.endswith("_v2"):
        return base              # already the v2 file (reading a v2 render back)
    return f"{root}_v2{ext or '.json'}"


def _read_controls(ctl, v, p):
    """camera_controls dict (as rc_capture reads it) -> registry values.

    rc_capture silently ignores values it does not understand; the migrator
    refuses them instead (the END telemetry would still echo the raw string).
    """
    from rc_capture import _control_bool

    def group(name):
        g = ctl.get(name)
        if g is None:
            return {}
        if not isinstance(g, dict):
            p.append(f"{_CC}.{name} is not a mapping")
            return {}
        return g

    def opt_float(label, value):
        if value in (None, ""):
            return None
        try:
            return _num(value)
        except (TypeError, ValueError):
            p.append(f"{label}={value!r} is not a number (v1 ignores it)")
            return None

    def opt_enum(label, value, allowed):
        if value in (None, ""):
            return None
        text = str(value).strip().lower()
        if text not in allowed:
            p.append(f"{label}={value!r} is not one of {sorted(allowed)} (v1 ignores it)")
            return None
        return text

    def flag(label, value, default):
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text not in {"1", "true", "yes", "on", "enabled", "0", "false", "no", "off",
                        "disabled"}:
            p.append(f"{label}={value!r} is not a bool (v1 uses the default)")
        return _control_bool(value, default)

    v["camera.controls_enabled"] = flag(f"{_CC}.enabled", ctl.get("enabled"), False)

    f = group("focus")
    v["camera.focus.enabled"] = flag(f"{_CC}.focus.enabled", f.get("enabled"), True)
    v["camera.focus.mode"] = opt_enum(f"{_CC}.focus.mode", f.get("mode"),
                                      {"manual", "auto", "continuous"})
    v["camera.focus.lens_position"] = opt_float(f"{_CC}.focus.lens_position",
                                                f.get("lens_position"))
    v["camera.focus.range"] = opt_enum(f"{_CC}.focus.range", f.get("range"),
                                       {"normal", "macro", "full"})
    v["camera.focus.speed"] = opt_enum(f"{_CC}.focus.speed", f.get("speed"), {"normal", "fast"})

    w = group("white_balance")
    v["camera.white_balance.enabled"] = flag(f"{_CC}.white_balance.enabled", w.get("enabled"),
                                             False)
    v["camera.white_balance.mode"] = opt_enum(
        f"{_CC}.white_balance.mode", w.get("mode"),
        set(R.BY_PATH["camera.white_balance.mode"].enum))
    red = opt_float(f"{_CC}.white_balance.red_gain", w.get("red_gain"))
    blue = opt_float(f"{_CC}.white_balance.blue_gain", w.get("blue_gain"))
    if (red is None) != (blue is None):
        p.append(f"{_CC}.white_balance: only one of red_gain/blue_gain is set (v1 ignores both)")
    v["camera.white_balance.gains"] = None if red is None or blue is None else [red, blue]

    e = group("exposure")
    v["camera.exposure.enabled"] = flag(f"{_CC}.exposure.enabled", e.get("enabled"), False)
    mode = e.get("mode")
    if mode not in (None, "") and not isinstance(mode, str):
        p.append(f"{_CC}.exposure.mode={mode!r} is not a string")
    v["camera.exposure.mode"] = None if mode in (None, "") else str(mode)
    v["camera.exposure.ev"] = opt_float(f"{_CC}.exposure.ev", e.get("ev"))
    sh = opt_float(f"{_CC}.exposure.shutter_us", e.get("shutter_us"))
    v["camera.exposure.shutter_us"] = None if sh is None else int(sh)
    v["camera.exposure.analogue_gain"] = opt_float(f"{_CC}.exposure.analogue_gain",
                                                   e.get("analogue_gain"))

    ip = group("image_processing")
    v["camera.image_processing.enabled"] = flag(f"{_CC}.image_processing.enabled",
                                                ip.get("enabled"), False)
    for k in ("sharpness", "contrast", "saturation", "brightness"):
        v[f"camera.image_processing.{k}"] = opt_float(f"{_CC}.image_processing.{k}", ip.get(k))
    dn = ip.get("denoise")
    v["camera.image_processing.denoise"] = None if dn in (None, "") else str(dn)
    hdr = ip.get("hdr")
    v["camera.image_processing.hdr"] = None if hdr in (None, "") else hdr
