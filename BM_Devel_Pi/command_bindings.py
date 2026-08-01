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
      txd -> settings["pacing_delay_seconds"]  (v2)
      cap -> settings["message_cap"]           (v2)
      src -> settings["source_image_path"]     (v2, None = live camera)
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
    CAP_TABLE,
    EXP_TABLE,
    FOC_TABLE,
    HLT_TABLE,
    ROI_TABLE,
    SRC_TABLE,
    TMZ_TABLE,
    TRG_TABLE,
    TWN_TABLE,
    TXD_TABLE,
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
        # Sprint13: reef-test crops (roi 5/6) are NARROWER than the fixed
        # output width — clamp so they transmit at native crop size.
        # output_size_for_crop refuses to upsample (raises), so without
        # this a sub-1000px crop would kill the cycle at overlay time.
        out_w = min(int(s["output_width"]), crop[2])
        s["output_size"] = output_size_for_crop(crop[2], crop[3], out_w)

    if "win" in state.touched:
        index = state.settings["win"]
        minutes = WIN_TABLE[index]["minutes"]
        if int(s["max_run_time_min"]) != minutes:
            overrides.append(
                ("max_run_time_min", s["max_run_time_min"], minutes, f"win={index}")
            )
        s["max_run_time_min"] = minutes
        s["budget_seconds"] = minutes * 60

    if "txd" in state.touched:
        index = state.settings["txd"]
        seconds = TXD_TABLE[index]["seconds"]
        if float(s["pacing_delay_seconds"]) != float(seconds):
            overrides.append(
                ("pacing_delay_seconds", s["pacing_delay_seconds"], seconds,
                 f"txd={index}")
            )
        s["pacing_delay_seconds"] = seconds
        s["pacing_source"] = f"command txd={index}"

    if "cap" in state.touched:
        index = state.settings["cap"]
        messages = CAP_TABLE[index]["messages"]
        if int(s["message_cap"]) != messages:
            overrides.append(
                ("message_cap", s["message_cap"], messages, f"cap={index}")
            )
        s["message_cap"] = messages

    if "src" in state.touched:
        index = state.settings["src"]
        path = SRC_TABLE[index]["path"]
        old = s.get("source_image_path")
        if old != path:
            overrides.append(("source_image_path", old, path, f"src={index}"))
        s["source_image_path"] = path

    # Sprint12 hlt/twn: index 0 carries NO override payload, so the YAML
    # value governs even though the key is touched (D-S12-1 — the
    # delete-the-key-to-restore-stock doctrine, implemented table-driven).
    if "hlt" in state.touched:
        index = state.settings["hlt"]
        override = HLT_TABLE[index]["override"]
        if override is not None:
            old = (s.get("power_halt_enabled"), s.get("power_halt_dry_run"))
            new = (override["enabled"], override["dry_run"])
            if old != new:
                overrides.append(
                    ("power_halt (enabled, dry_run)", old, new, f"hlt={index}")
                )
            s["power_halt_enabled"] = override["enabled"]
            s["power_halt_dry_run"] = override["dry_run"]
            s["power_halt_source"] = f"command hlt={index}"

    if "twn" in state.touched:
        index = state.settings["twn"]
        override = TWN_TABLE[index]["override"]
        if override is not None:
            old = (s.get("window_start"), s.get("window_end"))
            new = (override["start"], override["end"])
            if old != new:
                overrides.append(("transmit_window", old, new, f"twn={index}"))
            s["window_start"] = override["start"]
            s["window_end"] = override["end"]
            s["transmit_window"] = f"{override['start']}-{override['end']}"
            s["window_source"] = f"command twn={index}"

    # Sprint13 tmz: same index-0-carries-no-payload doctrine as hlt/twn.
    # Overrides how the window HH:MM is interpreted; the clock source and
    # everything else in the time chain stay YAML-owned. The gate re-reads
    # YAML itself, so rc_command_hooks.gate_kwargs_for passes this through
    # as timezone_override (the D-S12-6 pattern).
    if "tmz" in state.touched:
        index = state.settings["tmz"]
        tz = TMZ_TABLE[index]["tz"]
        if tz is not None:
            old = s.get("timezone")
            if old != tz:
                overrides.append(("timezone", old, tz, f"tmz={index}"))
            s["timezone"] = tz
            s["timezone_source"] = f"command tmz={index}"

    # Recompute the derived transmit-only message budget AFTER txd/win, so
    # telemetry never reports a budget computed from the pre-override pacing.
    # This is the number that silently caps a slow-paced cycle: at txd=5.0 s
    # and win=16 min it is 192, below the 195 default cap.
    # Guarded on key presence so slim test fixtures aren't handed invented keys.
    if "pacing_delay_seconds" in s and "budget_seconds" in s:
        delay = float(s["pacing_delay_seconds"] or 0)
        s["budget_messages_if_transmit_only"] = (
            int(s["budget_seconds"] // delay) if delay > 0 else None
        )

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


def stranding_warnings(state):
    """Loud consequence lines for an active hlt override (Sprint12 SPEC).

    The command is allowed either way — that is the point of hlt — but the
    boot log must state the stranding trade unambiguously: a wrong halt
    mode is exactly what stranded bmcam001/002 on 2026-07-31.
    """
    if "hlt" not in state.touched:
        return []
    index = state.settings["hlt"]
    override = HLT_TABLE[index]["override"]
    if override is None:
        return []
    if override["enabled"] and not override["dry_run"]:
        return [f"[CMD][WARN] hlt={index}: REAL power halt commanded — a "
                "constant-power unit halts at cycle end and stays dark "
                "until a physical power cycle"]
    if not override["enabled"]:
        return [f"[CMD][WARN] hlt={index}: power halt DISABLED by command — "
                "a battery unit now drains ~0.6 W continuously until "
                "re-enabled"]
    return [f"[CMD] hlt={index}: dry-run halt commanded (halt is logged, "
            "not executed)"]


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
