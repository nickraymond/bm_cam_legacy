#!/usr/bin/env python3
# filename: command_help.py
# description: Sprint13 — render the customer-facing console `help` and `cfg` output.
"""
Sprint13 — on-console help + config readback rendering.

Two pure renderers, both returning a list of ASCII lines for the console
transport (T1). EVERYTHING customer-visible is generated from
command_tables.py (D9 doctrine: the help can never describe a value the
daemon can't apply) — this module contains layout, not facts.

  render_help(topic)            -> the full command reference
  render_cfg(settings, state, controls) -> post-style resolved-config dump

`settings` is the resolve_rc_settings() dict AFTER overlay_rc_settings()
(the same dict --print-config prints — cfg and --print-config can never
disagree). `controls` is the effective camera_controls island dict after
overlay_camera_controls() (None = all-auto). `state` is the CommandState
(sources come from state.touched).

Format is modeled on the Spotter's own console output (Nick 2026-08-01):
verbose headings, one value per line, boxed tables, plain ASCII, lines
kept under 72 chars (test-enforced) so nothing wraps on a standard
terminal. No I/O, no serial: transport is the daemon's job.

Example:
  >>> from command_help import render_help
  >>> print("\\n".join(render_help()))

Known limitations: layout is frozen against the 2026-07-26 Spotter help
capture; the Phase 0 bench pass re-checks it against a fresh capture and
the real console width before the formatting gate is ticked.
"""

from command_tables import (
    COMMAND_INFO,
    COMMANDS,
    HLT_TABLE,
    QUERY_COMMANDS,
    ROI_TABLE,
    SRC_TABLE,
    TABLES_VERSION,
    TMZ_TABLE,
    TRG_TABLE,
    TWN_TABLE,
    table_for,
)

# Widest customer-visible line (test-enforced). 72 keeps a margin under
# the classic 80-col terminal even if the transport prefixes a few chars.
MAX_LINE_CHARS = 72

DEFAULT_TOPIC = "bmcam/cmd"

# The copy-paste examples (SPEC: force-capture trg 2 especially). Ids are
# literal small ints the operator bumps; tests parse every generated line
# through parse_command so an example can never go stale against the
# tables. (description, cmd, value-or-None)
QUICK_ACTIONS = (
    ("Capture + send an image now:", "trg", 2),
    ("Send a test image (camera bypassed - checks the link):", "trg", 3),
    ("Open the transmit window all day:", "twn", 2),
    ("Is the camera alive?", "ping", None),
    ("Show current settings:", "cfg", None),
    ("Show this reference:", "help", None),
)
_QUICK_ACTION_FIRST_ID = 101

# cfg row grouping (box heading -> commands, in row order). `trg` renders
# as the pending-trigger row. Timezone rides in POWER & SCHEDULE.
CFG_GROUPS = (
    ("CAMERA", ("roi", "foc", "awb", "exp", "src")),
    ("TRANSMISSION", ("win", "txd", "cap", "twn")),
    ("POWER & SCHEDULE", ("hlt", "tmz", "trg")),
)

_BAR = "+" + "=" * (MAX_LINE_CHARS - 4) + "+"


def _boxed_title(*texts):
    lines = [_BAR]
    for text in texts:
        lines.append("|" + text.center(MAX_LINE_CHARS - 4) + "|")
    lines.append(_BAR)
    return lines


def example_line(cmd, value, command_id, topic=DEFAULT_TOPIC):
    """One copy-paste `bm pub` line. Compact JSON, no spaces (the console
    splits arguments on whitespace); ping/help/cfg omit "v" entirely."""
    if value is None:
        payload = '{"id":%d,"c":"%s"}' % (command_id, cmd)
    else:
        payload = '{"id":%d,"c":"%s","v":%d}' % (command_id, cmd, value)
    return f"bm pub {topic} {payload} 1 1"


def quick_action_lines(topic=DEFAULT_TOPIC):
    lines = []
    command_id = _QUICK_ACTION_FIRST_ID
    for description, cmd, value in QUICK_ACTIONS:
        lines.append(f"  {description}")
        lines.append(f"      {example_line(cmd, value, command_id, topic)}")
        command_id += 1
    return lines


