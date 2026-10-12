#!/bin/bash
# readonly_check.sh: READ-ONLY pre-rehearsal check of one unit (EM-cleared 2026-10-12). No writes, no captures, no cron edits.
# Runs inside a wake window (Pi answers ~:00:30-:08). Output: runs/pool_rehearsal_20261012/check_<host>_<UTC>.txt
# Never prints Wi-Fi secrets (no nmcli --show-secrets).
H=$1; OUT=$(dirname "$0")/check_${H}_$(date -u +%Y%m%dT%H%MZ).txt
ssh -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 pi@$H 'bash -s' > "$OUT" 2>&1 <<'REMOTE'
cd /home/pi/BM_Devel_Pi || exit 1
echo "== host $(hostname) utc $(date -u +%FT%TZ) uptime $(cut -d. -f1 /proc/uptime)s"
echo "== sha"; head -c 40 software_sha.txt; echo
echo "== df"; df -h / /home /dev/shm 2>/dev/null
echo "== du top-level (MB)"; du -sm /home/pi/BM_Devel_Pi/* 2>/dev/null | sort -nr | head -12
echo "== biggest media by type"
for pat in '*.jxl' '*nrjxl*' '*.jpg' '*.jpeg' '*.h264' '*.mp4' '*.dng' '*.heic'; do
  find /home/pi/BM_Devel_Pi -xdev -type f -iname "$pat" -printf '%s %TY-%Tm-%Td %p\n' 2>/dev/null | sort -nr | head -2 | sed "s#^#[$pat] #"
  echo "[$pat] count $(find /home/pi/BM_Devel_Pi -xdev -type f -iname "$pat" 2>/dev/null | wc -l)"
done
echo "== #97 fix (write_render in supervisor_config.py)"; grep -c "def write_render" supervisor_config.py
echo "== RemoveIPC"; grep -rh "^RemoveIPC" /etc/systemd/logind.conf /etc/systemd/logind.conf.d/ 2>/dev/null || echo "RemoveIPC not set (default yes)"
echo "== /dev/shm"; ls -la /dev/shm/ 2>&1 | head -6
echo "== wifi power_save"; /sbin/iw dev wlan0 get power_save 2>&1 || iw dev wlan0 get power_save 2>&1
echo "== nmcli connections"; nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,DEVICE con show 2>&1
for c in $(nmcli -t -f NAME,TYPE con show 2>/dev/null | awk -F: '$2 ~ /wireless/ {print $1}'); do
  echo "-- $c: $(nmcli -t -g 802-11-wireless.mode,802-11-wireless.ssid,ipv4.method,802-11-wireless.powersave con show "$c" 2>&1 | tr '\n' ' ')"
done
echo "== network island config (camera_config.yaml)"; grep -nE -A10 "^ *(network|network_island|wifi|ap_fallback)" camera_config.yaml 2>/dev/null | head -30
echo "== island/hotspot units"; systemctl list-unit-files 2>/dev/null | grep -iE "nereus|bmcam|island|hotspot|ap_|wifi" | head
echo "== crontab"; crontab -l 2>&1
echo "== config"; sha256sum camera_config.yaml camera_schedule.yaml 2>/dev/null | cut -c1-8,65-
ls -la *overlay* remote_overlay* 2>/dev/null; f=$(ls -t cron_logs/rc_cycle_*.log | head -1); echo "latest log $f"; grep -m1 "\[CFG\] config v2" "$f"
python3 -c "
import config_v2 as c, supervisor_config as S
b = c.load_config('camera_config.yaml', strict=True).base
st = c.read_state(S.state_path_for(b)); e = S.resolve(b, st); v = e.values
print('BASE_HASH', c.config_hash(b), 'OVERLAY', st.get('overlay'))
print('EFFECTIVE', {k: v.get(k) for k in ('mode.media','mode.run','still.format','camera.exposure.profile','camera.image_processing.contrast','schedule.window.start','schedule.window.end','power.halt.enabled')})
" 2>&1 | cut -c1-600
echo "== mem"; grep -E "MemAvailable|CmaTotal|CmaFree" /proc/meminfo
echo "== done"
REMOTE
echo "rc=$? -> $OUT ($(wc -l < "$OUT") lines)"
