#!/usr/bin/env python3
"""From the DEPLOYED camera_schedule.yaml, write two bench copies for one side:
  cfg_<side>_video.yaml   as deployed, but power_halt.dry_run: true and a bench copy of the command state
  cfg_<side>_stills.yaml  the same with capture_mode: "progressive_jpeg"
The deployed YAML and the live command state are never modified."""
import shutil, sys
side = sys.argv[1]
B, LIVE = "/home/pi/s1bench", "/home/pi/BM_Devel_Pi"
src = open(f"{LIVE}/camera_schedule.yaml").read().split("\n")
def block_edit(lines, block, key_prefix, new):
    start = lines.index(block)
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i].startswith((" ", "#")):
            break
        if lines[i].startswith(key_prefix):
            lines[i] = new
            return
    raise SystemExit(f"{block} {key_prefix!r} not found")
for mode in ("video", "stills"):
    lines = list(src)
    block_edit(lines, "power_halt:", "  dry_run:", "  dry_run: true            # s1bench: never halt during the bench")
    state = f"{B}/state_{side}_{mode}.json"
    shutil.copyfile(f"{B}/backup/bm_command_state.json", state)
    block_edit(lines, "bm_commands:", "  state_path:", f"  state_path: {state}   # s1bench copy")
    if mode == "stills":
        i = lines.index('capture_mode: "video"')
        lines[i] = 'capture_mode: "progressive_jpeg"   # s1bench'
    open(f"{B}/cfg_{side}_{mode}.yaml", "w").write("\n".join(lines))
    print(f"wrote {B}/cfg_{side}_{mode}.yaml")
