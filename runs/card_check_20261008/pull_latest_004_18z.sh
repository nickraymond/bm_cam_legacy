#!/bin/bash
# READ-ONLY: in bmcam004's 18Z wake (11 AM PDT), list its newest local captures and pull the newest display image
# (JPEG/PNG <= 1 MB) or, failing that, the newest DNG to downscale on the Mac. For the EM (v3 colour card check).
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
D=runs/card_check_20261008; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,18,4,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 20); do ssh "${O[@]}" pi@bmcam004 'cd /home/pi/BM_Devel_Pi && ls -lt --time-style=+%FT%TZ images 2>/dev/null | head -12' </dev/null > $D/bmcam004_images_ls.txt 2>&1 && break; sleep 4; done
cat $D/bmcam004_images_ls.txt
F=$(awk 'NR>1 && $NF ~ /\.(jpg|jpeg|png)$/ && $5 <= 1048576 {print $NF; exit}' $D/bmcam004_images_ls.txt)
[ -z "$F" ] && F=$(awk 'NR>1 && $NF ~ /\.dng$/ {print $NF; exit}' $D/bmcam004_images_ls.txt)
[ -n "$F" ] && scp -q "${O[@]}" "pi@bmcam004:/home/pi/BM_Devel_Pi/images/$F" $D/ </dev/null && echo "PULLED $D/$F ($(wc -c < $D/$F) B)" || echo "NOTHING PULLED"
