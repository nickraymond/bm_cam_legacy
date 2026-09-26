#!/usr/bin/env python3
"""Sprint26 S2 bench: from the DEPLOYED config, write one bench DIRECTORY per mode
(a config-v2 unit picks camera_config.yaml from beside its YAML):
  /home/pi/s2bench/cfg_<mode>/camera_schedule.yaml   v1, halt dry-run, state -> the copy
  /home/pi/s2bench/cfg_<mode>/camera_config.yaml     v2 (only if the unit has one), same edits
  + copies of the unit's command state (v1 and/or v2)
Args: bench dir names (default: video stills). A name starting with "stills" is
switched to media still / capture_mode progressive_jpeg; any other name keeps the
deployed mode (e.g. `cmd`, `f1`).
The deployed files and the live command state are never modified."""
import os, shutil, sys
B, LIVE = "/home/pi/s2bench", "/home/pi/BM_Devel_Pi"


def block_edit(lines, block, key_prefix, new):
    start = lines.index(block)
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i].startswith((" ", "#")):
            break
        if lines[i].startswith(key_prefix):
            lines[i] = new
            return
    raise SystemExit(f"{block} {key_prefix!r} not found")


def replace_one(lines, old_prefix, new):
    hits = [i for i, l in enumerate(lines) if l.startswith(old_prefix)]
    if len(hits) != 1:
        raise SystemExit(f"{old_prefix!r}: {len(hits)} matches")
    lines[hits[0]] = new


for mode in (sys.argv[1:] or ["video", "stills"]):
    d = f"{B}/cfg_{mode}"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    v1 = open(f"{LIVE}/camera_schedule.yaml").read().split("\n")
    block_edit(v1, "power_halt:", "  dry_run:", "  dry_run: true            # s2bench: never halt")
    block_edit(v1, "bm_commands:", "  state_path:", f'  state_path: "{d}/bm_command_state.json"')
    if mode.startswith("stills"):
        replace_one(v1, 'capture_mode: "video"', 'capture_mode: "progressive_jpeg"')
    open(f"{d}/camera_schedule.yaml", "w").write("\n".join(v1))
    shutil.copyfile(f"{LIVE}/bm_command_state.json", f"{d}/bm_command_state.json")
    if os.path.exists(f"{LIVE}/camera_config.yaml"):
        v2 = open(f"{LIVE}/camera_config.yaml").read().split("\n")
        replace_one(v2, "    dry_run: false", "    dry_run: true  # s2bench: never halt")
        replace_one(v2, '  state_path: "', f'  state_path: "{d}/bm_command_state_v2.json"  # s2bench')
        if mode.startswith("stills"):
            replace_one(v2, '  media: "video"', '  media: "still"  # s2bench')
        open(f"{d}/camera_config.yaml", "w").write("\n".join(v2))
        shutil.copyfile(f"{LIVE}/bm_command_state_v2.json", f"{d}/bm_command_state_v2.json")
    print(f"wrote {d}: {sorted(os.listdir(d))}")
