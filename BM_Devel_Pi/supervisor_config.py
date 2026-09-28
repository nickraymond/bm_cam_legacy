#!/usr/bin/env python3
# filename: supervisor_config.py
# description: Sprint26 S4 b.1 — the supervisor runs on base ⊕ overlay: validated effective config, rendered to the tmpfs v1 view, re-resolved at decision points.
"""
The effective config on the v9 path (PLAN_S4.md G1, G2, G3; DESIGN §5.1, §6.1).

Until S4 the supervisor ran on the YAML BASE plus the v8 command bindings;
the v2 overlay only fed a logged hash. On the supervisor path of a MIGRATED
unit (a bm_command_state_v2 state file; G1 — not the config fallback level, so
a unit that fell back to lkg / v1_migrated can still be repaired remotely) it
now runs on:

    effective = base ⊕ state_overlay(state)      (config_v2 + plan_fold, G2)

validated as a whole (config_validate, scope "effective"). The v1 loaders keep
reading the tmpfs render (PLAN_S2 G2), now rendered from the effective values,
so the overlay reaches every island reader (bm_serial pacing, the camera
controls island, the schedule gate, video_tx) through one file.

Inputs:  the BootConfig from config_v2.select_for_legacy_runtime, the render
         path the v1 loaders read (args.config_path after selection).
Outputs: Effective(values, overlay, dropped, base_errors, lines); the render
         rewritten in place; make_reresolve() for the decision points.

Rules (G3):
  - a failing rule drops only the OVERLAY-sourced keys among the paths it
    names (never the base; a base failure is logged only: the base never
    bricks a boot). power.halt.* is kept unless it is itself the rule's
    primary key (a dev-mode dry-run must survive an unrelated bad key).
  - the sent still width is derived: never wider than the crop (roi 5/6).
  - the LKG stays BASE (config_v2 saved it before this runs).
  - `<CF err>` for a drop is queued for the uplink by c.1; logged here.

Known limitations (b.1): the v8 daemon still dispatches; its writes land in
the state file's v8 section and reach the effective config through
plan_fold at the next re-resolve.
"""

import json
import os

import config_registry as R
import config_v2
import config_validate

# Protected unless they are the failing rule's primary key (G3).
PROTECTED_PREFIXES = ("power.halt.",)


class Effective:
    def __init__(self):
        self.values = {}
        self.overlay = {}
        self.dropped = []        # [(path, value, reason)] overlay keys not run this boot
        self.base_errors = []    # [reason] rules the base itself fails (logged only)
        self.lines = []
        self.hash = None


def state_path_for(values):
    return values.get("commands.state_path") or R.BY_PATH["commands.state_path"].default


def is_migrated(values):
    """G1: the v9 path needs a v2 state file (schema bm_command_state_v2), or
    none yet on a migrated unit (the path is the v2 name). A v1 state file =
    unmigrated = v8."""
    path = state_path_for(values)
    if not os.path.basename(path).endswith("_v2.json"):
        return False
    state = config_v2.read_state(path)
    return state is None or state.get("schema") == "bm_command_state_v2"


def _overlay_violations(violations, overlay):
    """-> (keys to drop, base-only reasons)."""
    drop, base = [], []
    for v in violations:
        named = [p for p in v.paths if p in overlay]
        if not named:
            base.append(f"{'/'.join(v.paths)}: {v.message}")
            continue
        for p in named:
            protected = p.startswith(PROTECTED_PREFIXES) and p != v.paths[0]
            if not protected and p not in drop:
                drop.append((p, v.message))
    return drop, base


def resolve(base, state, env=None):
    """base values + v2 state dict -> Effective (pure but for env, which the
    caller probes). Never raises on bad overlay content."""
    eff = Effective()
    overlay = {}
    try:
        overlay = config_v2.state_overlay(state)
    except Exception as exc:              # a bad state never discards the YAML
        eff.lines.append(f"[CFG][WARN] command state overlay unreadable "
                         f"({type(exc).__name__}: {exc}); running the YAML values")
    for path, value in list(overlay.items()):
        key = R.BY_PATH.get(path)
        why = "unknown key" if key is None else R.check_value(key, value)
        if why:
            eff.dropped.append((path, value, why))
            del overlay[path]
    for _ in range(len(overlay) + 1):
        values = dict(base)
        values.update(overlay)
        drop, base_errs = _overlay_violations(
            config_validate.validate(values, "effective", env=env), overlay)
        if not drop:
            break
        for path, why in drop:
            eff.dropped.append((path, overlay.pop(path), why))
    eff.values, eff.overlay = values, overlay
    eff.base_errors = base_errs
    eff.hash = config_v2.config_hash(values)
    for path, value, why in eff.dropped:
        eff.lines.append(f"[CFG][ERR] overlay {path}={value!r} not run this boot: {why} "
                         "(<CF err> in c.1)")
    for why in base_errs:
        eff.lines.append(f"[CFG][WARN] the YAML base fails an S4 rule (running it anyway): "
                         f"{why}")
    return eff


def render_values(values):
    """The v1 render of an effective config: the sent still width derived
    (G3) so the v1 loaders never see an upscale."""
    out = dict(values)
    out["still.output_width"] = config_validate.derived_output_width(values)
    return config_v2.render_v1_text(out)


def apply(boot, render_path, env=None, announce=print):
    """Boot: resolve the effective config and rewrite the render the v1
    loaders read. -> Effective, or None when this is not the v9 path (no v2
    values or an unmigrated state), which leaves everything as S3 had it."""
    base = getattr(boot, "values", None)
    if not base or render_path is None or not is_migrated(base):
        return None
    state = config_v2.read_state(state_path_for(base))
    eff = resolve(base, state, env=env)
    for line in eff.lines:
        announce(line)
    import atomic_io
    atomic_io.write_text(render_path, render_values(eff.values))
    announce(f"[CFG] supervisor runs the effective config: base ⊕ {len(eff.overlay)} "
             f"overlay key(s) hash={eff.hash} (render {render_path})")
    return eff


def make_reresolve(base, render_path, resolve_settings, carry=("video",), env=None,
                   after=None, announce=print):
    """The decision-point re-resolve on the v9 path: re-read the state file,
    rebuild the effective config, rewrite the render, resolve the v1 settings
    from it. `carry` keys that main() adds after resolution (the video block)
    are kept from the current settings; `after(settings)` applies boot-scoped
    forcing (the crash-loop dry-run). A failure raises (the supervisor's
    callers keep the last good settings, loudly)."""
    def reresolve(current):
        state = config_v2.read_state(state_path_for(base))
        eff = resolve(base, state, env=env)
        for line in eff.lines:
            announce(line)
        import atomic_io
        atomic_io.write_text(render_path, render_values(eff.values))
        fresh = resolve_settings(render_path)
        for key in carry:
            if key in current:
                fresh[key] = current[key]
        return after(fresh) if after else fresh
    return reresolve


def summary_json(eff):
    """One compact line for logs/tests."""
    return json.dumps({"hash": eff.hash, "overlay": sorted(eff.overlay),
                       "dropped": [p for p, _v, _w in eff.dropped]}, sort_keys=True)
