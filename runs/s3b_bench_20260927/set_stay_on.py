#!/usr/bin/env python3
"""S3b bench: set stay_on keys in bmcam003's camera_config.yaml by exact line edits
(comments kept), then verify through the runtime's own loader. Run ON the Pi:
  python3 set_stay_on.py run=stay_on interval=0 heartbeat=300 vcap=40 [media=still scap=25]
"""
import re, sys
sys.path.insert(0, "/home/pi/BM_Devel_Pi")
A = "/home/pi/BM_Devel_Pi"
args = dict(a.split("=", 1) for a in sys.argv[1:])
p = f"{A}/camera_config.yaml"
s = open(p).read()

def setline(pattern, new, s, section=None):
    m = re.search(pattern, s, re.M)
    assert m, pattern
    return s[:m.start()] + new + s[m.end():]

mode = re.search(r"^mode:\n((?:  .*\n)+)", s, re.M)
block = mode.group(1)
if "run" in args:
    block = re.sub(r'^  run: .*$', f'  run: "{args["run"]}"  # s3bbench', block, flags=re.M)
if "media" in args:
    block = re.sub(r'^  media: .*$', f'  media: "{args["media"]}"  # s3bbench', block, flags=re.M)
for key, name in (("interval", "interval_s"), ("heartbeat", "heartbeat_s")):
    if key in args:
        if re.search(rf"^  {name}: ", block, re.M):
            block = re.sub(rf"^  {name}: .*$", f"  {name}: {int(args[key])}  # s3bbench", block, flags=re.M)
        else:
            block += f"  {name}: {int(args[key])}  # s3bbench\n"
s = s[:mode.start(1)] + block + s[mode.end(1):]
if "vcap" in args:
    s = re.sub(r"^    message_cap: \d+(.*)$", rf"    message_cap: {int(args['vcap'])}  # s3bbench", s, count=1, flags=re.M)
if "scap" in args:
    s = re.sub(r"^  message_cap: \d+(.*)$", rf"  message_cap: {int(args['scap'])}  # s3bbench", s, count=1, flags=re.M)
open(p, "w").write(s)

import config_v2, rc_progressive_jpeg as rc
b = config_v2.load_for_boot(f"{A}/camera_schedule.yaml", p, f"{A}/camera_config.lkg.json")
v = b.values
print("[set] level", b.level, "hash", config_v2.config_hash(v))
print("[set] run", rc.resolve_run_mode(b, rc.resolve_runtime(None, b)[0]),
      "media", v["mode.media"], "vcap", v["video.send.message_cap"], "scap", v["still.message_cap"])
assert b.level == "v2", b.lines
