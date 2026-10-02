#!/usr/bin/env python3
# filename: gen_config_catalog.py
# description: Sprint27 — export the camera config registry (+ wire limits, video geometry, UI tiers) to docs/bmcam_config_catalog.json for the backend and frontend.
"""
The ONE machine-readable copy of the camera's settings for everything off the
unit (Sprint27 SPEC §3.2). The backend vendors this file byte-for-byte and
validates remote config changes against it before anything is sent, so the
UI, the backend and the camera agree on types, ranges and enums.

Generated from the SAME modules the unit runs: config_registry (keys, types,
defaults, ranges, enums, guard classes, apply timing, presets, help),
command_wire (wire size/charset limits, error codes), video_geometry (sensor
modes, presets, encoder ceilings: the no-upscale arithmetic) and
config_validate (the video message-cap floor). A hand edit would drift:
tests/test_s27_config_catalog.py fails when the committed file differs from
this output.

Product tables that are NOT camera facts live here, labelled (SPEC §2.1):
  TIER      control / engineering / blocked per key (what the UI may write)
  UNITS     display unit per key (only where the help text states one)
  REQUIRES  the enable switches that gate a control (rc_capture.py:387-474)

Inputs:  none (reads BM_Devel_Pi modules).
Outputs: docs/bmcam_config_catalog.json (--write), or the JSON on stdout.
Example:
  .venv-dev/bin/python tools/gen_config_catalog.py --write
Known limitations: cross-key rules are described (backend_rules), not
executable; the backend ports them. `selftest` vectors cover per-key checks
only.
"""

import argparse
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))

import command_wire as W  # noqa: E402
import config_registry as R  # noqa: E402
import config_validate as V  # noqa: E402
import video_geometry as VG  # noqa: E402

OUT = os.path.join(REPO, "docs", "bmcam_config_catalog.json")
CATALOG_FORMAT = 1

# ---------------------------------------------------------------------------
# product tables (Sprint27 SPEC §2.1; Nick decides Q2/Q3)
# ---------------------------------------------------------------------------

# Writable by the backend (SPEC r2 §2.1, REVIEW_r1 row 2): an explicit ALLOWLIST.
# Anything not listed here is read-only or blocked; a new registry key starts
# blocked until someone adds it on purpose.
CONTROL_KEYS = (
    "mode.media", "mode.run", "mode.interval_s", "mode.heartbeat_s", "mode.output",
    "schedule.timezone", "schedule.window.enabled", "schedule.window.start", "schedule.window.end",
    "camera.native.jpeg_quality", "camera.controls_enabled",
    "camera.focus.enabled", "camera.focus.mode", "camera.focus.lens_position",
    "camera.focus.range", "camera.focus.speed",
    "camera.white_balance.enabled", "camera.white_balance.mode", "camera.white_balance.gains",
    "camera.exposure.enabled", "camera.exposure.ev", "camera.exposure.shutter_us",
    "camera.exposure.analogue_gain",
    # Sprint27 Nick Q2: writable with ranges / names measured on the unit (registry v6)
    "camera.image_processing.enabled", "camera.image_processing.sharpness",
    "camera.image_processing.contrast", "camera.image_processing.saturation",
    "camera.image_processing.brightness", "camera.image_processing.denoise",
    "camera.image_processing.hdr",
    "still.crop", "still.output_width", "still.quality_ladder", "still.message_cap",
    "still.budget_min",
    "video.record.framing", "video.record.crop", "video.record.output", "video.record.sensor_mode",
    "video.record.fps", "video.record.bitrate_mbps", "video.record.encoder.profile",
    "video.record.encoder.level", "video.record.encoder.intra",
    "video.send.duration_s", "video.send.lead_in_s", "video.send.fps", "video.send.message_cap",
    "video.send.keyframe_repeat_max", "video.send.size", "video.send.x264_preset",
    "video.send.budget_min",
)

