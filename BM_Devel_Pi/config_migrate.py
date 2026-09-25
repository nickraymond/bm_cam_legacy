#!/usr/bin/env python3
# filename: config_migrate.py
# description: Sprint26 S2c — v1 camera_schedule.yaml + bm_command_state.json -> config v2 files (pure logic).
"""
The v1 -> v2 migration (DESIGN_supervisor.md §5 "Migration", PLAN_S2.md G1).

  migrate(v1_yaml, v1_state) -> Migration
      .config_text   camera_config.yaml text: EVERY registry key spelled out,
                     registry order, one help comment per key
      .state         bm_command_state_v2.json object (None when commands are off or
                     there is no v1 state to carry)
      .problems      stop conditions: a human must decide; nothing is written
      .report        dict for the diff report (per key: value, v1 source, notes)

  overlay_from_v8(v8, values) -> {v2 path: value}
      The §5 state mapping (roi -> still.crop, foc/awb/exp -> whole group with
      explicit nulls, win -> both budget keys, txd, cap, hlt 1-3, twn, tmz).
      Applied AT LOAD (S2d/S2e) to compute the effective config and hash; the
      file keeps the v8 section until S4 folds it (G1, Nick 2026-09-25).

State v2 shape in S2 (G1):
  {"schema": "bm_command_state_v2", "tables_version": 8, "boot_counter": 0,
   "overlay": {}, "guarded": {}, "result_cache": {}, "high_water": {},
   "v8": {settings, touched, applied_ids, pending_trigger, pending_heals},
   "migrated_from": {path, sha256}}
The v8 section is today's state body, validated by the v1 CommandState loader
itself; the v8 daemon keeps reading/writing it until S4. `pending_trigger`
stays v8-shaped (the v8 loader would drop a `kv`); S4's fold adds the empty kv.
High-water marks and the result cache start empty (REVIEW R2 condition).
`src != 0` is dropped and reported (a persistent reference-image mode is a test
hazard, §6.1).

Nothing here writes files; tools/config_migrate_v1_v2.py does (dry-run default).
"""

import hashlib
import json
import os

import config_registry as R
import config_v1_reader

STATE_SCHEMA_V2 = "bm_command_state_v2"
V8_TABLES_VERSION = 8


class Migration:
    def __init__(self):
        self.values = {}
        self.config_text = None
        self.state = None
        self.problems = []
        self.notes = []
        self.dropped = {}
        self.report = {}


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ---------------------------------------------------------------------------
# v8 command state -> overlay values (the §5 mapping)
# ---------------------------------------------------------------------------

