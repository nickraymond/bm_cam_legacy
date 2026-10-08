#!/bin/bash
# READ-ONLY: bmcam004 reflash / sent-record-wipe evidence for the heal gate's records_since (EM ask 2026-10-08 07:20Z).
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,8,1,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 20); do ssh "${O[@]}" pi@bmcam004 'echo "== fs created (tune2fs, read-only)"; sudo -n tune2fs -l /dev/mmcblk0p2 2>&1 | grep -iE "created|last mount time|mount count"; echo "== birth times"; stat -c "%w | %y | %n" / /home/pi /home/pi/BM_Devel_Pi /home/pi/BM_Devel_Pi/sent /home/pi/repos/bm_cam_legacy /etc/machine-id /etc/hostname 2>&1; echo "== oldest sent files by mtime"; ls -ltr --time-style=full-iso /home/pi/BM_Devel_Pi/sent | head -4; echo "== pi image"; cat /boot/firmware/issue.txt 2>/dev/null || cat /boot/issue.txt 2>/dev/null | head -2' </dev/null > runs/prune_gate_20261007/pulled/bmcam004_reflash_probe.txt 2>&1 && break; sleep 4; done
cat runs/prune_gate_20261007/pulled/bmcam004_reflash_probe.txt
