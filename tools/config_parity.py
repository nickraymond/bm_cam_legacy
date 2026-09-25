#!/usr/bin/env python3
# filename: config_parity.py
# description: Sprint26 S2f — compare two config resolutions (print-config text, or --print-config --json) for deploy parity.
"""
The comparison deploy runs before it installs anything (DESIGN_supervisor.md
§8.3 S2f; PLAN_S2.md), and the one tests/test_config_v2_parity.py uses.

  text OLD NEW   two `--print-config` text outputs (old runtime vs new runtime on
                 the same unit). Identical except `[BOOT]` lines (uptime), the
                 `[CFG]` lines only an S2+ runtime prints, and the by-design
                 path / provenance differences (text_lines).
  json V1 V2     two `--print-config --json` outputs: the unit's v1 file vs its
                 migrated v2 file (rendered). Equal per key after normalising only
                 what differs by design (normalise() below).

Inputs:  two files (the LAST line of a --json file is the JSON).
Outputs: "PARITY OK" or every differing key/line; exit 0 OK, 1 differs, 2 usage.

Example (on a unit, from the staged runtime):
  python3 tools/config_parity.py json /tmp/v1.json /tmp/v2.json
"""

import contextlib
import difflib
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def controls_effect(controls, app=None):
    """What rc_capture BUILDS from a camera_controls dict: the rpicam flags plus
    the requested-controls telemetry. A group v1 left out and the render's
    {enabled: false} build the same thing (video_recorder.py uses the same
    builder), so camera controls are compared by effect, not by raw dict."""
    if not isinstance(controls, dict) or "error" in controls:
        return controls
    app = app or os.path.join(REPO, "BM_Devel_Pi")
    if app not in sys.path:
        sys.path.insert(0, app)
    import rc_capture
    with contextlib.redirect_stdout(io.StringIO()):
        args, requested = rc_capture._camera_controls_from_settings({"camera_controls": controls})
    return {"args": args, "requested": requested}


def normalise(doc, app=None):
    """Drop only what differs BY DESIGN between a v1 run and a v2 (render) run:
      - paths: the render vs the v1 file; the v2 state file vs the v1 one
      - the loaders' `source` provenance ("defaults" -> "yaml": a migrated file
        states every block)
      - camera-controls dicts, compared by effect (controls_effect)
      - [CFG] / [BOOT] log lines; the json probe's env/wap/loader_output paths
    """
    doc = dict(doc)
    if "print_config" in doc:
        doc["print_config"] = [line for line in doc["print_config"]
                               if not line.startswith(("[CFG]", "[BOOT]"))]
    text = json.dumps(doc, sort_keys=True)
    text = re.sub(r'[^"\s=]*/camera_schedule\.yaml', "{CFG}", text)   # v1 file or the render
    text = text.replace("bm_command_state_v2.json", "bm_command_state.json")
    doc = json.loads(text)
    for block in ("video", "video_tx", "media_key"):
        if isinstance(doc.get(block), dict):
            doc[block].pop("source", None)
    for block in ("resolved", "overlaid"):
        b = doc.get(block)
        if isinstance(b, dict):
            if isinstance(b.get("media_key_cfg"), dict):
                b["media_key_cfg"].pop("source", None)
            if "camera_controls_override" in b:
                b["camera_controls_override"] = controls_effect(b["camera_controls_override"], app)
    if "camera_controls_island" in doc:
        doc["camera_controls_island"] = controls_effect(doc["camera_controls_island"], app)
    if "print_config" in doc:
        doc["print_config"] = [re.sub(r"\(source=(yaml|defaults)\)", "(source=*)", line)
                               for line in doc["print_config"]]
    for key in ("loader_output", "env", "wap_network"):
        doc.pop(key, None)
    return doc


def diff_docs(a, b):
    """-> [differing top-level keys] of two normalised docs."""
    return [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def text_lines(path):
    """print-config text minus [BOOT]/[CFG] lines, with the same by-design
    differences normalised as normalise(): config path (v1 file or render),
    v2 state file name, loader `source` provenance."""
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(("[BOOT]", "[CFG]")):
                continue
            line = re.sub(r'[^"\s=]*/camera_schedule\.yaml', "{CFG}", line.rstrip("\n"))
            line = line.replace("bm_command_state_v2.json", "bm_command_state.json")
            line = re.sub(r"source=(yaml|defaults)\b", "source=*", line)
            out.append(line)
    return out


def last_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        lines = [line for line in fh.read().splitlines() if line.strip()]
    return json.loads(lines[-1])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] not in ("text", "json"):
        print(__doc__, file=sys.stderr)
        return 2
    mode, a_path, b_path = argv
    if mode == "text":
        a, b = text_lines(a_path), text_lines(b_path)
        if a == b:
            print(f"[PARITY] print-config text OK ({len(a)} lines)")
            return 0
        print("[PARITY][DIFF] print-config text differs:")
        for line in difflib.unified_diff(a, b, a_path, b_path, n=1, lineterm=""):
            print(f"  {line}")
        return 1
    a, b = normalise(last_json(a_path)), normalise(last_json(b_path))
    keys = diff_docs(a, b)
    if not keys:
        print(f"[PARITY] config json OK ({len(a)} loader outputs equal)")
        return 0
    print(f"[PARITY][DIFF] config json differs in: {', '.join(keys)}")
    for k in keys:
        print(f"  {k}:\n    A: {json.dumps(a.get(k), sort_keys=True)[:600]}\n"
              f"    B: {json.dumps(b.get(k), sort_keys=True)[:600]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