def overlay_from_v8(v8):
    """Overlay {v2 path: value} that the v8 state applies (only touched keys;
    hlt/twn/tmz index 0 carries no payload, exactly as command_bindings)."""
    import command_tables as T

    settings = v8.get("settings") or {}
    touched = set(v8.get("touched") or [])
    ov = {}

    def idx(cmd):
        return settings.get(cmd, T.DEFAULT_SETTINGS[cmd])

    if "roi" in touched:
        ov["still.crop"] = list(T.ROI_TABLE[idx("roi")]["crop"])
    if "win" in touched:
        minutes = T.WIN_TABLE[idx("win")]["minutes"]
        ov["still.budget_min"] = minutes
        ov["video.send.budget_min"] = minutes      # v1 video_tx borrows the stills key
    if "txd" in touched:
        ov["uplink.msg_interval_s"] = float(T.TXD_TABLE[idx("txd")]["seconds"])
    if "cap" in touched:
        ov["still.message_cap"] = T.CAP_TABLE[idx("cap")]["messages"]
    if "hlt" in touched:
        o = T.HLT_TABLE[idx("hlt")]["override"]
        if o is not None:
            ov["power.halt.enabled"] = o["enabled"]
            ov["power.halt.dry_run"] = o["dry_run"]
    if "twn" in touched:
        o = T.TWN_TABLE[idx("twn")]["override"]
        if o is not None:
            ov["schedule.window.start"] = o["start"]
            ov["schedule.window.end"] = o["end"]
    if "tmz" in touched:
        tz = T.TMZ_TABLE[idx("tmz")]["tz"]
        if tz is not None:
            ov["schedule.timezone"] = tz

    # foc/awb/exp REPLACE the whole group and force the master switch on
    # (command_bindings.overlay_camera_controls): every key of the group is
    # set, the ones the table does not carry become explicit nulls.
    commanded = False
    if "foc" in touched:
        e = T.FOC_TABLE[idx("foc")]
        ov.update({"camera.focus.enabled": True, "camera.focus.mode": e["mode"],
                   "camera.focus.lens_position": e["lens_position"],
                   "camera.focus.range": None, "camera.focus.speed": None})
        commanded = True
    if "awb" in touched:
        e = T.AWB_TABLE[idx("awb")]
        ov.update({"camera.white_balance.enabled": True,
                   "camera.white_balance.mode": e["mode"],
                   "camera.white_balance.gains": (None if e["gains"] is None
                                                  else [float(g) for g in e["gains"]])})
        commanded = True
    if "exp" in touched:
        e = T.EXP_TABLE[idx("exp")]
        ov.update({"camera.exposure.enabled": True, "camera.exposure.mode": None,
                   "camera.exposure.ev": e["ev"], "camera.exposure.shutter_us": None,
                   "camera.exposure.analogue_gain": None})
        commanded = True
    if commanded:
        ov["camera.controls_enabled"] = True
    return ov


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def migrate_state(v1_state_path, commands_enabled):
    """-> (state_v2 or None, notes, problems). Reads v1 through CommandState."""
    notes, problems = [], []
    if not commands_enabled:
        notes.append("commands are off: no v2 state file; the v1 state file (if any) is "
                     "left untouched as the archive")
        return None, notes, problems

    import contextlib
    import io

    import command_tables as T
    from command_state import CommandState, DEDUPE_KEEP

    raw = None
    if v1_state_path and os.path.exists(v1_state_path):
        try:
            with open(v1_state_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError) as exc:
            problems.append(f"v1 state {v1_state_path} unreadable: {exc}")
            return None, notes, problems
        if not isinstance(raw, dict):
            problems.append("v1 state is not a JSON object")
            return None, notes, problems
        tv = raw.get("tables_version")
        if tv != V8_TABLES_VERSION:
            problems.append(f"v1 state tables_version={tv!r}; only tables v8 migrate")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cs = CommandState(path=v1_state_path)       # the v1 loader, read-only
        if cs.load_info["error"] or cs.load_info["reset_keys"] or buf.getvalue().strip():
            problems.append("the v1 CommandState loader dropped or reset something: "
                            + " | ".join(buf.getvalue().strip().splitlines()))
    else:
        # Nothing to carry: create no v2 file, so the runtime starts from
        # factory defaults exactly as v1 did ("loaded from defaults") and
        # writes the v2 file on its first save.
        notes.append(f"no v1 state at {v1_state_path}: no v2 state created; the runtime "
                     "starts from factory defaults, as v1 did")
        return None, notes, problems

    settings = {cmd: cs.settings[cmd] for cmd in T.SETTINGS_COMMANDS}
    touched = set(cs.touched)
    if "src" in touched and settings["src"] != 0:
        notes.append(f"DROPPED src={settings['src']} (a persistent reference-image source "
                     "is a test hazard; use trg 3/4 one-shot)")
        settings["src"] = 0
        touched.discard("src")
    v8 = {
        "settings": settings,
        "touched": sorted(touched),
        "applied_ids": list(cs.applied_ids)[-DEDUPE_KEEP:],
        "pending_trigger": cs.pending_trigger,
        "pending_heals": list(cs.pending_heals),
    }
    state = {
        "schema": STATE_SCHEMA_V2,
        "tables_version": V8_TABLES_VERSION,
        "boot_counter": 0,
        "overlay": {},
        "guarded": {},
        "result_cache": {},
        "high_water": {},
        "v8": v8,
        "migrated_from": {"path": v1_state_path, "sha256": _sha256(v1_state_path)},
    }
    if touched:
        notes.append(f"v8 overlay carried: settings={settings} touched={sorted(touched)}")
    if cs.pending_heals:
        notes.append(f"{len(cs.pending_heals)} pending heal(s) carried")
    if cs.pending_trigger:
        notes.append(f"pending trigger carried: {cs.pending_trigger}")
    return state, notes, problems


# ---------------------------------------------------------------------------
# YAML text
# ---------------------------------------------------------------------------

def _scalar(key, value):
    """YAML text for one value. JSON scalars are valid YAML and unambiguous
    (strings always quoted, so "08:00" or "on" can never change type)."""
    if key.type == R.NETWORK_TYPE and value in (1, 2):
        return f"0x{value:02x}"
    return json.dumps(value)


