#!/usr/bin/env python3
# filename: config_v2.py
# description: Sprint26 S2d — config v2 loader, boot fallback chain, and the v1-shaped render the legacy runtime reads.
"""
Config v2 loader (DESIGN_supervisor.md §5.1; PLAN_S2.md G1/G2).

  load_config(path, state=None, strict=True) -> Config
      .base       {path: value} from camera_config.yaml (defaults filled in)
      .overlay    {path: value} from the command state (v8 section via
                  config_migrate.overlay_from_v8, then the v2 overlay; S2: empty)
      .effective  base ⊕ overlay
      .source     {path: "yaml" | "default" | "cmd"}
      .hash       8 hex = sha256 of the canonical effective config (hv=1)
      .errors     [(path or None, message)]; strict raises ConfigError instead
  Strict (deploy / migrate / S4 set): unknown keys, bad types, out-of-range
  values and missing required keys are rejected with the key named.

  load_for_boot(v1_path, v2_path, lkg_path) -> BootConfig — NEVER raises:
      1. v2 file, judged on the ACTIVE mode's keys only
      2. the v1 file migrated in memory (config_v1_reader)
      3. last-known-good (full values of the last good v2 boot)
      4. safe-minimal: in S2 the legacy runtime can't listen without a media
         action, so this is "nothing to do" (exit 0, unit stays up, loud line);
         reachability on fallback arrives with the S3 supervisor
      One loud `[CFG][ERR]` line per fallback taken (the `<CF err>` uplink is S4).

  render_v1_text(values) -> str
      The effective BASE values as a v1-shaped camera_schedule.yaml that every
      v1 loader (PyYAML, section hand parsers, flat islands) reads exactly as
      the v1 file it was migrated from. Written to tmpfs each boot (G2); the
      source YAML is never rewritten. The v8 command overlay is NOT baked in:
      the v8 CommandState applies it at runtime from the v2 state file, exactly
      as it did from the v1 one (G1).

Assumptions: PyYAML present (a hard dependency, checked at deploy).
Known limitations (S2): the hash is logged, not sent (S4); the guarded-key
machinery is S4; only mode.run per_boot + mode.output transmit are runnable.
"""

import hashlib
import json
import os

import config_registry as R

V2_NAME = "camera_config.yaml"
STATE_V2_NAME = "bm_command_state_v2.json"
LKG_NAME = "camera_config.lkg.json"
RENDER_NAME = "camera_schedule.yaml"
HASH_VERSION = 1


class ConfigError(ValueError):
    pass


def format_errors(errors):
    return "; ".join(f"{p}: {m}" if p else m for p, m in errors)


class Config:
    def __init__(self):
        self.path = None
        self.base = {}
        self.overlay = {}
        self.effective = {}
        self.source = {}
        self.errors = []
        self.warnings = []       # non-fatal: an overlay value dropped (the YAML governs)
        self.hash = None


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def _read_yaml(path):
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        raise ConfigError(f"{path}: top level is not a mapping")
    return doc


def active_keys(media):
    """Keys that decide whether THIS boot can run: every key without a
    validate_when, plus the ones scoped to the active media (v1 is mode-scoped
    too: a broken video block never failed a stills boot)."""
    return {k.path for k in R.KEYS if k.validate_when in (None, media)}


def _cross_key_errors(values):
    """The v1 validate_schedule rules that span keys (pjpg + video modes).
    -> [(path, message)]; the path decides whether it matters for a boot."""
    errs = []
    crop, w, h = values.get("still.crop"), values.get("camera.native.width"), \
        values.get("camera.native.height")
    if isinstance(crop, list) and len(crop) == 4 and isinstance(w, int) and isinstance(h, int):
        x, y, cw, ch = crop
        if x + cw > w or y + ch > h:
            errs.append(("still.crop", f"{crop} does not fit the native frame {w}x{h}"))
        ow = values.get("still.output_width")
        if isinstance(ow, int) and ow > cw:
            errs.append(("still.output_width", f"{ow} is wider than still.crop w {cw} "
                         "(no upscale)"))
    for path in ("mode.run", "mode.output"):
        key = R.BY_PATH[path]
        if key.runnable and values.get(path) not in key.runnable:
            errs.append((path, f"{values.get(path)!r} is not runnable before S3 "
                               f"(runnable: {', '.join(key.runnable)})"))
    return errs


