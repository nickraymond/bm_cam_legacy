#!/usr/bin/env python3
# filename: command_trigger.py
# description: Sprint26 S4 c.2 — the one-shot trigger's effect on a cycle (window bypass, capture-only, reference source); shared by v8 and v9.
"""
The one-shot `trg` (D-S12-3/4/5): what a consumed trigger does to ONE cycle.
Moved verbatim from command_bindings (Sprint26 S4 c.2) so the supervisor's v9
path applies a trigger without loading the v8 settings bindings; the legacy
runtime imports it through command_bindings, byte-identical.

Inputs:  resolved settings, the consumed trigger {"id", "value"[, "kv"]}.
Outputs: (new settings, flags {skip_time_window, capture_only}, log lines).
Known limitations: trg 3/4 name the stored reference images of SRC_TABLE.
"""

from command_tables import SRC_TABLE, TRG_TABLE


def apply_trigger(settings, trigger):
    """Map a consumed one-shot trigger onto settings + run flags
    (D-S12-3/4/5). Pure: returns (new_settings, flags, log_lines).

    flags: skip_time_window is ALWAYS True (D-S12-4 — the trigger boot
    bypasses the window gate, once); capture_only True for trg 1.
    A reference trigger sets source_image_path for THIS boot only — the
    persisted `src` setting is untouched.
    """
    entry = TRG_TABLE[trigger["value"]]
    s = dict(settings)
    flags = {"skip_time_window": True,
             "capture_only": entry["action"] == "capture"}
    lines = [f"[CMD] one-shot trigger id={trigger['id']} "
             f"trg={trigger['value']} ({entry['label']}): window gate "
             "BYPASSED for this boot only"]
    if entry["src"] is not None:
        path = SRC_TABLE[entry["src"]]["path"]
        s["source_image_path"] = path
        lines.append(f"[CMD] trigger source: {path} (camera skipped this "
                     "boot; persisted src setting untouched)")
    if flags["capture_only"]:
        lines.append("[CMD] trigger action: capture only — native to SD, "
                     "no encode/transmit")
    s["trigger"] = dict(trigger)
    return s, flags, lines
