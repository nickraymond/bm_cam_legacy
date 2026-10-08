#!/bin/bash
# RC2 POST hook (hil_deploy_window.sh, after deploy + re-arm, cycle STOPPED, before the halt). Nick's OK 18:21Z:
# still.format nrjxl in bmcam003's BASE camera_config.yaml (backup, strict-load verify, revert on failure); then the
# pre-approved hil_b3a_layout.sh bmcam003 rgb; then BUILD_RECORD inputs (sha, sha256s, effective values, print-config).
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
R=runs/jxl_rc_20261008; O=(-o BatchMode=yes -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=3)
ssh "${O[@]}" pi@bmcam003 'bash -s' <<'REMOTE' > $R/pulled/post_format_20z.txt 2>&1
cd /home/pi/BM_Devel_Pi || exit 3
TS=$(date -u +%Y%m%dT%H%M%SZ); BK=/home/pi/hil_backup/rc2_$TS; mkdir -p $BK; cp -p camera_config.yaml $BK/ && echo "BACKUP $BK/camera_config.yaml"
python3 - <<'PY' || { cp -p $BK/camera_config.yaml .; echo "REVERTED (edit failed)"; exit 5; }
import os, re
p = "camera_config.yaml"; L = open(p).read().split("\n")
st = [i for i, l in enumerate(L) if l.split("#", 1)[0].rstrip() == "still:"]
if len(st) != 1: raise SystemExit(f"'still:' found {len(st)} times")
s = st[0]; e = next((i for i in range(s + 1, len(L)) if L[i][:1].strip() and not L[i].startswith("#")), len(L))
f = [i for i in range(s + 1, e) if re.match(r'^  format:', L[i])]
if len(f) > 1: raise SystemExit("still.format found twice")
if f: old = L[f[0]]; L[f[0]] = '  format: "nrjxl"  # RC2 (Nick OK 2026-10-08): JPEG XL stills'; print("EDIT replaced:", old.strip())
else: L.insert(s + 1, '  format: "nrjxl"  # RC2 (Nick OK 2026-10-08): JPEG XL stills'); print("EDIT inserted still.format nrjxl at line", s + 2)
open(p + ".tmp", "w").write("\n".join(L)); os.replace(p + ".tmp", p)
PY
python3 -c "
import config_v2 as c, supervisor_config as S
b = c.load_config('camera_config.yaml', strict=True).base
e = S.resolve(b, c.read_state(S.state_path_for(b))); v = e.values
print('STRICT OK base_hash', c.config_hash(b), 'effective format', v.get('still.format'), 'dropped', e.dropped)
" || { cp -p $BK/camera_config.yaml .; echo "REVERTED (strict load failed)"; exit 6; }
REMOTE
cat $R/pulled/post_format_20z.txt
grep -q "^STRICT OK .*effective format nrjxl" $R/pulled/post_format_20z.txt || { echo "FORMAT NOT SET -> skipping layout"; exit 7; }
hil/tools/hil_b3a_layout.sh bmcam003 rgb 2>&1 | tee $R/pulled/b3a_layout_20z.txt | tail -6
ssh "${O[@]}" pi@bmcam003 'cd /home/pi/BM_Devel_Pi; echo "== sha"; head -c 40 software_sha.txt; echo; echo "== sha256"; sha256sum camera_config.yaml camera_schedule.yaml bm_command_state_v2.json; echo "== cjxl"; cjxl --version 2>&1 | head -1; python3 -c "
import config_v2 as c, supervisor_config as S
b = c.load_config(\"camera_config.yaml\", strict=True).base
e = S.resolve(b, c.read_state(S.state_path_for(b))); v = e.values
print(\"EFFECTIVE\", {k: v.get(k) for k in (\"mode.media\",\"mode.run\",\"still.format\",\"still.raw.layout\",\"camera.exposure.profile\",\"uplink.msg_interval_s\",\"uplink.chunk_chars\",\"uplink.network_type\",\"uplink.lane.enabled\",\"still.message_cap\",\"still.crop\",\"still.output_width\",\"camera.image_processing.contrast\",\"schedule.window.start\",\"schedule.window.end\",\"power.halt.enabled\",\"power.halt.dry_run\")}, \"overlay\", e.overlay if hasattr(e,\"overlay\") else \"?\", \"dropped\", e.dropped, \"base_hash\", c.config_hash(b))
"; echo "== crontab"; crontab -l; echo "== print-config"; python3 rc_progressive_jpeg.py --print-config 2>&1' </dev/null > $R/pulled/build_after_rc2.txt 2>&1
ssh "${O[@]}" pi@bmcam003 'cat /home/pi/BM_Devel_Pi/camera_config.yaml' </dev/null > $R/pulled/build_camera_config.yaml 2>&1
grep -E "^EFFECTIVE|^[0-9a-f]{12}$" $R/pulled/build_after_rc2.txt | cut -c1-600; echo "POST done"
