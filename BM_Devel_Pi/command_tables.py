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
# v2 (2026-07-29, Nick-approved): added txd / cap / src for field control of
# transmit pacing, message cap, and reference-image injection.
# v3 (2026-07-31, Sprint12): added hlt / twn (remote power-halt and
# transmit-window overrides — the two settings that stranded bmcam001/002
# on 2026-07-31) and trg (one-shot capture/send trigger).
# v4 (2026-08-01, Sprint12 in-sprint fix): twn 2 became TRUE 24 h
# (00:00-00:00 full-circle, gate change D-S12-9) — the 00:01-23:59 v3
# value left 2 min of daily dead time. v3 existed only on the sprint
# branch / bench bmcam003.
TABLES_VERSION = 4

# Native sensor-equivalent frame (IMX708 full res) — ROI rects live here.
NATIVE_WIDTH = 4608
NATIVE_HEIGHT = 2592

# All ROI presets downsample to this output width (height follows aspect).
ROI_OUTPUT_WIDTH = 1000

# The v1 commands (SPEC; Q4 closed) + the v2 transport/debug trio + the
# v3 Sprint12 additions. Anything else is rejected.
COMMANDS = ("roi", "foc", "awb", "exp", "win", "txd", "cap", "src",
            "hlt", "twn", "trg", "ping")

# Commands that carry persistent settings state. NOTE the two exclusions:
# ping (stateless) and trg (a ONE-SHOT action — it arms a pending trigger
# in the state file's `pending_trigger` slot, not a persistent setting;
# see command_state.py).
SETTINGS_COMMANDS = ("roi", "foc", "awb", "exp", "win", "txd", "cap", "src",
                     "hlt", "twn")

# One-shot action commands: applied once on the next boot, then cleared.
ACTION_COMMANDS = ("trg",)

# Factory state: all-zero is the documented factory reset (SPEC). Index 0 of
# EVERY table must therefore be the current production default, so that a
# factory reset lands a unit on shipped behaviour and never on a test mode.
# For hlt/twn, index 0 means "no override — the YAML value governs", which
# IS the shipped behaviour.
DEFAULT_SETTINGS = {"roi": 0, "foc": 0, "awb": 0, "exp": 0, "win": 0,
                    "txd": 0, "cap": 0, "src": 0, "hlt": 0, "twn": 0}


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

# txd — transmit pacing, seconds per message
# (bm_serial.image_transmit_delay_seconds).
#
# NAMING (deliberate, Nick-approved 2026-07-29): this is NOT a "UART delay".
# Phase E showed the value's entire effect is on the SPOTTER'S CELLULAR
# QUEUE — a periodic ~9 s blackout on a 5-minute wall-clock grid, during
# which the 2-slot queue rejects every submit. Loss per blackout is
# max(0, 9 s / txd − 2), so zero loss needs txd >= ~4.5 s. The UART itself
# runs at 115200 and was never the bottleneck; calling this a UART setting
# sends a field operator to the wrong layer.
# Evidence: runs/sprint10_phaseE_20260728/RESULTS.md.
#
# Index 0 = 1.0 s = the shipped Sprint09 value, so factory reset is a no-op.
TXD_TABLE = {
    0: {"label": "1.0 s (Sprint09 default)", "seconds": 1.0},
    1: {"label": "1.5 s", "seconds": 1.5},
    2: {"label": "2.0 s", "seconds": 2.0},
    3: {"label": "3.0 s", "seconds": 3.0},
    4: {"label": "4.0 s", "seconds": 4.0},
    5: {"label": "5.0 s (Phase E zero-loss)", "seconds": 5.0},
}

# cap — hard ceiling on messages per image (progressive_jpeg.message_cap).
#
# NOTE the interaction, it surprises people: the EFFECTIVE cap is
#   min(cap, floor(max_run_time_min * 60 / txd))
# so the time budget can bind first. At txd=5.0 s with win=16 min the time
# budget allows only 192 messages, and `cap` above 192 has no effect at all.
# Raise `win` as well if you need a larger cap at slow pacing.
CAP_TABLE = {
    0: {"label": "195 (field default)", "messages": 195},
    1: {"label": "100", "messages": 100},
    2: {"label": "150", "messages": 150},
    3: {"label": "250", "messages": 250},
    4: {"label": "300", "messages": 300},
}

