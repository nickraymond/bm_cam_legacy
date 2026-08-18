#!/usr/bin/env python3
# filename: add_sprint17_video_keys.py
# description: Sprint17 — insert the video-only geometry + encoder keys into a unit's YAML.
"""
One-shot config migration for Sprint17 (D-S17-1/D-S17-4).

WHY THIS IS A SEPARATE TOOL: video_settings.patch_yaml deliberately REFUSES
keys that are not already in the file — the settings GUI edits configs, it
never authors them (bench finding, bmcam000 2026-08-18). So the new
`video.preset` / `video.encoder.*` keys have to be inserted once, by hand or
by this, before the GUI can drive them.

Idempotent: keys already present are updated in place, never duplicated.
Comments, blank lines and every other key keep their bytes.

INPUTS
  --config PATH     camera_schedule.yaml to edit (default the live runtime one)
  --preset NAME     value for video.preset
  --fps N           value for video.fps (optional)
  --bitrate N       value for video.bitrate_mbps (optional)
  --denoise MODE    value for video.encoder.denoise (optional)
  --dry-run         print the resulting island, write nothing

OUTPUTS
  the edited file, plus a timestamped .before_sprint17_* backup beside it

EXAMPLE
  python3 tools/add_sprint17_video_keys.py --preset wide_1080p --bitrate 9.3 --fps 15

ASSUMPTIONS
  - The file already HAS a `video:` island (every video unit does; the stills
    template gained one in Sprint16). Refuses loudly if not — this tool
    migrates a video island, it does not create a unit's config.
  - Validation runs after the write with the SAME loaders the runtime boots
    with; ANY failure restores the backup, so the file on disk is never left
    invalid (same doctrine as patch_yaml).

KNOWN LIMITATIONS
  - Only touches the video island. Stills keys are never read or written.
"""

import argparse
import os
import shutil
import sys
import time

DEFAULT_CONFIG = "/home/pi/BM_Devel_Pi/camera_schedule.yaml"

# Inserted verbatim when absent. Kept in one place so every unit that migrates
# ends up with the same commented block a fresh template ships.
ENCODER_BLOCK = """  # Sprint17 D-S17-4: encoder knobs. Empty string / 0 = leave the camera
  # default (the flag is not passed at all).
  encoder:
    profile: ""            # baseline|main|high
    level: ""              # 4|4.1|4.2
    intra: 0               # keyframe interval; 0 = camera default
    denoise: ""            # auto|off|cdn_off|cdn_fast|cdn_hq
    sharpness: 1.0         # 0..16, 1.0 = normal
"""

PRESET_COMMENT = """  # Sprint17 D-S17-1: VIDEO-ONLY geometry. The preset carries its own crop,
  # sensor mode and output size — video no longer inherits the stills crop.
"""


def find_island(lines, name="video"):
    """(start, end) line indices of a top-level `name:` island, end exclusive."""
    start = None
    for i, raw in enumerate(lines):
        code = raw.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip(" \t"))
        if start is None:
            if indent == 0 and code.strip() == f"{name}:":
                start = i
            continue
        if indent == 0:                       # next top-level key ends it
            return start, i
    return (start, len(lines)) if start is not None else (None, None)


def set_scalar(lines, start, end, key, value, indent=2):
    """Set `key: value` at `indent` inside the island; append if absent.
    Returns the (possibly grown) end index."""
    pad = " " * indent
    for i in range(start + 1, end):
        code = lines[i].split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        this_indent = len(code) - len(code.lstrip(" \t"))
        if this_indent != indent or ":" not in code:
            continue
        if code.strip().split(":", 1)[0].strip() == key:
            head, rest = lines[i].rstrip("\n").split(":", 1)
            comment = ""
            if "#" in rest:
                comment = "   # " + rest.split("#", 1)[1].strip()
            lines[i] = f"{head}: {value}{comment}\n"
            return end
    # Insert right after the island header so the geometry reads first.
    lines.insert(start + 1, f"{pad}{key}: {value}\n")
    return end + 1


def has_subsection(lines, start, end, name):
    for i in range(start + 1, end):
        code = lines[i].split("#", 1)[0].rstrip()
        if code.strip() == f"{name}:" and (len(code) - len(code.lstrip())) == 2:
            return i
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--preset", default=None)
    ap.add_argument("--fps", default=None)
    ap.add_argument("--bitrate", default=None)
    ap.add_argument("--denoise", default=None)
    ap.add_argument("--repo", default=None,
                    help="checkout to import the validators from")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.config):
        print(f"[S17CFG][ERROR] no such config: {args.config}")
        return 2

    with open(args.config) as f:
        lines = f.readlines()

    start, end = find_island(lines)
    if start is None:
        print(f"[S17CFG][ERROR] {args.config} has no top-level `video:` island. "
              "This tool migrates a video island; it does not create one.")
        return 2
    print(f"[S17CFG] video island at lines {start + 1}-{end}")

    if args.preset is not None:
        end = set_scalar(lines, start, end, "preset", f'"{args.preset}"')
        # Comment goes in AFTER the key, so it lands above the line it
        # explains (set_scalar inserts at start + 1 too).
        if not any("D-S17-1" in l for l in lines[start:end]):
            lines.insert(start + 1, PRESET_COMMENT)
            end += 1
        print(f"[S17CFG] video.preset = {args.preset}")
    for key, value in (("fps", args.fps), ("bitrate_mbps", args.bitrate)):
        if value is not None:
            end = set_scalar(lines, start, end, key, value)
            print(f"[S17CFG] video.{key} = {value}")

    if has_subsection(lines, start, end, "encoder") is None:
        # Put the encoder block at the end of the island, before the next
        # top-level key, so it reads after the geometry.
        insert_at = end
        while insert_at > start and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, ENCODER_BLOCK)
        end = insert_at + 1
        print("[S17CFG] inserted video.encoder block")
    if args.denoise is not None:
        start, end = find_island(lines)
        enc_at = has_subsection(lines, start, end, "encoder")
        set_scalar(lines, enc_at, end, "denoise", f'"{args.denoise}"', indent=4)
        print(f"[S17CFG] video.encoder.denoise = {args.denoise}")

    start, end = find_island(lines)
    if args.dry_run:
        print("".join(lines[start:end]))
        return 0

    backup = (f"{args.config}.before_sprint17_"
              f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    shutil.copy2(args.config, backup)
    with open(args.config, "w") as f:
        f.writelines(lines)
    print(f"[S17CFG] backup: {backup}")

    # Validate with the real loaders; restore on ANY failure so a unit can
    # never be left with a config it will refuse to boot.
    repo = args.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(repo, "BM_Devel_Pi"))
    try:
        import video_geometry  # noqa: F401
        import video_recorder
        vcfg = video_recorder.load_video_config(args.config)
    except Exception as exc:
        shutil.copy2(backup, args.config)
        print(f"[S17CFG][ERROR] config REFUSED, backup restored: {exc}")
        return 1

    for line in video_geometry.describe(vcfg["geometry"], vcfg["bitrate_mbps"]):
        print(line.replace("[VID]", "[S17CFG]"))
    print("[S17CFG] OK — applies at the unit's next restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