def render_config_text(values, header_lines=()):
    """camera_config.yaml text: `schema: 2`, then every registry key in
    registry order, nested by group, with the key's help as a comment."""
    lines = [f"# {h}" if h else "#" for h in header_lines]
    lines.append(f"schema: {R.SCHEMA_VERSION}")
    open_path = []
    for key in R.KEYS:
        parts = key.path.split(".")
        groups = parts[:-1]
        common = 0
        while (common < len(open_path) and common < len(groups)
               and open_path[common] == groups[common]):
            common += 1
        for depth in range(common, len(groups)):
            if depth == 0:
                lines.append("")
            lines.append("  " * depth + groups[depth] + ":")
        open_path = groups
        text = f"{'  ' * len(groups)}{parts[-1]}: {_scalar(key, values[key.path])}"
        lines.append(f"{text}  # {key.help}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# the whole migration
# ---------------------------------------------------------------------------

def migrate(v1_yaml_path, v1_state_path=None, generated_by="config_migrate"):
    m = Migration()
    got = config_v1_reader.read_v1(v1_yaml_path)
    m.values, m.notes, m.dropped = dict(got.values), list(got.notes), dict(got.dropped)
    m.problems = list(got.problems)

    if v1_state_path is None and "commands.state_path" in m.values:
        # The v1 path the unit really uses (its own loader's answer).
        import command_daemon
        import command_state
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cfg = command_daemon.load_bm_commands_config(v1_yaml_path)
        v1_state_path = cfg["state_path"] or command_state.DEFAULT_STATE_PATH

    if not m.problems:
        state, notes, problems = migrate_state(v1_state_path, m.values["commands.enabled"])
        m.state = state
        m.notes += notes
        m.problems += problems
        if state is not None:
            for path, value in overlay_from_v8(state["v8"]).items():
                why = R.check_value(R.BY_PATH[path], value)
                if why:
                    m.problems.append(f"v8 overlay gives {path}={value!r}: {why}")

    m.report = {
        "source": {"yaml": os.path.abspath(v1_yaml_path),
                   "yaml_sha256": _sha256(v1_yaml_path) if os.path.exists(v1_yaml_path)
                   else None,
                   "state": v1_state_path},
        "refused": bool(m.problems),
        "problems": m.problems,
        "notes": m.notes,
        "dropped_v1_keys": m.dropped,
        "keys": {k.path: {"value": m.values.get(k.path), "v1_sources": list(k.v1_sources),
                          "default": k.default,
                          "differs_from_default": m.values.get(k.path) != k.default}
                 for k in R.KEYS},
        "v8_overlay": overlay_from_v8(m.state["v8"]) if m.state else {},
    }
    if not m.problems:
        m.config_text = render_config_text(m.values, header_lines=(
            f"camera_config.yaml — config v2 (schema {R.SCHEMA_VERSION}, registry "
            f"v{R.REGISTRY_VERSION}). Generated by {generated_by}",
            f"from {os.path.basename(v1_yaml_path)} sha256 {m.report['source']['yaml_sha256']}",
            "Every setting is spelled out. Remote changes live in the command state "
            "overlay, never here;",
            "this file changes only by deploy or migrate (DESIGN_supervisor.md §5).",
        ))
    return m


def report_text(m):
    """Human-readable diff report (the migrator prints this; dry-run default)."""
    r = m.report
    out = [f"# config v1 -> v2 migration report",
           f"source: {r['source']['yaml']} (sha256 {r['source']['yaml_sha256']})",
           f"state:  {r['source']['state']}",
           f"result: {'REFUSED — a human must decide' if r['refused'] else 'OK'}", ""]
    if m.problems:
        out.append("## Problems (nothing is written)")
        out += [f"- {p}" for p in m.problems] + [""]
    if m.notes:
        out.append("## Notes")
        out += [f"- {n}" for n in m.notes] + [""]
    if m.dropped:
        out.append("## v1 keys with no v2 key")
        out += [f"- {k}: {why}" for k, why in sorted(m.dropped.items())] + [""]
    out.append("## Keys that differ from the registry default")
    for path, info in r["keys"].items():
        if info["differs_from_default"]:
            out.append(f"- {path} = {json.dumps(info['value'])}  "
                       f"(default {json.dumps(info['default'])}; v1 {', '.join(info['v1_sources'])})")
    if r["v8_overlay"]:
        out += ["", "## Command overlay carried from v1 (applied at load, not written into "
                "the YAML)"]
        out += [f"- {p} = {json.dumps(v)}" for p, v in r["v8_overlay"].items()]
    return "\n".join(out) + "\n"
