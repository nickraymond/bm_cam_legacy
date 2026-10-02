#!/bin/bash
# hil_unit_snapshot.sh — READ-ONLY snapshot of one bmcam unit, for before/after records.
#
# Purpose:  one file per unit per moment (deploy before/after, gate start/end) holding what
#           a reviewer needs to know the unit's state: runtime sha, config hash, crontab,
#           camera processes, /dev/shm render, logind RemoveIPC/linger, CMA, disk, the R1
#           code markers (#97 render-dir fix, #98 video rule) and the latest cycle log's errors.
# Inputs:   $1 host (tailnet name, e.g. bmcam003), $2 tag (e.g. before_deploy)
#           env HIL_RUN_DIR (default .), HIL_UNIT_USER (pi), HIL_UNIT_APP (/home/pi/BM_Devel_Pi),
#           HIL_UNIT_REPO (/home/pi/repos/bm_cam_legacy)
# Outputs:  $HIL_RUN_DIR/snapshots/<host>_<tag>_<UTC>.txt, also printed. Key lines start with
#           "KEY " so they can be grepped: KEY sha, KEY cfg_hash, KEY cron_armed, KEY procs,
#           KEY shm_render, KEY shm_stay_on, KEY removeipc, KEY linger, KEY fix97, KEY rule98, KEY err_count.
# Example:  HIL_RUN_DIR=runs/r1_takeover_20261002 hil/tools/hil_unit_snapshot.sh bmcam003 before_deploy
# Limits:   runs `rc_progressive_jpeg.py --print-config` on the unit (~10 s on a Pi Zero 2W, opens
#           no camera/UART) with BMCAM_RENDER_DIR pointed at a temp dir: without that it recreates
#           /dev/shm/bmcam and fakes "render present" (found 2026-10-02 takeover). The ssh logout
#           of this snapshot can itself wipe /dev/shm on a unit without the RemoveIPC fix. Exit 1 if the unit is unreachable.
set -u
H="${1:?host}"; TAG="${2:?tag}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_host "$H"
U="${HIL_UNIT_USER:-pi}"; APP="${HIL_UNIT_APP:-/home/pi/BM_Devel_Pi}"; REPO="${HIL_UNIT_REPO:-/home/pi/repos/bm_cam_legacy}"
RUN="${HIL_RUN_DIR:-.}"; mkdir -p "$RUN/snapshots"
OUTF="$RUN/snapshots/${H}_${TAG}_$(date -u +%Y%m%dT%H%M%SZ).txt"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$U@$H" "APP=$APP REPO=$REPO bash -s" > "$OUTF" 2>&1 <<'REMOTE'
echo "== $(hostname) $(date -u +%FT%TZ) up $(cut -d' ' -f1 /proc/uptime)s"
# /dev/shm FIRST: --print-config below would otherwise recreate /dev/shm/bmcam (config_v2.render_dir)
echo "-- /dev/shm"; ls -la /dev/shm 2>&1; ls -la /dev/shm/bmcam 2>&1
[ -f /dev/shm/bmcam/camera_schedule.yaml ] && echo "KEY shm_render present" || echo "KEY shm_render missing"
[ -e /dev/shm/bmcam_stay_on ] && echo "KEY shm_stay_on present" || echo "KEY shm_stay_on missing"
# print-config renders into a throwaway dir so the snapshot never touches the live render
export BMCAM_RENDER_DIR=$(mktemp -d /tmp/hil_snapshot_render.XXXXXX)
echo "KEY sha $(cat $APP/software_sha.txt 2>/dev/null | head -1)"
echo "KEY repo $(git -C $REPO rev-parse --abbrev-ref HEAD 2>/dev/null) $(git -C $REPO log --oneline -1 2>/dev/null)"
echo "KEY cfg_hash $(cd $APP && python3 rc_progressive_jp[e]g.py --print-config 2>&1 | grep -o 'hash=[0-9a-f]*' | head -1)"
cd $APP && python3 rc_progressive_jp[e]g.py --print-config 2>&1 | grep -E '^\[CFG\]' | head -8
for f in camera_config.yaml camera_schedule.yaml bm_command_state_v2.json config_journal.jsonl; do
  [ -f $APP/$f ] && echo "file $(sha256sum $APP/$f | cut -c1-16) $f $(wc -l < $APP/$f)L" || echo "file missing $f"; done
python3 -c "
import json; s=json.load(open('$APP/bm_command_state_v2.json'))
print('overlay', json.dumps(s.get('overlay'), sort_keys=True)[:300])
print('guarded', json.dumps(s.get('guarded'), sort_keys=True)[:200])
print('boot', s.get('boot_counter'), 'hw', json.dumps(s.get('high_water'), sort_keys=True))
" 2>&1 | sed 's/^/state /'
echo "-- crontab"; crontab -l 2>&1 | grep -v '^\s*$'
crontab -l 2>/dev/null | grep -Eq '^[[:space:]]*@reboot[^#]*rc_run_capture_cycle' && echo "KEY cron_armed yes" || echo "KEY cron_armed no"
P=$(pgrep -af '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle|[r]picam-|[f]fmpeg|[b]m_cmd_daemon' | cut -c1-160)
echo "KEY procs $(printf '%s\n' "$P" | grep -c .)"; printf '%s\n' "$P"
R=$(grep -h -E '^[[:space:]]*#?[[:space:]]*RemoveIPC' /etc/systemd/logind.conf /etc/systemd/logind.conf.d/*.conf 2>/dev/null | tr '\n' ' ')
echo "KEY removeipc ${R:-unset(default yes)}"
ls -l /etc/systemd/logind.conf.d 2>&1 | sed 's/^/logind.d /'
echo "KEY linger $(loginctl show-user pi -p Linger 2>&1)"
echo "-- sessions"; loginctl list-sessions --no-legend 2>&1
grep -q 'recreated' $APP/supervisor_config.py 2>/dev/null && echo "KEY fix97 yes" || echo "KEY fix97 no"
grep -q '_rule_video_geometry' $APP/config_validate.py 2>/dev/null && echo "KEY rule98 yes" || echo "KEY rule98 no"
grep -E 'MemAvailable|CmaTotal|CmaFree' /proc/meminfo
df -h / | tail -1 | sed 's/^/disk /'
L=$(ls -t $APP/cron_logs/rc_cycle_*.log 2>/dev/null | head -1)
echo "KEY log ${L:-none}"
if [ -n "$L" ]; then
  echo "KEY err_count $(grep -c '\[ERR\]' $L)"
  echo "KEY reresolve_fail $(grep -c 'settings re-resolve failed' $L)"
  grep -E '\[ERR\]|\[WARN\].*render dir' $L | tail -5
  echo "-- tail"; tail -5 $L | cut -c1-200
fi
rm -rf "$BMCAM_RENDER_DIR"
REMOTE
RC=$?
cat "$OUTF"
echo "[snapshot] $OUTF (ssh rc=$RC)"
[ $RC -eq 255 ] && exit 1
exit 0