# UI level (Nick 2026-10-01, via the config UI session): "basic" keys are shown up front, every
# other key is "advanced" (folded away). Display only: it never changes what is writable (tier).
# camera.controls_enabled is basic because no exposure / WB / focus value takes effect without it
# (rc_capture.py:387-474).
BASIC_KEYS = (
    "mode.media", "camera.controls_enabled",
    "camera.exposure.enabled", "camera.exposure.ev", "camera.exposure.shutter_us",
    "camera.exposure.analogue_gain",
    "camera.white_balance.enabled", "camera.white_balance.mode", "camera.white_balance.gains",
    "camera.focus.enabled", "camera.focus.mode", "camera.focus.lens_position",
    "still.crop", "still.output_width",
    "video.record.framing", "video.record.fps",
    "video.send.duration_s", "video.send.size", "video.send.fps",
)

# Shown (current value) but NOT writable in the MVP (REVIEW_r1 rows 2 / C2). The
# camera.image_processing.* keys moved to control in Sprint27 Q2 (measured ranges).
ENGINEERING_REASON = {
    "camera.exposure.mode": "reported only: builds no camera flag (W7)",
    "still.save.quality": "only used by save_local, which is not writable yet",
}

# Never written remotely, whatever the token (reason shown). Checked by prefix.
BLOCKED_REASON = {
    "time.": "clock source / fallback: a wrong value breaks the transmit window and timestamps",
    "power.": "halt and bus power: wrong values hard-cut the Pi (SD risk) or keep it awake",
    "uplink.": "the BM uplink: a wrong value can stop every message from the unit",
    "commands.": "the command channel itself: a wrong value can cut remote access",
    "network.": "WiFi at boot: remote units have no WiFi to fall back to",
    "storage.": "SD limits: a wrong value deletes recordings or fills the card",
    "video.ui.": "recorder web UI: opens a port (bench only)",
    "video.logger.": "continuous recorder only (video_logger is deploy-only)",
    "camera.backend": "capture backend: a wrong one fails every capture (picamera2 CMA limits)",
    "camera.native.width": "native frame size: must match the sensor (every crop rule uses it)",
    "camera.native.height": "native frame size: must match the sensor (every crop rule uses it)",
}

# Values (not keys) the backend refuses: guarded values need the cfm flow (Next
# sprint), video_logger is deploy-only on the unit (command_v9.py:477-480).
BLOCKED_VALUES = {
    "mode.media": (("video_logger", "the continuous recorder is set by deploy only"),),
    "mode.output": (("save_local", "guarded: needs the cfm flow (Next sprint)"),),
}

# WARNING thresholds only (Sprint27 F-G3-8, Nick 2026-10-02): the hard limit is the registry
# RANGE, the one source the unit, the backend and the UI all read (config_registry: message
# caps ..500, budgets ..30 min, WB gains ..8). Nothing here may refuse a value; a test pins
# every warn_above below its key's range max.
LIMITS = {
    "still.message_cap": {"warn_above": 300, "why": "cellular cost (Nick 2026-10-01)"},
    "video.send.message_cap": {"warn_above": 300, "why": "cellular cost (Nick 2026-10-01)"},
    "still.budget_min": {"warn_above": 18, "why": "battery: minutes awake (Nick 2026-10-01)"},
    "video.send.budget_min": {"warn_above": 18, "why": "battery: minutes awake (Nick 2026-10-01)"},
    "mode.interval_s": {"warn_below": 600, "why": "below 10 min a stay_on unit is near-continuous "
                                                  "on cellular"},
    "mode.heartbeat_s": {"warn_below": 600, "why": "below 10 min a stay_on unit is near-continuous "
                                                   "on cellular"},
    "video.send.duration_s": {"warn_not": 5.0, "why": "only 5 s clips are ladder-validated"},
    # the wire carries lists of at most W.MAX_LIST items (command_wire.py)
    "still.quality_ladder": {"max_items": W.MAX_LIST, "why": "a command carries lists of at most "
                                                            f"{W.MAX_LIST} items"},
}