def render_help(topic=DEFAULT_TOPIC):
    """The full customer-facing command reference, as a list of lines."""
    lines = []
    lines += _boxed_title("bmcam CAMERA - REMOTE COMMAND REFERENCE",
                          f"(tables v{TABLES_VERSION})")
    lines += [
        "",
        "HOW TO SEND A COMMAND",
        "---------------------",
        "Type at this console:",
        "",
        '    bm pub %s {"id":123,"c":"<cmd>","v":<index>} 1 1' % topic,
        "",
        '  * "id"  : any number you haven\'t used before - just count up',
        '  * "v"   : an INDEX from the tables below, never a raw value',
        '  * v=0   : always means "factory default (config file governs)"',
        "",
        "GOOD TO KNOW",
        "------------",
        "  * Settings take effect on the camera's NEXT wake cycle.",
        "  * The camera only listens while awake. No ack? Send it again -",
        "    duplicates are free and never double-applied.",
        '  * Success looks like:  {"id":123,"ok":1,"st":{...}}',
        "",
        "QUICK ACTIONS - copy, paste, bump the id",
        "----------------------------------------",
    ]
    lines += quick_action_lines(topic)
    lines.append("")
    lines += _boxed_title("COMMANDS")
    lines.append("")

    for cmd in COMMANDS:
        info = COMMAND_INFO[cmd]
        if cmd == "ping" or cmd in QUERY_COMMANDS:
            # Single-line commands, rendered together after the loop.
            continue
        lines.append(f"{cmd} - {info['title']}")
        for index, entry in sorted(table_for(cmd).items()):
            lines.append(f"    v={index}   {entry['label']}")
        for note in info["notes"]:
            lines.append(f"    {note}")
        lines.append("")

    for cmd in ("ping", "cfg", "help"):
        lines.append(f"{cmd:<4} - {COMMAND_INFO[cmd]['title']}")
    lines.append("")

    lines += [
        _BAR,
        "Config file on the camera: camera_schedule.yaml (via SSH).",
        "A command overrides the file for that one setting; sending v=0",
        "hands control back to the file. All-zeros = full factory reset.",
        _BAR,
    ]
    return lines


# ---------------------------------------------------------------------------
# cfg — resolved-config dump
# ---------------------------------------------------------------------------

def _label_for_crop(crop):
    for entry in ROI_TABLE.values():
        if tuple(entry["crop"]) == tuple(crop):
            return entry["label"]
    x, y, w, h = crop
    return f"{w}x{h} at ({x},{y})"


def _controls_value(controls, island_key, render_fn, auto_text):
    block = (controls or {}).get(island_key)
    if not isinstance(block, dict) or not block.get("enabled", True):
        return auto_text
    return render_fn(block)


def _focus_text(block):
    if block.get("mode") != "manual":
        return "autofocus"
    lens = block.get("lens_position")
    return f"manual (lens {lens:g})" if lens is not None else "manual"


def _awb_text(block):
    mode = block.get("mode", "auto")
    if mode == "custom":
        return (f"custom gains R{block.get('red_gain')} "
                f"B{block.get('blue_gain')}")
    return mode


def _exp_text(block):
    ev = block.get("ev")
    return f"{ev:+g} EV" if ev is not None else "auto"


def _halt_text(settings):
    enabled = settings.get("power_halt_enabled")
    dry_run = settings.get("power_halt_dry_run")
    if not enabled:
        return "halt OFF (developer mode)"
    if dry_run:
        return "halt DRY-RUN (logs only)"
    return "halt ON (power savings)"


def _window_text(settings):
    start = settings.get("window_start")
    end = settings.get("window_end")
    if start == end and start is not None:
        return "ALL DAY (24 h)"
    return f"{start} - {end}"


def _src_text(settings):
    path = settings.get("source_image_path")
    if path is None:
        return "live camera"
    for entry in SRC_TABLE.values():
        if entry["path"] == path:
            return entry["label"]
    return "reference image"


