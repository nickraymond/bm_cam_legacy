#!/bin/bash
# hil_b3a_layout.sh — set still.raw.layout LOCALLY in a bench unit's v2 camera_config.yaml (the backend's catalog
# does not know v11 yet), following the camera session's procedure (2026-10-06, desk-verified on the v11 loader):
# nested YAML, `layout:` inside `still:` → `raw:` at the raw keys' indent; backup first; strict-load verify; a
# refused file is restored from the backup immediately (the boot loader would otherwise fall back loudly to LKG).
#
# Inputs:   $1 host (bmcam003|bmcam004), $2 rgb | bayer4 | verify
# Outputs:  stdout: backup path, `effective: <format> <layout> <rgb_max_s> overlay [...] dropped [...]` and `still path
#           reads: ...`; exit 0 only when effective = nrjxl <layout> 45 with dropped [] and the still path agrees
# Example:  hil/tools/hil_b3a_layout.sh bmcam004 rgb      (restore: hil/tools/hil_b3a_layout.sh bmcam004 bayer4)
# Limits:   needs the v11 runtime (4584436+) deployed; changes ONLY still.raw.layout in the base: still.format=nrjxl may
#           come from the command-state overlay (bmcam004: cid 1000061) — the effective check covers it.
set -u
H="${1:?host}"; MODE="${2:?rgb|bayer4|verify}"
case "$H" in bmcam003|bmcam004) ;; *) echo "[hil-guard] REFUSED: $H is not a bench unit" >&2; exit 5;; esac
case "$MODE" in rgb|bayer4|verify) ;; *) echo "mode: rgb | bayer4 | verify" >&2; exit 2;; esac
ssh -o BatchMode=yes -o ConnectTimeout=5 "pi@$H" "bash -s" "$MODE" <<'REMOTE'
set -u
MODE="$1"; cd /home/pi/BM_Devel_Pi || exit 3
verify() {   # EFFECTIVE config = base ⊕ command-state overlay (supervisor_config.resolve), camera session 10/6
  python3 -c "import config_v2 as c, supervisor_config as S, rc_raw_jxl as X, tempfile; b = c.load_config('camera_config.yaml').base; e = S.resolve(b, c.read_state(S.state_path_for(b))); v = e.values; print('effective:', v['still.format'], v['still.raw.layout'], v['still.raw.rgb_encode_max_s'], 'overlay', sorted(e.overlay), 'dropped', e.dropped); p = tempfile.mktemp(); open(p, 'w').write(S.render_values(v)); r = X.load_raw_config(p); print('still path reads:', r['format'], r['layout'], r['rgb_encode_max_s'])"
}
if [ "$MODE" = verify ]; then verify; exit $?; fi
BK=camera_config.yaml.bak_b1_$(date -u +%Y%m%dT%H%M%SZ); cp -p camera_config.yaml "$BK" && echo "backup: /home/pi/BM_Devel_Pi/$BK"
python3 - "$MODE" <<'PY'
import re, sys
want = sys.argv[1]
lines = open("camera_config.yaml").read().split("\n")
out, in_still, in_raw, raw_indent, done, fmt_fixed = [], False, False, None, False, False
for i, ln in enumerate(lines):
    s = ln.split("#", 1)[0].rstrip()
    ind = len(ln) - len(ln.lstrip(" "))
    if s and ind == 0:                                   # top-level key
        if in_raw and not done:                          # raw: block ended without a layout line
            out.append(" " * raw_indent + f"layout: {want}"); done = True
        in_still, in_raw = s.startswith("still:"), False
    elif in_still and s.strip() == "raw:":
        in_raw, raw_indent = True, None
        out.append(ln); continue
    elif in_still and in_raw and s and raw_indent is not None and ind < raw_indent:
        if not done:
            out.append(" " * raw_indent + f"layout: {want}"); done = True
        in_raw = False
    if in_raw and s:
        if raw_indent is None:
            raw_indent = ind
        if ind == raw_indent and s.strip().startswith("layout:"):
            ln = " " * raw_indent + f"layout: {want}"; done = True
    out.append(ln)
if in_raw and not done and raw_indent is not None:
    out.append(" " * raw_indent + f"layout: {want}"); done = True
if not done:                                          # no raw: mapping yet -> append one at the end of still:
    si = next((i for i, l in enumerate(out) if l.startswith("still:")), None)
    if si is None:
        sys.exit("FAIL: no top-level still: mapping; file unchanged")
    end = next((i for i in range(si + 1, len(out)) if out[i][:1].strip() and not out[i].startswith("#")), len(out))
    while end > si + 1 and not out[end - 1].strip():
        end -= 1
    out[end:end] = ["  raw:", f"    layout: {want}"]; done = True
open("camera_config.yaml", "w").write("\n".join(out))
print(f"set still.raw.layout={want} (base only; still.format comes from base ⊕ overlay)")
PY
rc=$?
if [ $rc != 0 ]; then cp -p "$BK" camera_config.yaml; echo "edit failed: restored $BK"; exit 4; fi
diff "$BK" camera_config.yaml
if ! V=$(verify 2>&1); then echo "$V" | tail -3; cp -p "$BK" camera_config.yaml; echo "REFUSED by the loader: restored $BK"; exit 4; fi
echo "$V"
echo "$V" | grep -q "^effective: nrjxl $MODE 45 .*dropped \[\]" || { echo "MISMATCH: effective is not 'nrjxl $MODE 45' with dropped [] (no B3a this boot); restoring $BK"; cp -p "$BK" camera_config.yaml; exit 4; }
echo "$V" | grep -q "^still path reads: nrjxl $MODE 45" || { echo "MISMATCH: still path does not read nrjxl $MODE 45; restoring $BK"; cp -p "$BK" camera_config.yaml; exit 4; }
REMOTE