UNITS = {
    "mode.interval_s": "s", "mode.heartbeat_s": "s",
    "camera.native.jpeg_quality": "JPEG q", "camera.focus.lens_position": "dioptres",
    "camera.exposure.ev": "EV", "camera.exposure.shutter_us": "us",
    "camera.image_processing.sharpness": "x (1 = normal)", "camera.image_processing.contrast": "x (1 = normal)",
    "camera.image_processing.saturation": "x (1 = normal)",
    "camera.image_processing.brightness": "offset (0 = normal)",
    "still.crop": "native px [x, y, w, h]", "still.output_width": "px",
    "still.quality_ladder": "JPEG q, best first", "still.save.quality": "JPEG q",
    "still.message_cap": "messages", "still.budget_min": "min",
    "video.record.crop": "native px [x, y, w, h]", "video.record.output": "px WxH",
    "video.record.fps": "fps", "video.record.bitrate_mbps": "Mbps",
    "video.send.duration_s": "s", "video.send.lead_in_s": "s", "video.send.fps": "fps",
    "video.send.message_cap": "messages", "video.send.size": "px WxH",
    "video.send.budget_min": "min", "video.logger.clip_minutes": "min",
    "video.logger.session_minutes": "min",
}

# rc_capture._camera_controls_from_settings builds a group's flags only when the
# master switch AND the group's switch are true (rc_capture.py:387-474).
_GATED = {"camera.focus.": "camera.focus.enabled",
          "camera.white_balance.": "camera.white_balance.enabled",
          "camera.exposure.": "camera.exposure.enabled",
          "camera.image_processing.": "camera.image_processing.enabled"}


def _requires(path):
    if path == "camera.controls_enabled":
        return []
    for prefix, switch in _GATED.items():
        if path.startswith(prefix):
            out = ["camera.controls_enabled"]
            if path != switch:
                out.append(switch)
            return out
    return []


def _tier(key):
    """-> (tier, reason). control = writable (allowlist); engineering = shown,
    read-only in the MVP; blocked = never. LOCKED / SERVICE / guarded keys first."""
    if key.guard == R.LOCKED:
        return "blocked", "locked: deploy only"
    if key.guard == R.SERVICE:
        return "blocked", "service key: needs Nick's signed command"
    if key.guard in (R.GUARDED_REVERT, R.GUARDED_STAGE) and key.guard_when is None:
        return "blocked", f"{key.guard}: needs the cfm flow (Next sprint)"
    for prefix, why in BLOCKED_REASON.items():
        if key.path == prefix or key.path.startswith(prefix):
            return "blocked", why
    for prefix, why in ENGINEERING_REASON.items():
        if key.path == prefix or key.path.startswith(prefix):
            return "engineering", why
    if key.path in CONTROL_KEYS:
        return "control", None
    return "blocked", "not on the Sprint27 control allowlist"


def _section(path):
    top = path.split(".", 1)[0]
    if top == "video":
        return "video"
    return top


def _blocked_values(key):
    out = [{"value": v, "why": why} for v, why in BLOCKED_VALUES.get(key.path, ())]
    if key.guard in (R.GUARDED_REVERT, R.GUARDED_STAGE) and key.guard_when is not None:
        have = {json.dumps(b["value"]) for b in out}
        for v in key.guard_when:
            if json.dumps(v) not in have:
                out.append({"value": v, "why": f"{key.guard}: needs the cfm flow (Next sprint)"})
    # "" cannot cross the wire (strings are 1..48 chars): only a reset restores it.
    if "" in key.enum:
        out.append({"value": "", "why": "cannot be sent (wire strings are 1..48 chars): use reset"})
    return out


def _choices(key):
    """Runtime-checked names for free-string keys (config_registry._runtime_choices)."""
    if key.path == "video.record.framing":
        return sorted(VG.PRESETS)
    if key.path == "video.record.sensor_mode":
        return sorted(VG.SENSOR_MODES)
    return None


def _key_doc(key):
    tier, why = _tier(key)
    d = {
        "path": key.path, "group": key.group, "section": _section(key.path),
        "type": key.type, "default": key.default, "nullable": key.nullable,
        "enum": list(key.enum), "range": list(key.range) if key.range else None,
        "choices": _choices(key), "unit": UNITS.get(key.path),
        "help": key.help, "presets": [[label, value] for label, value in key.presets],
        "apply": key.apply, "guard": key.guard,
        "guard_when": list(key.guard_when) if key.guard_when is not None else None,
        "media": key.validate_when, "one_shot": key.path in R.ONE_SHOT,
        "short": key.short, "wire_visible": key.wire_visible,
        "tier": tier, "tier_reason": why, "blocked_values": _blocked_values(key),
        "requires": _requires(key.path), "limits": LIMITS.get(key.path),
        "level": "basic" if key.path in BASIC_KEYS else "advanced",
    }
    return d