def parse_values(doc, strict):
    """Nested v2 doc -> ({path: value} with defaults, source, [(path, msg)])."""
    errors = []
    doc = dict(doc)
    schema = doc.pop("schema", None)
    if schema != R.SCHEMA_VERSION:
        errors.append((None, f"schema must be {R.SCHEMA_VERSION}, got {schema!r}"))
    flat = R.flatten(doc)
    values, source = R.defaults(), {p: "default" for p in R.BY_PATH}
    for path, value in flat.items():
        key = R.BY_PATH.get(path)
        if key is None:
            errors.append((None, f"unknown key {path!r}"))
            continue
        why = R.check_value(key, value)
        if why:
            errors.append((path, f"{value!r}: {why}"))
            continue
        values[path] = value
        source[path] = "yaml"
    for key in R.KEYS:
        if key.required and values.get(key.path) is None:
            errors.append((key.path, "is required"))
    errors += _cross_key_errors(values)
    if strict and errors:
        raise ConfigError(format_errors(errors))
    return values, source, errors


def state_overlay(state):
    """Overlay {path: value} a v2 state applies: the v8 section (G1) first,
    then the v2 overlay (empty until S4)."""
    if not isinstance(state, dict):
        return {}
    import config_migrate
    ov = {}
    if isinstance(state.get("v8"), dict):
        ov.update(config_migrate.overlay_from_v8(state["v8"]))
    if isinstance(state.get("overlay"), dict):
        ov.update(state["overlay"])
    return ov


def read_state(path):
    """v2 state dict, or None (missing / unreadable: the CommandState loader
    reports that itself when the runtime opens it)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def canonical(values):
    """Registry-typed, key-sorted JSON of every registry key (the hash input)."""
    out = {}
    for key in R.KEYS:
        v = values.get(key.path)
        if key.type == R.FLOAT and v is not None:
            v = float(v)
        elif key.type == R.GAINS and v is not None:
            v = [float(x) for x in v]
        out[key.path] = v
    return json.dumps(out, sort_keys=True, separators=(",", ":"))


def config_hash(values):
    return hashlib.sha256(canonical(values).encode("utf-8")).hexdigest()[:8]


def load_config(path, state=None, strict=True):
    cfg = Config()
    cfg.path = path
    try:
        doc = _read_yaml(path)
    except ConfigError:
        raise
    except Exception as exc:
        if strict:
            raise ConfigError(f"{path}: {type(exc).__name__}: {exc}")
        cfg.errors.append((None, f"{path}: {type(exc).__name__}: {exc}"))
        return cfg
    cfg.base, cfg.source, cfg.errors = parse_values(doc, strict)
    try:
        cfg.overlay = state_overlay(state)
    except Exception as exc:              # a bad state never discards the YAML
        cfg.warnings.append(f"command state overlay unreadable ({type(exc).__name__}: "
                            f"{exc}); running the YAML values")
        cfg.overlay = {}
    for path_, value in list(cfg.overlay.items()):
        key = R.BY_PATH.get(path_)
        why = "unknown key" if key is None else R.check_value(key, value)
        if why:
            cfg.warnings.append(f"overlay {path_}={value!r}: {why}; ignored")
            del cfg.overlay[path_]
    cfg.effective = dict(cfg.base)
    cfg.effective.update(cfg.overlay)
    for path_ in cfg.overlay:
        cfg.source[path_] = "cmd"
    cfg.hash = config_hash(cfg.effective)
    return cfg


# ---------------------------------------------------------------------------
# the v1-shaped render (G2)
# ---------------------------------------------------------------------------

def _q(value):
    """Quoted string for v1: every v1 parser strips one pair of double quotes;
    no value may carry '#', '"', '\\' or control characters (registry rule)."""
    text = str(value)
    bad = R._unwritable(text)
    if bad:
        raise ConfigError(f"value {text!r} cannot be written for the v1 parsers: {bad}")
    return f'"{text}"'


def float_text(value):
    """A float as YAML reads it back as a float: always a decimal point, never
    an exponent (PyYAML reads `1e-05` as a STRING)."""
    text = repr(float(value))
    if "e" in text or "E" in text or "inf" in text or "nan" in text:
        text = format(float(value), ".15f").rstrip("0")
        if text.endswith("."):
            text += "0"
    return text


def _n(value):
    """Bare number / bool for v1 (safe_load and every hand parser agree)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return float_text(value) if isinstance(value, float) else str(value)


