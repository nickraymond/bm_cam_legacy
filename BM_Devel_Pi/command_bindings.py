#!/usr/bin/env python3
# filename: command_bindings.py
# description: Sprint10 §3 — map persisted command settings onto the RC capture path.
"""
Sprint10 §3 — camera bindings: command indices -> concrete RC settings.

The OVERLAY model (D13): camera_schedule.yaml is never rewritten.
At cycle start (and again after the pre-capture listen window) the RC
orchestrator calls these pure functions to override the YAML-resolved
values with whatever the field has commanded:

  overlay_rc_settings(settings, state)
      roi -> settings["crop_native_xywh"] + recomputed output_size
      win -> settings["max_run_time_min"] / ["budget_seconds"]
  overlay_camera_controls(yaml_controls, state)
      foc/awb/exp -> a camera_controls dict (the production island
      shape process_image_v2._camera_controls_from_settings consumes)

Only keys in state.touched override (see command_state.py): a unit
whose YAML island sets manual focus keeps it until `foc` is explicitly
commanded. Commanding index 0 ("auto"/default) IS an override — the
operator said auto, so auto wins over the YAML.

Q2 mapping (repo audit): roi = post-capture PIL crop in native coords
(no sensor reconfig); foc/awb/exp = rpicam-still CLI flags via the
camera_controls island (--autofocus-mode/--lens-position, --awb
[--awbgains], --ev); win = progressive_jpeg.max_run_time_min.

Pure functions, no I/O. Coordinate system: crop rects NATIVE 4608x2592.

Example:
  >>> settings, overrides = overlay_rc_settings(settings, state)
  >>> for line in describe_overrides(overrides): print(line)

Known limitations: a `win` change that arrives in this window's listen
phase intentionally takes effect NEXT cycle (the budget was charged at
process start); roi/foc/awb/exp apply to this window's capture.
"""

from command_tables import (
    AWB_TABLE,
    EXP_TABLE,
    FOC_TABLE,
    ROI_TABLE,
    WIN_TABLE,
)
from rc_jpeg_encoder import output_size_for_crop


def overlay_rc_settings(settings, state):
    """Overlay roi/win onto a resolve_rc_settings() dict.

    Returns (new_settings, overrides). overrides is a list of
    (key, old_value, new_value, source) tuples — empty when nothing is
    touched or the commanded value equals the YAML value. The input
    dict is not mutated.
    """
    s = dict(settings)
    overrides = []

    if "roi" in state.touched:
        index = state.settings["roi"]
        crop = ROI_TABLE[index]["crop"]
        old_crop = tuple(s["crop_native_xywh"])
        if old_crop != crop:
            overrides.append(("crop_native_xywh", old_crop, crop, f"roi={index}"))
        s["crop_native_xywh"] = crop
        s["output_size"] = output_size_for_crop(crop[2], crop[3], s["output_width"])

    if "win" in state.touched:
        index = state.settings["win"]
        minutes = WIN_TABLE[index]["minutes"]
        if int(s["max_run_time_min"]) != minutes:
            overrides.append(
                ("max_run_time_min", s["max_run_time_min"], minutes, f"win={index}")
            )
        s["max_run_time_min"] = minutes
        s["budget_seconds"] = minutes * 60

    s["command_overrides"] = [
        {"key": key, "from": list(old) if isinstance(old, tuple) else old,
         "to": list(new) if isinstance(new, tuple) else new, "source": source}
        for key, old, new, source in overrides
    ]
    return s, overrides


def overlay_camera_controls(yaml_controls, state):
    """Overlay foc/awb/exp onto the YAML camera_controls island dict.

    Returns a controls dict in the production island shape, or None when
    there is nothing to pass (no YAML island and nothing commanded).
    A commanded key REPLACES its whole YAML sub-block (commanded auto
    must clear a YAML manual override, not merge with it).
    """
    controls = dict(yaml_controls) if isinstance(yaml_controls, dict) else {}
    commanded = False

    if "foc" in state.touched:
        entry = FOC_TABLE[state.settings["foc"]]
        focus = {"enabled": True, "mode": entry["mode"]}
        if entry["lens_position"] is not None:
            focus["lens_position"] = entry["lens_position"]
        controls["focus"] = focus
        commanded = True

    if "awb" in state.touched:
        entry = AWB_TABLE[state.settings["awb"]]
        wb = {"enabled": True, "mode": entry["mode"]}
        if entry["gains"] is not None:
            wb["red_gain"], wb["blue_gain"] = entry["gains"]
        controls["white_balance"] = wb
        commanded = True

    if "exp" in state.touched:
        entry = EXP_TABLE[state.settings["exp"]]
        exposure = {"enabled": True}
        if entry["ev"] is not None:
            exposure["ev"] = entry["ev"]
        controls["exposure"] = exposure
        commanded = True

    if commanded:
        # The island's master switch must be on or the sub-blocks are
        # ignored; a commanded setting always turns it on.
        controls["enabled"] = True

    return controls if controls else None


def describe_overrides(overrides):
    """Human-readable override lines for the cycle log."""
    return [
        f"[CMD] override {key}: {old} -> {new} ({source})"
        for key, old, new, source in overrides
    ]
