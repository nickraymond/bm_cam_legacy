#!/bin/bash
# hil_weekend_mirror.sh: detached mirror of a weekend evidence folder to nereus000 every 30 min (EM 2026-10-09: a Mac
# sleep may lose command sends but never the record). One-way, additive (rsync -a, no --delete), into a folder that
# only this tool writes. Companion to hil_weekend_collector.sh (whose running instance is not edited).
# Inputs:  $1 local folder (e.g. runs/weekend_20261010), $2 remote dir on nereus000 (e.g. /home/pi/weekend_20261010),
#          $3 until (UTC ISO). Stop: touch <local>/STOP.
# Example: nohup bash hil/tools/hil_weekend_mirror.sh runs/weekend_20261010 /home/pi/weekend_20261010 2026-10-12T16:30:00Z \
#            > runs/weekend_20261010/mirror.out 2>&1 < /dev/null & disown
set -u
cd "$(dirname "$0")/../.."
SRC=$1; DST=$2; UNTIL=$3
caffeinate -i -w $$ &
END=$(python3 -c "import datetime as d,sys;print(int(d.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$UNTIL")
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@192.168.1.45 "mkdir -p $DST" </dev/null
while :; do
  rsync -a -e "ssh -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=3" "$SRC/" "pi@192.168.1.45:$DST/" </dev/null
  echo "$(date -u +%FT%TZ) mirror rc=$? $(du -sk "$SRC" | cut -f1) KiB" >> "$SRC/mirror.log"
  [ -f "$SRC/STOP" ] && break
  [ "$(date +%s)" -ge "$END" ] && break
  sleep 1800
done
echo "$(date -u +%FT%TZ) mirror exit" >> "$SRC/mirror.log"