def render_v1_text(values):
    """v1 camera_schedule.yaml text for the BASE values (see module doc)."""
    v = values
    media = v["mode.media"]
    budget = v["still.budget_min"] if media == "still" else v["video.send.budget_min"]
    L = ["# GENERATED at boot from camera_config.yaml (config v2) for the legacy runtime.",
         "# tmpfs only; never edit; never the source of truth (PLAN_S2.md G2).",
         f"time_source: {_q(v['time.source'])}",
         f"timezone: {_q(v['schedule.timezone'])}",
         "transmit_window:",
         f"  start: {_q(v['schedule.window.start'])}",
         f"  end: {_q(v['schedule.window.end'])}",
         f"enforce_time_window: {_n(v['schedule.window.enabled'])}",
         f"enforce_spotter_time_window: {_n(v['schedule.window.enabled'])}",
         f"set_system_clock_from_spotter: {_n(v['time.set_system_clock'])}",
         f"spotter_time_timeout_seconds: {_n(v['time.timeout_s'])}",
         f"allow_system_clock_fallback: {_n(v['time.allow_system_fallback'])}",
         f"uart_port: {_q(v['uplink.uart.port'])}",
         f"baudrate: {_n(v['uplink.uart.baudrate'])}",
         "rtc:",
         f"  hwclock_path: {_q(v['time.rtc.hwclock_path'])}",
         f"  set_system_clock_from_rtc: {_n(v['time.rtc.set_system_clock'])}",
         f"  require_plausible_after_utc: {_q(v['time.rtc.plausible_after_utc'])}",
         "image_pipeline:",
         f"  capture_backend: {_q(v['camera.backend'])}",
         "  source:",
         f"    width: {_n(v['camera.native.width'])}",
         f"    height: {_n(v['camera.native.height'])}",
         f"    jpeg_quality: {_n(v['camera.native.jpeg_quality'])}",
         "  camera_controls:",
         f"    enabled: {_n(v['camera.controls_enabled'])}"]
    groups = (("focus", ("enabled", "mode", "lens_position", "range", "speed")),
              ("white_balance", ("enabled", "mode")),
              ("exposure", ("enabled", "mode", "ev", "shutter_us", "analogue_gain")),
              ("image_processing", ("enabled", "sharpness", "contrast", "saturation",
                                    "brightness", "denoise", "hdr")))
    for group, keys in groups:
        L.append(f"    {group}:")
        for k in keys:
            val = v[f"camera.{group}.{k}"]
            if val is None:
                continue          # absent = no flag (v1)
            L.append(f"      {k}: {_q(val) if isinstance(val, str) else _n(val)}")
        if group == "white_balance" and v["camera.white_balance.gains"] is not None:
            red, blue = v["camera.white_balance.gains"]
            L += [f"      red_gain: {_n(float(red))}", f"      blue_gain: {_n(float(blue))}"]
    capture_mode = "progressive_jpeg" if media == "still" else "video"
    ladder = ",".join(str(q) for q in v["still.quality_ladder"])
    x, y, w, h = v["still.crop"]
    L += [f"capture_mode: {_q(capture_mode)}",
          "progressive_jpeg:",
          f"  max_run_time_min: {_n(budget)}",
          f"  message_cap: {_n(v['still.message_cap'])}",
          f"  output_width: {_n(v['still.output_width'])}",
          "  quality:",
          f"    ladder: {_q(ladder)}",
          "  crop:",
          f"    x: {x}", f"    y: {y}", f"    w: {w}", f"    h: {h}",
          "power_halt:",
          f"  enabled: {_n(v['power.halt.enabled'])}",
          f"  dry_run: {_n(v['power.halt.dry_run'])}",
          f"  mode: {_q(v['power.halt.mode'])}",
          f"  script_path: {_q(v['power.halt.script_path'])}",
          "bm_serial:",
          f"  network_type: 0x{v['uplink.network_type']:02x}",
          f"  image_buffer_size: {_n(v['uplink.chunk_chars'])}",
          f"  image_transmit_delay_seconds: {_n(v['uplink.msg_interval_s'])}",
          "bm_commands:",
          f"  enabled: {_n(v['commands.enabled'])}",
          f"  topic: {_q(v['commands.topic'])}",
          f"  post_transmit_listen_s: {_n(v['commands.listen_tail_s'])}",
          "  defer_acks_during_transmit: false",
          f"  state_path: {_q(v['commands.state_path'])}",
          "transmit_phase:",
          f"  enabled: {_n(v['uplink.lane.enabled'])}",
          f"  grid_seconds: {_n(v['uplink.lane.grid_s'])}",
          f"  post_boundary_guard_s: {_n(v['uplink.lane.post_guard_s'])}",
          f"  pre_boundary_guard_s: {_n(v['uplink.lane.pre_guard_s'])}",
          f"  max_wait_s: {_n(v['uplink.lane.max_wait_s'])}",
          "media_key:",
          f"  enabled: {_n(v['uplink.media_key.enabled'])}",
          f"  retain_days: {_n(v['uplink.media_key.retain_days'])}",
          "video:",
          f"  clip_minutes: {_n(v['video.logger.clip_minutes'])}",
          f"  fps: {_n(v['video.record.fps'])}",
          f"  bitrate_mbps: {_n(v['video.record.bitrate_mbps'])}",
          f"  session_minutes: {_n(v['video.logger.session_minutes'])}",
          f"  dir: {_q(v['video.record.dir'])}"]
    if v["video.record.framing"] is not None:
        L.append(f"  preset: {_q(v['video.record.framing'])}")
    if v["video.record.crop"] is not None:
        L.append(f"  crop_native_xywh: {_q(','.join(str(c) for c in v['video.record.crop']))}")
    if v["video.record.output"] is not None:
        L.append(f"  output: {_q(v['video.record.output'])}")
    if v["video.record.sensor_mode"] is not None:
        L.append(f"  sensor_mode: {_q(v['video.record.sensor_mode'])}")
    L += ["  storage:",
          f"    max_used_pct: {_n(v['video.storage.max_used_pct'])}",
          f"    min_free_gb: {_n(v['video.storage.min_free_gb'])}",
          f"    ring_dry_run: {_n(v['video.storage.ring_dry_run'])}",
          "  encoder:",
          f"    profile: {_q(v['video.record.encoder.profile'])}",
          f"    level: {_q(v['video.record.encoder.level'])}",
          f"    intra: {_n(v['video.record.encoder.intra'])}",
          f"    denoise: {_q(v['video.record.encoder.denoise'])}"]
    if v["video.record.encoder.sharpness"] is not None:
        L.append(f"    sharpness: {_n(v['video.record.encoder.sharpness'])}")
    L += ["  ui:",
          f"    enabled: {_n(v['video.ui.enabled'])}",
          f"    port: {_n(v['video.ui.port'])}",
          "video_tx:",
          f"  enabled: {_n(media == 'video')}",
          f"  duration_s: {_n(v['video.send.duration_s'])}",
          f"  lead_in_s: {_n(v['video.send.lead_in_s'])}",
          f"  fps: {_n(v['video.send.fps'])}",
          f"  message_cap: {_n(v['video.send.message_cap'])}",
          f"  keyframe_repeat_max: {_n(v['video.send.keyframe_repeat_max'])}",
          f"  output: {_q(v['video.send.size'])}",
          f"  preset: {_q(v['video.send.x264_preset'])}"]
    if v["network.default"] != "none":
        L += ["network:",
              f"  default: {_q(v['network.default'])}",
              f"  ap_fallback_s: {_n(v['network.ap_fallback_s'])}",
              f"  ap_timeout_min: {_n(v['network.ap_timeout_min'])}"]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# boot