# ---------------------------------------------------------------------------
# per-key selftest vectors: the backend's checker must agree with check_value
# ---------------------------------------------------------------------------

_PROBES = (None, True, False, 0, 1, -1, 2.5, -8.5, 8.0, 100000, "", "x", "auto", "12:00",
           "25:00", "480x270", "481x271", "0x10", "UTC", "Not/AZone", "a#b",
           [1, 2, 3, 4], [0, 0, 4608, 2592], [1504, 846, 1600, 900], [-1, 0, 10, 10],
           [15, 13, 11, 9], [9, 11], [1.8, 1.6], [1.8, 0], [1, 2], "/abs/path",
           "2026-01-01T00:00:00+00:00")


def _vectors(key):
    probes = list(_PROBES) + [key.default] + list(key.enum)
    if key.range:
        lo, hi = key.range
        probes += [lo, hi]
        if isinstance(lo, int) and isinstance(hi, int):
            probes += [lo - 1, hi + 1]
        else:
            probes += [lo - 0.5, hi + 0.5]
    out, seen = [], set()
    for v in probes:
        tag = json.dumps(v)
        if tag in seen:
            continue
        seen.add(tag)
        if key.type == R.TZ and isinstance(v, str) and v not in ("UTC", "America/Los_Angeles"):
            continue          # zone validity depends on the host tzdata: not a portable vector
        why = R.check_value(key, v)
        out.append([v, why is None])
    return out


def _geometry_vectors():
    """Backend parity for the ported no-upscale arithmetic (REVIEW_r1 row 7, A2):
    [framing, crop, output, sensor_mode, fps] -> what config_validate._video_geometry
    (the unit's own rule) resolves: [output_w, output_h, sensor_mode, fps], or null
    = refused."""
    frames = [None] + sorted(VG.PRESETS)
    modes = [None] + sorted(VG.SENSOR_MODES)
    crops = [None, [0, 0, 4608, 2592], [1504, 846, 1600, 900], [1804, 1015, 1000, 562],
             [4000, 2000, 608, 592]]
    outputs = [None, "1920x1080", "1280x720", "1000x562", "1001x563", "1922x1080"]
    cases = []
    for framing in frames:
        for crop in crops:
            for output in outputs:
                if framing and crop and output:
                    continue               # explicit crop + output beat framing: covered by None
                for mode in modes:
                    cases.append([framing, crop, output, mode, 15])
    for framing in frames:
        for mode in modes:
            cases.append([framing, None, None, mode, 30])
    out = []
    for framing, crop, output, mode, fps in cases:
        values = {"video.record.framing": framing, "video.record.crop": crop,
                  "video.record.output": output, "video.record.sensor_mode": mode,
                  "video.record.fps": fps}
        geo, _why = V._video_geometry(values)
        res = ([geo["output_wh"][0], geo["output_wh"][1], geo["sensor_mode"], geo["fps"]]
               if geo else None)
        out.append([[framing, crop, output, mode, fps], res])
    return out


# F-G3-10 (Nick 2026-10-02): the backend confirms a command whose ack was lost when a later
# <WS>/START carries the EXPECTED post-change config hash. It needs (1) the unit's hash function
# (config_v2.canonical + sha256, pinned by `hash_selftest`) and (2) every registry key of the
# base config, which a full refresh fetches: `refresh_gets` = `get` name lists (<= MAX_LIST names,
# groups allowed) covering EVERY key, each answered in <= REFRESH_MAX_PARTS <CF> parts, sized
# with the unit's own build_cf on long-valued settings (cap: 3 parts per cellular get, e:big).
REFRESH_MAX_PARTS = 2
REFRESH_GROUPS = ("mode", "schedule", "time", "power", "camera.backend", "camera.native",
                  "camera.controls_enabled", "camera.focus", "camera.white_balance",
                  "camera.exposure", "camera.image_processing", "still", "video.record",
                  "video.send", "video.logger", "video.ui", "storage", "uplink.uart",
                  "uplink.network_type", "uplink.chunk_chars", "uplink.msg_interval_s",
                  "uplink.lane", "uplink.media_key", "commands", "network")


