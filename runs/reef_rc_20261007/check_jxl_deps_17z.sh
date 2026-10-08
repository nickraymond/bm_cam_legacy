#!/bin/bash
# READ-ONLY: does bmcam003 have the nrjxl dependencies (cjxl binary, numpy) for RC2? 17Z wake.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,17,1,45,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 20); do ssh "${O[@]}" pi@bmcam003 'echo "cjxl: $(command -v cjxl) $(cjxl --version 2>&1 | head -1)"; python3 -c "import numpy; print(\"numpy\", numpy.__version__)" 2>&1; df -h /home/pi | tail -1; free -m | head -2' </dev/null > runs/reef_rc_20261007/pulled/bmcam003_jxl_deps.txt 2>&1 && break; sleep 4; done
cat runs/reef_rc_20261007/pulled/bmcam003_jxl_deps.txt
