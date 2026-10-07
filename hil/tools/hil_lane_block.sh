#!/bin/bash
# hil_lane_block.sh — set the `uplink: lane:` block LOCALLY in a bench unit's v2 camera_config.yaml (the C2 transmit
# lane as a jitter guard; EM/Nick GO 2026-10-06 for bmcam004), backup first, strict EFFECTIVE verify, auto-restore.
#
# Inputs:   $1 host (bmcam003|bmcam004), $2 on | off | verify; env GRID_S (3600) POST_S (375) PRE_S (20) WAIT_S (180)
# Outputs:  backup path, the diff, `effective lane: <enabled> <grid> <post> <pre> <wait> dropped [...]`, and the
#           runtime's `transmit_phase (C2)` print-config line; exit 0 only when effective matches and dropped = []
# Example:  hil/tools/hil_lane_block.sh bmcam004 on        (restore: copy the printed backup back, or `off`)
# Limits:   needs the v2 runtime; the unit's cycle should be stopped (deploy-window pattern); edits ONLY the
#           uplink.lane.* keys in the base (other uplink keys untouched).
set -u
H="${1:?host}"; MODE="${2:?on|off|verify}"
case "$H" in bmcam003|bmcam004) ;; *) echo "[hil-guard] REFUSED: $H is not a bench unit" >&2; exit 5;; esac
case "$MODE" in on|off|verify) ;; *) echo "mode: on | off | verify" >&2; exit 2;; esac
ssh -o BatchMode=yes -o ConnectTimeout=5 "pi@$H" "bash -s" "$MODE" "${GRID_S:-3600}" "${POST_S:-375}" "${PRE_S:-20}" "${WAIT_S:-180}" <<'REMOTE'
set -u
MODE="$1"; GRID="$2"; POST="$3"; PRE="$4"; WAIT="$5"; cd /home/pi/BM_Devel_Pi || exit 3
verify() {
  python3 -c "import config_v2 as c, supervisor_config as S; b = c.load_config('camera_config.yaml').base; e = S.resolve(b, c.read_state(S.state_path_for(b))); v = e.values; print('effective lane:', v['uplink.lane.enabled'], v['uplink.lane.grid_s'], v['uplink.lane.post_guard_s'], v['uplink.lane.pre_guard_s'], v['uplink.lane.max_wait_s'], 'dropped', e.dropped)" && \
  python3 rc_progressive_jpeg.py --print-config 2>&1 | grep -E "transmit_phase|\[PHASE\]" | head -3
}
if [ "$MODE" = verify ]; then verify; exit $?; fi
BK=camera_config.yaml.bak_lane_$(date -u +%Y%m%dT%H%M%SZ); cp -p camera_config.yaml "$BK" && echo "backup: /home/pi/BM_Devel_Pi/$BK"
python3 - "$MODE" "$GRID" "$POST" "$PRE" "$WAIT" <<'PY'
import sys
mode, grid, post, pre, wait = sys.argv[1:]
want = {"enabled": "true" if mode == "on" else "false"}
if mode == "on":
    want.update(grid_s=grid, post_guard_s=post, pre_guard_s=pre, max_wait_s=wait)
lines = open("camera_config.yaml").read().split("\n")
up = next((i for i, l in enumerate(lines) if l.split("#", 1)[0].rstrip() == "uplink:"), None)
if up is None:
    lines += ["uplink:", "  lane:"] + [f"    {k}: {v}" for k, v in want.items()]
else:
    end = next((i for i in range(up + 1, len(lines)) if lines[i][:1].strip() and not lines[i].startswith("#")), len(lines))
    ln = next((i for i in range(up + 1, end) if lines[i].split("#", 1)[0].rstrip() == "  lane:"), None)
    if ln is None:
        while end > up + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = ["  lane:"] + [f"    {k}: {v}" for k, v in want.items()]
    else:
        lend = next((i for i in range(ln + 1, end) if lines[i].strip() and (len(lines[i]) - len(lines[i].lstrip(" "))) <= 2), end)
        keep = [l for l in lines[ln + 1:lend] if l.strip() and l.split(":", 1)[0].strip() not in want]
        lines[ln + 1:lend] = keep + [f"    {k}: {v}" for k, v in want.items()]
open("camera_config.yaml", "w").write("\n".join(lines))
print("set uplink.lane:", want)
PY
rc=$?
[ $rc = 0 ] || { cp -p "$BK" camera_config.yaml; echo "edit failed: restored $BK"; exit 4; }
diff "$BK" camera_config.yaml
if ! V=$(verify 2>&1); then echo "$V" | tail -3; cp -p "$BK" camera_config.yaml; echo "REFUSED by the loader: restored $BK"; exit 4; fi
echo "$V"
if [ "$MODE" = on ]; then
  echo "$V" | grep -qE "^effective lane: True ${GRID}(\.0)? ${POST}(\.0)? ${PRE}(\.0)? ${WAIT}(\.0)? dropped \[\]" || { echo "MISMATCH: effective lane values; restoring $BK"; cp -p "$BK" camera_config.yaml; exit 4; }
else
  echo "$V" | grep -qE "^effective lane: False .*dropped \[\]" || { echo "MISMATCH: lane not off; restoring $BK"; cp -p "$BK" camera_config.yaml; exit 4; }
fi
REMOTE
