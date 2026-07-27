#!/usr/bin/env python3
# filename: command_tables.py
# description: Sprint10 — remote-command enums + value tables (single source of truth).
"""
Sprint10 command daemon — fixed value tables for the v1 remote command set.

This module is the SINGLE SOURCE OF TRUTH for what a remote command may
ask the camera to do (SPEC "Command message contract", DESIGN D3). Raw
values are never accepted on the wire: a command carries only a value
INDEX into these tables, so a garbled payload can at worst select a
different tested-valid preset, never an arbitrary setting.

Consumers:
  - command parser (inbound validation: is `v` a valid index for `c`?)
  - camera bindings (index -> concrete crop rect / rpicam flags / minutes)
  - operator GUI (dropdown options are GENERATED from these tables — D9)

Pure data + lookup helpers: no I/O, no serial, no camera imports.

Coordinate system: ROI crop rects are in NATIVE sensor-equivalent
4608x2592 coordinates (the same system as progressive_jpeg.crop in
camera_schedule.yaml). All ROI presets downsample to the same 1000 px
output width so the transmission budget is ~constant across zoom levels.

PLACEHOLDER VALUES (finalize before field deployment — SPEC/Q3):
  - roi rects for indices 1-4 (index 0 is the S07 byte-validated default)
  - foc manual lens positions (in-air dioptre guesses; water shifts focus)
  - awb index 3 custom-underwater gains
  - exp EV steps
`win` values are fixed by SPEC. Bump TABLES_VERSION on ANY table change.

Example:
  >>> from command_tables import valid_value, entry_for, command_options
  >>> valid_value("roi", 2)
  True
  >>> entry_for("win", 2)["minutes"]
  8
"""

# Bump on any table change; rides in GUI/debug surfaces so an operator can
# tell which table revision a device or GUI was built against.
TABLES_VERSION = 1

# Native sensor-equivalent frame (IMX708 full res) — ROI rects live here.
NATIVE_WIDTH = 4608
NATIVE_HEIGHT = 2592

# All ROI presets downsample to this output width (height follows aspect).
ROI_OUTPUT_WIDTH = 1000

# The six v1 commands (SPEC; Q4 closed). Anything else is rejected.
COMMANDS = ("roi", "foc", "awb", "exp", "win", "ping")

# Commands that carry persistent settings state (everything but ping).
SETTINGS_COMMANDS = ("roi", "foc", "awb", "exp", "win")

# Factory state: all-zero is the documented factory reset (SPEC).
DEFAULT_SETTINGS = {"roi": 0, "foc": 0, "awb": 0, "exp": 0, "win": 0}


def _centered(w, h):
    """Centered crop rect (x, y, w, h) in native coords."""
    return ((NATIVE_WIDTH - w) // 2, (NATIVE_HEIGHT - h) // 2, w, h)


# roi — concentric centered 16:9 zoom presets (Q3: zoom only, no pan).
# Index 0 is the current production default (S07 byte-validated center
# crop); 1 is widest (full frame); 4 is max detail (1000-wide floor so the
# output never upsamples). Rects for 1-4 are PLACEHOLDER until final field
# framing is chosen.
ROI_TABLE = {
    0: {"label": "default 1600x900", "crop": _centered(1600, 900)},
    1: {"label": "widest (full frame)", "crop": (0, 0, NATIVE_WIDTH, NATIVE_HEIGHT)},
    2: {"label": "wide 3072x1728", "crop": _centered(3072, 1728)},
    3: {"label": "mid 2304x1296", "crop": _centered(2304, 1296)},
    4: {"label": "max detail 1000x562", "crop": _centered(1000, 562)},
}

# foc — 0 = autofocus; 1..N = manual lens positions at fixed distances.
# lens_position is rpicam-still --lens-position (dioptres, 1/metres; 0 =
# infinity). PLACEHOLDER in-air values — underwater the effective focus
# distance shifts (n~1.33 behind a flat port); finalize with a tank test.
FOC_TABLE = {
    0: {"label": "autofocus", "mode": "auto", "lens_position": None},
    1: {"label": "manual infinity", "mode": "manual", "lens_position": 0.0},
    2: {"label": "manual 2 m", "mode": "manual", "lens_position": 0.5},
    3: {"label": "manual 1 m", "mode": "manual", "lens_position": 1.0},
    4: {"label": "manual 0.5 m", "mode": "manual", "lens_position": 2.0},
    5: {"label": "manual 0.25 m", "mode": "manual", "lens_position": 4.0},
}

# awb — rpicam-still --awb modes; index 3 is a fixed-gain underwater
# preset (--awb custom --awbgains R,B). Gains are PLACEHOLDER — tune
# against underwater reference-card captures before deployment.
AWB_TABLE = {
    0: {"label": "auto", "mode": "auto", "gains": None},
    1: {"label": "daylight", "mode": "daylight", "gains": None},
    2: {"label": "cloudy", "mode": "cloudy", "gains": None},
    3: {"label": "underwater preset", "mode": "custom", "gains": (1.8, 1.2)},
}

# exp — 0 = full auto exposure; 1..N = EV compensation steps
# (rpicam-still --ev, in stops). Step list is PLACEHOLDER.
EXP_TABLE = {
    0: {"label": "auto", "ev": None},
    1: {"label": "-2 EV", "ev": -2.0},
    2: {"label": "-1 EV", "ev": -1.0},
    3: {"label": "-0.5 EV", "ev": -0.5},
    4: {"label": "+0.5 EV", "ev": 0.5},
    5: {"label": "+1 EV", "ev": 1.0},
    6: {"label": "+2 EV", "ev": 2.0},
}

# win — active-window duration (progressive_jpeg.max_run_time_min).
# Values fixed by SPEC; index 0 is the current field-trial setting.
WIN_TABLE = {
    0: {"label": "16 min (default)", "minutes": 16},
    1: {"label": "12 min", "minutes": 12},
    2: {"label": "8 min", "minutes": 8},
    3: {"label": "5 min", "minutes": 5},
}

# ping carries no value; a single index keeps the lookup API uniform
# (parser treats a missing `v` as 0 for ping).
PING_TABLE = {
    0: {"label": "ping (liveness)"},
}

_TABLES = {
    "roi": ROI_TABLE,
    "foc": FOC_TABLE,
    "awb": AWB_TABLE,
    "exp": EXP_TABLE,
    "win": WIN_TABLE,
    "ping": PING_TABLE,
}


def is_command(cmd):
    """True if `cmd` is one of the six v1 command names."""
    return cmd in _TABLES


def table_for(cmd):
    """Full value table for a command. Raises KeyError on unknown command."""
    return _TABLES[cmd]


def valid_value(cmd, v):
    """True if `v` is a valid value index for `cmd` (exact int only —
    bool is rejected so JSON `true` can't alias index 1)."""
    if not is_command(cmd):
        return False
    if isinstance(v, bool) or not isinstance(v, int):
        return False
    return v in _TABLES[cmd]


def entry_for(cmd, v):
    """Table entry dict for (cmd, v). Raises KeyError if either is unknown."""
    return _TABLES[cmd][v]


def command_options(cmd):
    """(index, label) pairs for GUI dropdowns, sorted by index (D9: the
    GUI can never offer a value the daemon can't apply)."""
    return [(v, _TABLES[cmd][v]["label"]) for v in sorted(_TABLES[cmd])]