def _sizing_values():
    """Registry defaults with long values where a key is free text (worst case for a <CF>)."""
    v = R.defaults()
    v["mode.media"] = "video"
    v["schedule.timezone"] = "America/Argentina/ComodRivadavia"
    v["video.record.framing"] = "stills_roi_1000p"
    v["video.record.crop"] = [1504, 846, 1600, 900]
    v["video.record.output"] = "1000x562"
    v["video.record.sensor_mode"] = "4608x2592"
    v["camera.white_balance.gains"] = [1.8125, 1.6875]
    v["camera.image_processing.hdr"] = "single-exp"
    v["camera.image_processing.denoise"] = "cdn_fast"
    v["camera.image_processing.sharpness"] = 12.25
    v["camera.image_processing.contrast"] = 1.875          # widest text inside the F-G3-5 ranges
    v["camera.image_processing.saturation"] = 1.875
    v["camera.image_processing.brightness"] = -0.125
    return v


def _refresh_gets():
    v = _sizing_values()

    def expand(name):
        return [name] if name in R.BY_PATH else [k.path for k in R.keys_in(name)]

    def parts(names):
        keys = [p for n in names for p in expand(n)]
        return len(W.build_cf("0123abcd", [(k, v[k], "c1000000") for k in keys]))

    def size(names):
        keys = [p for n in names for p in expand(n)]
        return sum(len(t) for t in W.build_cf("0123abcd", [(k, v[k], "c1000000") for k in keys]))

    # first-fit decreasing: biggest groups first, each into the first get it still fits
    # a whole top-level group when it fits one get, else its sub-groups / keys (fewer names)
    groups = []
    for top in R.groups():
        subs = [g for g in REFRESH_GROUPS if g == top or g.startswith(top + ".")]
        groups += [top] if parts([top]) <= REFRESH_MAX_PARTS else subs
    gets = []
    for g in sorted(groups, key=lambda g: (-size([g]), groups.index(g))):
        assert parts([g]) <= REFRESH_MAX_PARTS, g
        for names in gets:
            if len(names) < W.MAX_LIST and parts(names + [g]) <= REFRESH_MAX_PARTS:
                names.append(g)
                break
        else:
            gets.append([g])
    covered = {p for names in gets for n in names for p in expand(n)}
    missing = [k.path for k in R.KEYS if k.path not in covered]
    assert not missing, f"refresh_gets misses {missing}"
    return gets


def _hash_version():
    import config_v2
    return config_v2.HASH_VERSION


def _hash_vectors():
    """[{path: value} for EVERY key, config_hash] from the unit's own function."""
    import config_v2
    out = []
    bases = []
    d = R.defaults()
    for media in ("still", "video"):
        b = dict(d)
        b["mode.media"] = media
        bases.append(b)
    bases.append(_sizing_values())
    b = dict(_sizing_values())
    b.update({"camera.exposure.ev": -1.0, "still.message_cap": 300, "mode.run": "stay_on",
              "camera.white_balance.gains": None, "uplink.msg_interval_s": 1.3,
              "video.record.encoder.profile": "high", "camera.image_processing.hdr": True})
    bases.append(b)
    for values in bases:
        out.append([{k.path: values.get(k.path) for k in R.KEYS}, config_v2.config_hash(values)])
    return out


