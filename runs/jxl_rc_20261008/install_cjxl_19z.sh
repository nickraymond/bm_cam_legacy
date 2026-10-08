#!/bin/bash
# Nick's OK (TE2 chat 18:21Z): install libjxl-tools on bmcam003 for RC2 (nrjxl). 19Z window (12 PM PDT), starting
# 19:01:30Z so apt/dpkg finish well before the cycle's own halt (~19:08). Precedent bmcam004 10/5: libjxl-tools
# 0.11.2-0.1~deb13u2 + libgif7. RESTORE: ssh pi@bmcam003 'sudo apt-get remove -y libjxl-tools'. Also pulls the newest
# local display JPEG read-only (card check).
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/jxl_rc_20261008; G=$R/gate.log; mkdir -p $R/pulled
O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
caffeinate -i -w $$ &
t=$(python3 -c "import datetime as d;print(int(d.datetime(2026,10,8,19,1,30,tzinfo=d.timezone.utc).timestamp()))"); until [ "$(date +%s)" -ge "$t" ]; do sleep 5; done
for i in $(seq 1 20); do ssh "${O[@]}" pi@bmcam003 true </dev/null 2>/dev/null && break; sleep 4; done
ssh "${O[@]}" pi@bmcam003 'bash -s' <<'REMOTE' > $R/pulled/install_cjxl_19z.txt 2>&1
set -u
echo "start $(date -u +%T) up $(cut -d. -f1 /proc/uptime)s"
command -v cjxl && { echo "ALREADY PRESENT"; cjxl --version | head -1; exit 0; }
if ! apt-cache policy libjxl-tools 2>/dev/null | grep -q "Candidate: [0-9]"; then echo "no candidate -> apt-get update"; sudo -n apt-get update -qq 2>&1 | tail -3; fi
apt-cache policy libjxl-tools | head -3
sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -q libjxl-tools 2>&1 | tail -6
echo "rc=${PIPESTATUS[0]} end $(date -u +%T)"
echo "cjxl: $(command -v cjxl) $(cjxl --version 2>&1 | head -1)"
dpkg -l libjxl-tools libgif7 2>/dev/null | tail -2
sudo -n dpkg --audit && echo "dpkg audit clean"
REMOTE
cat $R/pulled/install_cjxl_19z.txt
echo "$(date -u +%FT%TZ) [TE2] libjxl-tools install on bmcam003: $(grep -E '^(ALREADY|rc=|cjxl:|dpkg audit|no candidate)' $R/pulled/install_cjxl_19z.txt | tr '\n' ' ') RESTORE: ssh pi@bmcam003 'sudo apt-get remove -y libjxl-tools'" | tee -a $G
# card check (read-only): newest local display JPEG
ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi && ls -t images/*_image_compressed.jpg 2>/dev/null | head -1' </dev/null > $R/pulled/.newest 2>/dev/null
N=$(cat $R/pulled/.newest); [ -n "$N" ] && mkdir -p runs/card_check_20261008 && scp -q "${O[@]}" "pi@bmcam003:/home/pi/BM_Devel_Pi/$N" runs/card_check_20261008/bmcam003_$(basename $N) </dev/null && echo "PULLED runs/card_check_20261008/bmcam003_$(basename $N)"
