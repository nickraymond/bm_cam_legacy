#!/bin/bash
# JXL-RC backlog row for wake HH (read-only): media table on nereus000 for BMCAM_003 since heal_since (20:00Z 10/8) +
# this wake's health-check time, rsd asks landed on the console, heal chunks sent (cycle log). Appends to backlog.csv.
# Usage: backlog.sh YYYY-MM-DD HH
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
D=$1; HH=$2; R=runs/jxl_rc_20261008
ssh -o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 pi@192.168.1.45 "python3 /home/pi/hil_g4/hil_media_table.py --api https://nereus-vision-staging.onrender.com --devices BMCAM_003 --since 2026-10-08T19:55 --until ${D}T$HH:10 2>&1" < /dev/null > $R/analysis/media_${D}T$HH.csv
F=$(ls $R/console/wake_SPOT-33507C_${D}T${HH}00*.txt 2>/dev/null | head -1)
python3 - "$R/analysis/media_${D}T$HH.csv" "$F" "$R/pulled/cycle_${HH}Z.log" "$D" "$HH" "$R/backlog.csv" <<'PY'
import csv, os, re, sys
media, con, cyc, D, HH, out = sys.argv[1:]
rows = [r for r in csv.DictReader(open(media)) if r.get("media_id")]
open_rows = [r for r in rows if r["complete"] != "True"]
backlog = sum(int(r["missing"] or 0) for r in open_rows)
jx = [r for r in open_rows if r["captured_at"] >= "2026-10-08T22:00"]
jxb = sum(int(r["missing"] or 0) for r in jx)
C = open(con, errors="replace").read() if con and os.path.exists(con) else ""
hc = re.findall(r"(\d\d:\d\d:\d\d)\.\d+Z \[ORC\] \[INFO\] Running health check", C)
rsd = re.findall(r'Remote message received\(\d+\)! "bm pub bmcam/cmd \{"id":(\d+),"c":"rsd"', C)
Y = open(cyc, errors="replace").read() if os.path.exists(cyc) else ""
hs = re.search(r"\[HEAL\] sent (\d+) heal chunk", Y)
new = not os.path.exists(out)
with open(out, "a") as f:
    if new: f.write("wake_utc,images_total,images_complete,images_open,backlog_chunks,jxl_rc_open,jxl_rc_backlog_chunks,health_check,rsd_landed_on_console,heal_chunks_sent\n")
    f.write(f"{D}T{HH}:00Z,{len(rows)},{len(rows)-len(open_rows)},{len(open_rows)},{backlog},{len(jx)},{jxb},{'|'.join(hc)},{'|'.join(rsd)},{hs.group(1) if hs else 0}\n")
print(f"BACKLOG {D}T{HH}Z: images {len(rows)} complete {len(rows)-len(open_rows)} open {len(open_rows)} backlog {backlog} chunks (JXL-RC images: {len(jx)} open, {jxb} chunks) | health check {hc} | rsd on console {rsd} | heal chunks sent {hs.group(1) if hs else 0}")
for r in open_rows: print("  open", r["media_id"], r["captured_at"][5:16], r["received"] + "/" + r["expected"])
PY