def build():
    keys = [_key_doc(k) for k in R.KEYS]
    body = {
        "format": CATALOG_FORMAT,
        "generated_by": "tools/gen_config_catalog.py",
        "schema_version": R.SCHEMA_VERSION,
        "registry_version": R.REGISTRY_VERSION,
        "wire": {
            "unit_max_json_bytes": W.MAX_JSON_BYTES,
            "unit_max_console_line_bytes": W.MAX_CONSOLE_LINE_BYTES,
            "max_list": W.MAX_LIST, "max_kv": W.MAX_KV,
            "string_regex": W.RE_STR.pattern.rstrip("\\Z").rstrip("\\"),
            "remote_id_range": [r[1:3] for r in W.RANGES if r[0] == "remote"][0],
            "error_codes": W.ERROR_CODES,
        },
        "video_geometry": {
            "native_wh": [VG.NATIVE_W, VG.NATIVE_H],
            "max_encode_wh": [VG.MAX_ENCODE_W, VG.MAX_ENCODE_H],
            "fps30_block_above_pixels": VG.FPS30_BLOCK_ABOVE_PIXELS,
            "upscale_slack_px": VG.UPSCALE_SLACK_PX,
            "default_sensor_mode_order": list(VG.DEFAULT_SENSOR_MODE_ORDER),
            "migration_preset": VG.MIGRATION_PRESET,
            "sensor_modes": {name: {"mode_wh": list(s["mode_wh"]), "fov_xywh": list(s["fov_xywh"]),
                                    "max_fps": s["max_fps"]}
                             for name, s in VG.SENSOR_MODES.items()},
            "presets": {name: {"crop": list(p["crop"]), "mode": p["mode"],
                               "output": list(p["output"]), "max_fps": p["max_fps"]}
                        for name, p in VG.PRESETS.items()},
        },
        "rules": {
            "video_cap_floor": V.VIDEO_CAP_FLOOR,
            "interval_min_s": 60,
            "video_send_size_min_px": V.VIDEO_SEND_SIZE_MIN_PX,
            "video_geometry_keys": list(V.VIDEO_GEOMETRY_KEYS),
            # backend-only (REVIEW_r1 row 3): stay_on only on a unit whose bus is held on
            "stay_on_requires_reported_true": "power.bus_always_on",
        },
        "keys": keys,
        # registry v7 (Sprint27 F-G3-4): old key -> the key that owns its rpicam option now
        "retired": dict(R.RETIRED),
        "hash": {"version": _hash_version(), "selftest": _hash_vectors()},
        "refresh_gets": _refresh_gets(),
        "selftest": {k.path: _vectors(k) for k in R.KEYS},
        "geometry_selftest": _geometry_vectors(),
    }
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    body["sha256"] = hashlib.sha256(canon.encode("ascii")).hexdigest()
    return body


def render():
    """Pretty JSON for review, but every vector list on ONE line (the vectors
    are ~3/4 of the file; one item per line would make it unreadable)."""
    body = build()
    compact = {}
    pretty = dict(body)
    pretty["selftest"] = {}
    for path, vectors in body["selftest"].items():
        tag = f"@@ST:{path}@@"
        compact[tag] = json.dumps(vectors, separators=(",", ":"), ensure_ascii=True)
        pretty["selftest"][path] = tag
    pretty["hash"] = dict(body["hash"])
    tag = "@@HV@@"
    compact[tag] = json.dumps(body["hash"]["selftest"], separators=(",", ":"), ensure_ascii=True)
    pretty["hash"]["selftest"] = tag
    pretty["geometry_selftest"] = []
    for i, vec in enumerate(body["geometry_selftest"]):
        tag = f"@@GV:{i}@@"
        compact[tag] = json.dumps(vec, separators=(",", ":"), ensure_ascii=True)
        pretty["geometry_selftest"].append(tag)
    text = json.dumps(pretty, indent=1, sort_keys=True, ensure_ascii=True)
    for tag, value in compact.items():
        text = text.replace(json.dumps(tag), value, 1)
    return text + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help=f"write {os.path.relpath(OUT, REPO)}")
    args = ap.parse_args()
    text = render()
    if args.write:
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write(text)
        cat = json.loads(text)
        tiers = {}
        for k in cat["keys"]:
            tiers[k["tier"]] = tiers.get(k["tier"], 0) + 1
        print(f"[CAT] wrote {OUT}: {len(cat['keys'])} keys {tiers} sha256={cat['sha256'][:12]} "
              f"({len(text)} B)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
