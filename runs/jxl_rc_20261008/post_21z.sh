#!/bin/bash
# Run the RC2 POST (post_20z.sh: still.format nrjxl base edit + hil_b3a_layout.sh rgb + BUILD_RECORD inputs) in bmcam003's
# 21Z window, since the 20Z deploy skipped it on the sha check. The running 21Z cycle already read its config at boot,
# so the edit takes effect at the 22Z boot (JXL-RC wake 1).
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,21,1,15,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 30); do ssh -o BatchMode=yes -o ConnectTimeout=3 pi@bmcam003 true </dev/null 2>/dev/null && break; sleep 4; done
bash runs/jxl_rc_20261008/post_20z.sh