def _trigger_text(state):
    trigger = getattr(state, "pending_trigger", None)
    if not trigger:
        return "none"
    # Column-friendly form: drop the label's parenthetical.
    label = TRG_TABLE[trigger["value"]]["label"].split(" (")[0]
    return f"armed: {label}"


def _source_for(cmd, state):
    """Source column: 'command <cmd>=<idx>' only when the command is
    touched AND carries an override payload (hlt/twn/tmz index 0 hands
    control back to the file, D-S12-1)."""
    if cmd not in state.touched:
        return "config file"
    index = state.settings[cmd]
    payload_key = {"hlt": "override", "twn": "override", "tmz": "tz"}
    key = payload_key.get(cmd)
    if key is not None and table_for(cmd)[index][key] is None:
        return "config file"
    return f"command {cmd}={index}"


def _cfg_value(cmd, settings, state, controls):
    if cmd == "roi":
        return _label_for_crop(settings["crop_native_xywh"])
    if cmd == "foc":
        return _controls_value(controls, "focus", _focus_text, "autofocus")
    if cmd == "awb":
        return _controls_value(controls, "white_balance", _awb_text, "auto")
    if cmd == "exp":
        return _controls_value(controls, "exposure", _exp_text, "auto")
    if cmd == "src":
        return _src_text(settings)
    if cmd == "win":
        return f"{int(settings['max_run_time_min'])} min per wake"
    if cmd == "txd":
        return f"{float(settings['pacing_delay_seconds']):g} s per message"
    if cmd == "cap":
        return f"{int(settings['message_cap'])} per image"
    if cmd == "twn":
        return _window_text(settings)
    if cmd == "hlt":
        return _halt_text(settings)
    if cmd == "tmz":
        return settings.get("timezone", "unknown")
    raise KeyError(cmd)


# Column widths: name, value, source. Sized to the widest current content;
# the width test keeps the total under MAX_LINE_CHARS.
_COL_NAME, _COL_VALUE, _COL_SOURCE = 21, 27, 16
_ROW_BAR = (" +" + "-" * _COL_NAME + "+" + "-" * _COL_VALUE
            + "+" + "-" * _COL_SOURCE + "+")


def _clip(text, width):
    """Column safety net: alignment beats completeness in a boxed table
    (the full wording is always available in `help`)."""
    return text if len(text) <= width else text[: width - 2] + ".."


def _row(name, value, source):
    name = _clip(name, _COL_NAME)
    value = _clip(value, _COL_VALUE)
    source = _clip(source, _COL_SOURCE)
    return (f" |{name:<{_COL_NAME}}|{value:<{_COL_VALUE}}"
            f"|{source:<{_COL_SOURCE}}|")


def render_cfg(settings, state, controls=None):
    """post-style dump of the effective RESOLVED config (next cycle)."""
    lines = []
    lines += _boxed_title("bmcam CAMERA - CURRENT SETTINGS (next wake cycle)")
    lines.append("")
    for heading, cmds in CFG_GROUPS:
        lines.append(f" {heading}")
        lines.append(_ROW_BAR)
        lines.append(_row(" Setting", " Value", " Controlled by"))
        lines.append(_ROW_BAR)
        for cmd in cmds:
            info = COMMAND_INFO[cmd]
            if cmd == "trg":
                trigger = getattr(state, "pending_trigger", None)
                source = f" command trg={trigger['value']}" if trigger else " -"
                lines.append(_row(f" {info['cfg_name']}",
                                  f" {_trigger_text(state)}", source))
                continue
            name = f" {info['cfg_name']} ({cmd})"
            value = f" {_cfg_value(cmd, settings, state, controls)}"
            source = f" {_source_for(cmd, state)}"
            lines.append(_row(name, value, source))
        lines.append(_ROW_BAR)
        lines.append("")
    lines += [
        " To change a row: see its command in `help`.  To give a row back",
        " to the config file: send that command with v=0.",
        f"{'tables v' + str(TABLES_VERSION):>{MAX_LINE_CHARS - 2}}",
    ]
    return lines