# src — image source. Index 0 captures from the camera as normal; 1..9 skip
# the camera and push a committed reference native through the SAME encode +
# transmit ladder. Field debug tool (Nick 2026-07-29): it separates "the
# camera is broken" from "the link is broken" without a site visit, and gives
# a byte-identical payload across units for A/B work.
#
# Every path below is a PREPARED 4608x2592 native. The raw
# reference_images/reference_reef_coral_*.jpg files are 4000x3000 and the
# pipeline REJECTS them ("expected native 4608x2592") — that is soak finding
# 009, which cost an entire overnight run. Paths are validated at pack time
# by tests/test_command_tables.py, not merely assumed to exist.
SRC_TABLE = {
    0: {"label": "live camera (default)", "path": None},
    1: {"label": "reef primary", "path": "reference_images/prepared/P7071008/synthetic_native_4608x2592.jpg"},
    2: {"label": "reef alt 01", "path": "reference_images/prepared/reference_reef_coral_alt_01/synthetic_native_4608x2592.jpg"},
    3: {"label": "reef alt 02", "path": "reference_images/prepared/reference_reef_coral_alt_02/synthetic_native_4608x2592.jpg"},
    4: {"label": "reef alt 03", "path": "reference_images/prepared/reference_reef_coral_alt_03/synthetic_native_4608x2592.jpg"},
    5: {"label": "reef alt 04", "path": "reference_images/prepared/reference_reef_coral_alt_04/synthetic_native_4608x2592.jpg"},
    6: {"label": "reef alt 05", "path": "reference_images/prepared/reference_reef_coral_alt_05/synthetic_native_4608x2592.jpg"},
    7: {"label": "reef alt 06", "path": "reference_images/prepared/reference_reef_coral_alt_06/synthetic_native_4608x2592.jpg"},
    8: {"label": "reef alt 07", "path": "reference_images/prepared/reference_reef_coral_alt_07/synthetic_native_4608x2592.jpg"},
    9: {"label": "reference card", "path": "reference_images/reference_card_native_imx708.jpg"},
}

# hlt — power-halt override (Sprint12). Persisted like every settings
# command but applied at NEXT boot only (Sprint11 D2 — the running cycle
# halts with whatever it booted with; ack-before-halt ordering is tested
# in test_rc_orchestrator). Index 0 carries NO override payload: the YAML
# power_halt block governs, which is the delete-the-key-to-restore-stock
# doctrine. mode/script_path stay YAML-owned — only enabled/dry_run are
# remotely commandable.
#
# STRANDING TRADE (deliberate, SPEC): hlt 1 on a constant-power unit halts
# at cycle end and stays dark until a physical power cycle; hlt 3 on a
# battery unit drains ~0.6 W continuously. The overlay logs both loudly.
HLT_TABLE = {
    0: {"label": "yaml default (no override)", "override": None},
    1: {"label": "halt enabled (real)",
        "override": {"enabled": True, "dry_run": False}},
    2: {"label": "halt dry-run (log only)",
        "override": {"enabled": True, "dry_run": True}},
    3: {"label": "halt disabled",
        "override": {"enabled": False, "dry_run": True}},
}

# twn — transmit-window override (Sprint12). Vetted presets, not arbitrary
# times (a finite table cannot fat-finger 25:00; arbitrary times are
# DESIGN O2, deferred). Times are HH:MM in the unit's OWN configured
# timezone — twn never changes timezone (one variable per command).
# Index 2 (wide) is the remote equivalent of --skip-time-window: the
# un-brick for "window misconfigured, unit never transmits".
TWN_TABLE = {
    0: {"label": "yaml default (no override)", "override": None},
    1: {"label": "field 10:00-15:00",
        "override": {"start": "10:00", "end": "15:00"}},
    2: {"label": "all day 24h (bench/diagnostic)",
        "override": {"start": "00:00", "end": "00:00"}},
    3: {"label": "morning 08:00-12:00",
        "override": {"start": "08:00", "end": "12:00"}},
    4: {"label": "midday 11:00-14:00",
        "override": {"start": "11:00", "end": "14:00"}},
}

# trg — one-shot capture/send trigger (Sprint12; Nick 2026-07-31). NOT a
# setting: arms `pending_trigger` in the state file; the next boot consumes
# it exactly once (cleared BEFORE the cycle runs, so a crash cannot re-fire
# it every boot). The trigger boot ALWAYS bypasses the transmit-window gate
# (Nick-confirmed) — in-window it adds nothing and out-of-window it would
# be a silent no-op otherwise. One-shot, so it cannot strand a unit the
# way a bad persistent window could.
#
# `src` values index into SRC_TABLE (single source of truth for reference
# paths — finding 009's dimension check covers these too). A reference
# trigger sends a known-good image with the camera skipped: verifies the
# whole encode+transmit+backend chain independent of optics/light (e.g.
# bench testing in a dim room).
TRG_TABLE = {
    0: {"label": "cancel pending trigger", "action": None, "src": None},
    1: {"label": "capture only (to SD, no transmit)",
        "action": "capture", "src": None},
    2: {"label": "capture + send", "action": "capture_transmit", "src": None},
    3: {"label": "send reef reference (camera skipped)",
        "action": "capture_transmit", "src": 1},
    4: {"label": "send reference card (camera skipped)",
        "action": "capture_transmit", "src": 9},
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
    "txd": TXD_TABLE,
    "cap": CAP_TABLE,
    "src": SRC_TABLE,
    "hlt": HLT_TABLE,
    "twn": TWN_TABLE,
    "trg": TRG_TABLE,
    "ping": PING_TABLE,
}


def is_command(cmd):
    """True if `cmd` is a known command name (any table revision)."""
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
