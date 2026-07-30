#!/usr/bin/env python3
# filename: repair_yaml_comments.py
# description: Repair value#comment gluing in a deployed camera_schedule.yaml, then PROVE it parses.
"""
One-shot repair for the 2026-07-29 incident: tools/patch_camera_schedule.py
(pre-fix) replaced values without preserving the space before an inline
comment, producing lines like

    enabled: true# SOAK ARMED 2026-07-26: halt at every cycle end

The glued scalar contains ": ", which makes the ENTIRE file invalid to
PyYAML. Every yaml.safe_load-based loader (bm_serial pacing, bm_commands,
camera_controls) then silently falls back to defaults, while the hand-rolled
line parsers (core schedule, transmit_phase, media_gid) keep working — so
the cycle looks healthy on the console while running the wrong config.

This script inserts the missing space on any `key: value#comment` line,
writes a timestamped backup first, and then does the ONLY check that
matters: yaml.safe_load of the result, printed loudly. Run ON the Pi.

Usage: python3 repair_yaml_comments.py [/home/pi/BM_Devel_Pi/camera_schedule.yaml]
Exit codes: 0 repaired (or already clean) AND parses; 1 still broken.
"""

import re
import shutil
import sys
import time

path = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/pi/BM_Devel_Pi/camera_schedule.yaml"

text = open(path, encoding="utf-8").read()
fixed_lines = []
repairs = []
for line in text.splitlines(keepends=True):
    # `key: value#comment` with no space before '#'. Quoted values in this
    # file never contain '#', so the first '#' after a non-space is always
    # a glued comment.
    new = re.sub(r"^(\s*[\w]+:\s*[^#\n]*\S)#", r"\1 #", line)
    if new != line:
        repairs.append((line.rstrip("\n"), new.rstrip("\n")))
    fixed_lines.append(new)

if repairs:
    backup = f"{path}.preglue_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    shutil.copy2(path, backup)
    open(path, "w", encoding="utf-8").write("".join(fixed_lines))
    print(f"[REPAIR] {len(repairs)} glued line(s) fixed (backup: {backup})")
    for old, new in repairs:
        print(f"[REPAIR]   {old.strip()}\n[REPAIR]   -> {new.strip()}")
else:
    print("[REPAIR] no glued lines found")

try:
    import yaml
    yaml.safe_load(open(path, encoding="utf-8").read())
    print("[REPAIR] yaml.safe_load: VALID — island loaders will work")
    sys.exit(0)
except Exception as exc:
    print(f"[REPAIR][FAIL] file still does not parse: {exc}")
    sys.exit(1)