# ---------------------------------------------------------------------------

class BootConfig:
    def __init__(self):
        self.level = None        # "v2" | "v1_migrated" | "lkg" | "safe_minimal"
        self.values = None       # BASE values to render (None for safe_minimal)
        self.config = None       # Config when level == "v2"
        self.lines = []          # loud log lines, in order


def _state_for(values, state_path=None):
    return read_state(state_path or values.get("commands.state_path") or "")


def load_for_boot(v1_path, v2_path, lkg_path, state_path=None):
    """The never-brick chain (module doc). Never raises."""
    b = BootConfig()
    media_err = None
    try:
        cfg = load_config(v2_path, strict=False)
        cfg_state = _state_for(cfg.base, state_path)
        cfg = load_config(v2_path, state=cfg_state, strict=False)
        media = cfg.base.get("mode.media")
        active = active_keys(media)
        # An error with no key (unreadable file, wrong schema, unknown key) or
        # on a key the active mode uses makes the file unusable for THIS boot;
        # an overlay value that failed was already dropped (the YAML governs).
        fatal = [(p, m) for p, m in cfg.errors if p is None or p in active]
        ignored = [(p, m) for p, m in cfg.errors if (p, m) not in fatal]
        if media is None and not any(p == "mode.media" for p, _m in fatal):
            fatal.append(("mode.media", "is required"))
        if not fatal:
            for p, m in ignored:
                b.lines.append(f"[CFG][WARN] {v2_path}: {p}: {m} (ignored for this boot)")
            for w in cfg.warnings:
                b.lines.append(f"[CFG][WARN] {w}")
            b.level, b.values, b.config = "v2", cfg.base, cfg
            b.lines.append(f"[CFG] config v2 {v2_path} media={media} hash={cfg.hash} "
                           f"hv={HASH_VERSION} registry=v{R.REGISTRY_VERSION} "
                           f"overlay={len(cfg.overlay)} key(s)")
            return b
        media_err = format_errors(fatal)
    except Exception as exc:             # never brick
        media_err = f"{type(exc).__name__}: {exc}"
    b.lines.append(f"[CFG][ERR] config v2 {v2_path} unusable: {media_err} — falling back")

    try:
        import contextlib
        import io
        import config_v1_reader
        with contextlib.redirect_stdout(io.StringIO()):
            got = config_v1_reader.read_v1(v1_path)
        if not got.problems:
            b.level, b.values = "v1_migrated", got.values
            b.lines.append(f"[CFG][ERR] running the v1 file {v1_path} migrated in memory")
            return b
        b.lines.append(f"[CFG][ERR] v1 file {v1_path} not usable: {'; '.join(got.problems)}")
    except Exception as exc:
        b.lines.append(f"[CFG][ERR] v1 file {v1_path} not usable: {type(exc).__name__}: {exc}")

    try:
        with open(lkg_path, "r", encoding="utf-8") as fh:
            lkg = json.load(fh)
        values, _source, errors = parse_values(R.nest(lkg["values"]) | {"schema": 2}, False)
        if not errors:
            b.level, b.values = "lkg", values
            b.lines.append(f"[CFG][ERR] running last-known-good {lkg_path} "
                           f"(hash {config_hash(values)}, saved {lkg.get('saved_utc')})")
            return b
        b.lines.append(f"[CFG][ERR] last-known-good {lkg_path} invalid: "
                       f"{format_errors(errors)}")
    except Exception as exc:
        b.lines.append(f"[CFG][ERR] no usable last-known-good {lkg_path}: "
                       f"{type(exc).__name__}: {exc}")

    b.level = "safe_minimal"
    b.lines.append("[CFG][ERR] SAFE-MINIMAL: no usable config; no media action this boot "
                   "(S2: the legacy runtime cannot listen without one)")
    return b


