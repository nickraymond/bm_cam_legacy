#!/usr/bin/env python3
"""S3c bench: set dotted keys in bmcam003's camera_config.yaml (YAML round trip; the
pre-bench file is in /home/pi/s3cbench/backup/camera_config.yaml, restore = cp), then
verify through the runtime's own loader and resolvers. Run ON the Pi:
  python3 set_s3c.py mode.run=stay_on mode.output=save_local mode.interval_s=60 \
      mode.media=video video.storage.max_used_pct=41.3 video.storage.min_free_gb=1.0
Values are parsed as YAML scalars (60 -> int, 41.3 -> float, true -> bool).
Exit 1 if the file does not load at level v2 (the runtime would fall back)."""
import sys
import yaml
sys.path.insert(0, "/home/pi/BM_Devel_Pi")
A = "/home/pi/BM_Devel_Pi"
p = f"{A}/camera_config.yaml"
doc = yaml.safe_load(open(p))
for arg in sys.argv[1:]:
    dotted, raw = arg.split("=", 1)
    node = doc
    *parents, leaf = dotted.split(".")
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = yaml.safe_load(raw)
    print(f"[set] {dotted} = {node[leaf]!r}")
with open(p, "w") as fh:
    fh.write("# s3cbench: edited by runs/s3c_bench_20260927/set_s3c.py; original in "
             "/home/pi/s3cbench/backup/\n")
    yaml.safe_dump(doc, fh, sort_keys=False)

import config_v2, rc_progressive_jpeg as rc
b = config_v2.load_for_boot(f"{A}/camera_schedule.yaml", p, f"{A}/camera_config.lkg.json")
v = b.values or {}
runtime = rc.resolve_runtime(None, b)[0]
print("[set] level", b.level, "hash", config_v2.config_hash(v) if v else None)
print("[set] runtime", runtime, "run", rc.resolve_run_mode(b, runtime),
      "output", rc.resolve_output(b, runtime), "media", v.get("mode.media"),
      "storage", v.get("video.storage.max_used_pct"), v.get("video.storage.min_free_gb"),
      "halt", v.get("power.halt.enabled"), "dry_run", v.get("power.halt.dry_run"))
if b.level != "v2":
    print("\n".join(b.lines))
    sys.exit(1)