def save_lkg(lkg_path, values, when_utc):
    """Remember a v2 config that just loaded cleanly (full base values)."""
    import atomic_io
    atomic_io.write_json(lkg_path, {"saved_utc": when_utc, "hash": config_hash(values),
                                    "registry": R.REGISTRY_VERSION, "values": values})


def render_dir():
    """tmpfs for the render (G2): /dev/shm on the Pi; BMCAM_RENDER_DIR overrides."""
    base = os.environ.get("BMCAM_RENDER_DIR")
    if not base:
        base = "/dev/shm/bmcam" if os.path.isdir("/dev/shm") else os.path.join(
            __import__("tempfile").gettempdir(), "bmcam_render")
    os.makedirs(base, exist_ok=True)
    return base


def _render_resolves(render, media):
    """Raise if the v1 loaders the active mode uses reject the render."""
    import contextlib
    import io
    import rc_progressive_jpeg as rc
    with contextlib.redirect_stdout(io.StringIO()):
        rc.resolve_rc_settings(render)
        if media in ("video", "video_logger"):
            import rc_video_tx
            import video_recorder
            video_recorder.load_video_config(render)
            rc_video_tx.load_video_tx_config(render)


def select_for_legacy_runtime(config_path, fmt="auto", persist=True, announce=print):
    """Decide what the v1 loaders read this boot (PLAN_S2.md G2).

    -> (path, BootConfig or None). path None = safe-minimal: nothing to do.
    fmt: auto (v2 iff camera_config.yaml sits beside the v1 file) | v1 | v2.
    With v2 active: render the BASE values to tmpfs and point every reader
    that does not take --config-path at the render (bm_serial's env path,
    wap's hard-coded path). persist=False (inspection) skips the LKG write.
    """
    here = os.path.dirname(os.path.abspath(config_path))
    v2_path = os.path.join(here, V2_NAME)
    if fmt == "v1" or (fmt == "auto" and not os.path.exists(v2_path)):
        return config_path, None
    boot = load_for_boot(config_path, v2_path, os.path.join(here, LKG_NAME))
    for line in boot.lines:
        announce(line)
    if boot.level == "safe_minimal":
        return None, boot

    import atomic_io
    import bm_serial
    import rc_command_hooks

    # Render, then prove the v1 loaders accept it BEFORE pointing anything at
    # it (a value v2 accepts but a v1 loader rejects would otherwise fail the
    # boot after the v2 load). Any failure: the v1 file, as before S2.
    try:
        render = os.path.join(render_dir(), RENDER_NAME)
        atomic_io.write_text(render, render_v1_text(boot.values))
        _render_resolves(render, boot.values["mode.media"])
    except Exception as exc:
        announce(f"[CFG][ERR] v2 render unusable ({type(exc).__name__}: {exc}); "
                 f"running the v1 file {config_path}")
        return config_path, boot
    os.environ["BM_CAMERA_CONFIG_PATH"] = render
    bm_serial.BM_CAMERA_CONFIG_PATH = render
    rc_command_hooks.CONFIG_PATH = render
    announce(f"[CFG] legacy runtime reads the v1 render {render} (level={boot.level})")

    if persist and boot.level == "v2":
        lkg = os.path.join(here, LKG_NAME)
        try:
            with open(lkg, "r", encoding="utf-8") as fh:
                same = json.load(fh).get("hash") == config_hash(boot.values)
        except Exception:
            same = False
        if not same:             # write only on change: SD wear
            import datetime as _dt
            try:
                save_lkg(lkg, boot.values,
                         _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                announce(f"[CFG] last-known-good saved {lkg}")
            except OSError as exc:
                announce(f"[CFG][WARN] last-known-good not saved: {exc}")
    return render, boot
