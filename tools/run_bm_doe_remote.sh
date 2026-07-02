#!/usr/bin/env bash
set -Eeuo pipefail

# BM Camera Image DOE remote runner
# Run from VS Code / Mac terminal.
# It copies the DOE capture script to each camera, triggers the JPEG-only DOE,
# waits for completion, downloads results, generates contact sheets, and prints
# a link-budget summary.

REPO="${REPO:-/Users/nickbuemond/Documents/GitHub/bm_cam_legacy}"
HOSTS_STR="${HOSTS:-bmcam001 bmcam002}"
REMOTE_APP="${REMOTE_APP:-/home/pi/BM_Devel_Pi}"
OUT_BASE="${OUT_BASE:-$HOME/Downloads/bm_underwater_doe}"
LOCAL_LOG_BASE="${LOCAL_LOG_BASE:-$HOME/Downloads/bm_doe_logs}"
RUN_TAG="${RUN_TAG:-underwater_jpeg_auto_$(date -u +%Y%m%dT%H%M%SZ)}"

# DOE matrix
RESOLUTIONS="${RESOLUTIONS:-480p 720p 420sq 720sq}"
SOURCE_MODES="${SOURCE_MODES:-jpeg}"
QUALITIES="${QUALITIES:-25 35 45 55 65 75}"
SOURCE_JPEG_QUALITY="${SOURCE_JPEG_QUALITY:-95}"
HEIC_ENCODE_MODE="${HEIC_ENCODE_MODE:-subprocess}"
HEIC_ENCODE_TIMEOUT_SEC="${HEIC_ENCODE_TIMEOUT_SEC:-180}"

# Link budget assumptions
LINK_THROUGHPUT_KBPS="${LINK_THROUGHPUT_KBPS:-0.361}"
TARGET_TRANSMIT_MIN="${TARGET_TRANSMIT_MIN:-16}"
HARD_TRANSMIT_MIN="${HARD_TRANSMIT_MIN:-18}"

# Contact sheet settings
TILE_WIDTH="${TILE_WIDTH:-900}"
IMAGE_HEIGHT="${IMAGE_HEIGHT:-520}"
JPEG_ROUNDTRIP_QUALITY="${JPEG_ROUNDTRIP_QUALITY:-95}"

# Polling
POLL_SEC="${POLL_SEC:-20}"
TIMEOUT_MIN="${TIMEOUT_MIN:-35}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"

CAPTURE_SCRIPT="$REPO/tests/bm_image_quality_doe_capture.py"
CONTACT_SHEET_SCRIPT="$REPO/tools/make_bm_image_doe_contact_sheet.py"
BATCH_DIR="$OUT_BASE/$RUN_TAG"

IFS=' ' read -r -a HOSTS <<< "$HOSTS_STR"
IFS=' ' read -r -a RES_ARR <<< "$RESOLUTIONS"
IFS=' ' read -r -a QUAL_ARR <<< "$QUALITIES"
IFS=' ' read -r -a SRC_ARR <<< "$SOURCE_MODES"
EXPECTED_ROWS=$(( ${#RES_ARR[@]} * ${#QUAL_ARR[@]} * ${#SRC_ARR[@]} ))

mkdir -p "$OUT_BASE" "$LOCAL_LOG_BASE" "$BATCH_DIR"

log() { printf '\n[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

b64() {
  printf '%s' "$1" | python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())'
}

ssh_cam() {
  local host="$1"
  shift
  ssh -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" -o ServerAliveInterval=15 -o ServerAliveCountMax=2 "pi@$host" "$@"
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "ERROR: missing required file: $path" >&2
    exit 1
  fi
}

require_file "$CAPTURE_SCRIPT"
require_file "$CONTACT_SHEET_SCRIPT"

cat <<EOF
============================================================
BM IMAGE DOE REMOTE RUNNER
============================================================
Repo:              $REPO
Hosts:             ${HOSTS[*]}
Run tag:           $RUN_TAG
Output batch dir:  $BATCH_DIR
Resolutions:       $RESOLUTIONS
Source modes:      $SOURCE_MODES
Qualities:         $QUALITIES
HEIC encode mode:  $HEIC_ENCODE_MODE
Expected rows:     $EXPECTED_ROWS per camera
Link throughput:   $LINK_THROUGHPUT_KBPS kbps
Target/hard:       $TARGET_TRANSMIT_MIN / $HARD_TRANSMIT_MIN min
============================================================
EOF

log "Preflight: checking SSH connectivity"
for host in "${HOSTS[@]}"; do
  echo "---- $host ----"
  if ! ssh_cam "$host" 'hostname; date -u +%Y-%m-%dT%H:%M:%SZ; uptime' ; then
    echo "ERROR: cannot reach $host over SSH. Stop here and retry when the camera is powered/online." >&2
    exit 1
  fi
done

log "Stopping stale DOE processes and disabling temporary DOE boot cron"
for host in "${HOSTS[@]}"; do
  echo "---- $host ----"
  ssh_cam "$host" 'bash -s' <<'REMOTE'
set -euo pipefail
APP="/home/pi/BM_Devel_Pi"
BACKUP_DIR="$APP/crontab_backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
crontab -l > "$BACKUP_DIR/crontab_pre_manual_doe_$STAMP.txt" 2>/dev/null || true
crontab -l 2>/dev/null \
  | grep -v 'run_bm_image_doe_boot.sh' \
  | grep -v 'bm_image_doe_boot.lock' \
  > /tmp/crontab_no_doe || true
crontab /tmp/crontab_no_doe
pkill -f doe_capture_quality_sweep.py 2>/dev/null || true
pkill -f run_bm_image_doe_boot.sh 2>/dev/null || true
pkill -f bm_image_doe 2>/dev/null || true
rm -f /tmp/bm_image_doe_manual.lock /tmp/bm_image_doe_boot.lock 2>/dev/null || true
sleep 1
pgrep -af 'doe_capture_quality_sweep|run_bm_image_doe_boot|bm_image_doe' || echo "no DOE process running"
REMOTE
done

log "Copying DOE capture script to cameras"
for host in "${HOSTS[@]}"; do
  echo "---- $host ----"
  scp -q "$CAPTURE_SCRIPT" "pi@$host:$REMOTE_APP/doe_capture_quality_sweep.py"
  ssh_cam "$host" "chmod +x '$REMOTE_APP/doe_capture_quality_sweep.py'; cd '$REMOTE_APP' && /usr/bin/python3 -m py_compile ./doe_capture_quality_sweep.py && /usr/bin/python3 ./doe_capture_quality_sweep.py --list-resolutions | grep -E '480p|720p|420sq|720sq'"
done

log "Triggering DOE on all cameras"
RUN_TAG_B64="$(b64 "$RUN_TAG")"
RESOLUTIONS_B64="$(b64 "$RESOLUTIONS")"
SOURCE_MODES_B64="$(b64 "$SOURCE_MODES")"
QUALITIES_B64="$(b64 "$QUALITIES")"
SOURCE_JPEG_QUALITY_B64="$(b64 "$SOURCE_JPEG_QUALITY")"
HEIC_ENCODE_MODE_B64="$(b64 "$HEIC_ENCODE_MODE")"
HEIC_ENCODE_TIMEOUT_SEC_B64="$(b64 "$HEIC_ENCODE_TIMEOUT_SEC")"
LINK_THROUGHPUT_KBPS_B64="$(b64 "$LINK_THROUGHPUT_KBPS")"
TARGET_TRANSMIT_MIN_B64="$(b64 "$TARGET_TRANSMIT_MIN")"
HARD_TRANSMIT_MIN_B64="$(b64 "$HARD_TRANSMIT_MIN")"

for host in "${HOSTS[@]}"; do
  echo "---- $host ----"
  ssh_cam "$host" \
    "RUN_TAG_B64='$RUN_TAG_B64' RESOLUTIONS_B64='$RESOLUTIONS_B64' SOURCE_MODES_B64='$SOURCE_MODES_B64' QUALITIES_B64='$QUALITIES_B64' SOURCE_JPEG_QUALITY_B64='$SOURCE_JPEG_QUALITY_B64' HEIC_ENCODE_MODE_B64='$HEIC_ENCODE_MODE_B64' HEIC_ENCODE_TIMEOUT_SEC_B64='$HEIC_ENCODE_TIMEOUT_SEC_B64' LINK_THROUGHPUT_KBPS_B64='$LINK_THROUGHPUT_KBPS_B64' TARGET_TRANSMIT_MIN_B64='$TARGET_TRANSMIT_MIN_B64' HARD_TRANSMIT_MIN_B64='$HARD_TRANSMIT_MIN_B64' bash -s" <<'REMOTE'
set -euo pipefail

decode_b64() {
  printf '%s' "$1" | base64 -d
}

RUN_TAG="$(decode_b64 "$RUN_TAG_B64")"
RESOLUTIONS="$(decode_b64 "$RESOLUTIONS_B64")"
SOURCE_MODES="$(decode_b64 "$SOURCE_MODES_B64")"
QUALITIES="$(decode_b64 "$QUALITIES_B64")"
SOURCE_JPEG_QUALITY="$(decode_b64 "$SOURCE_JPEG_QUALITY_B64")"
HEIC_ENCODE_MODE="$(decode_b64 "$HEIC_ENCODE_MODE_B64")"
HEIC_ENCODE_TIMEOUT_SEC="$(decode_b64 "$HEIC_ENCODE_TIMEOUT_SEC_B64")"
LINK_THROUGHPUT_KBPS="$(decode_b64 "$LINK_THROUGHPUT_KBPS_B64")"
TARGET_TRANSMIT_MIN="$(decode_b64 "$TARGET_TRANSMIT_MIN_B64")"
HARD_TRANSMIT_MIN="$(decode_b64 "$HARD_TRANSMIT_MIN_B64")"

APP="/home/pi/BM_Devel_Pi"
LOG_DIR="$APP/doe_trigger_logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME="$(hostname)"
LOG_FILE="$LOG_DIR/${HOSTNAME}_manual_doe_${STAMP}.log"
mkdir -p "$LOG_DIR" "$APP/doe_runs"
cd "$APP"

echo "REMOTE_RUN_TAG=$RUN_TAG"
echo "REMOTE_RESOLUTIONS=$RESOLUTIONS"
echo "REMOTE_SOURCE_MODES=$SOURCE_MODES"
echo "REMOTE_QUALITIES=$QUALITIES"
echo "REMOTE_HEIC_ENCODE_MODE=$HEIC_ENCODE_MODE"

# shellcheck disable=SC2086
nohup /usr/bin/flock -n /tmp/bm_image_doe_manual.lock \
  /usr/bin/python3 -u ./doe_capture_quality_sweep.py \
    --tag "$RUN_TAG" \
    --resolutions $RESOLUTIONS \
    --source-modes $SOURCE_MODES \
    --qualities $QUALITIES \
    --source-jpeg-quality "$SOURCE_JPEG_QUALITY" \
    --heic-encode-mode "$HEIC_ENCODE_MODE" \
    --heic-encode-timeout-sec "$HEIC_ENCODE_TIMEOUT_SEC" \
    --link-throughput-kbps "$LINK_THROUGHPUT_KBPS" \
    --target-transmit-min "$TARGET_TRANSMIT_MIN" \
    --hard-transmit-min "$HARD_TRANSMIT_MIN" \
  > "$LOG_FILE" 2>&1 &
PID=$!

echo "DOE_STARTED=true"
echo "PID=$PID"
echo "DOE_LOG=$LOG_FILE"
REMOTE
done
remote_status() {
  local host="$1"
  ssh_cam "$host" 'bash -s' -- "$RUN_TAG" "$EXPECTED_ROWS" <<'REMOTE'
set -euo pipefail
RUN_TAG="$1"
EXPECTED_ROWS="$2"
APP="/home/pi/BM_Devel_Pi"

PROC_COUNT="$(pgrep -fc 'doe_capture_quality_sweep.py|bm_image_doe_manual.lock' || true)"
LOG_FILE="$(ls -t "$APP"/doe_trigger_logs/*_manual_doe_*.log 2>/dev/null | head -1 || true)"
RUN_DIR="$(find "$APP/doe_runs" -mindepth 1 -maxdepth 1 -type d -name "*_${RUN_TAG}_*" -printf "%T@ %p\n" 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"
ROWS=0
FILES=0
SIZE="0"
DONE="false"
HAS_OUTPUT_MARKER="false"
TAIL=""

if [ -n "$RUN_DIR" ]; then
  FILES="$(find "$RUN_DIR/images" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')"
  SIZE="$(du -sh "$RUN_DIR" 2>/dev/null | awk '{print $1}' || echo 0)"
  if [ -f "$RUN_DIR/results.csv" ]; then
    ROWS="$(python3 - <<PY
import csv
from pathlib import Path
p=Path('$RUN_DIR/results.csv')
try:
    print(max(0, sum(1 for _ in csv.DictReader(p.open()))))
except Exception:
    print(0)
PY
)"
  fi
fi

if [ -n "$LOG_FILE" ]; then
  if grep -q 'DOE_OUTPUT_DIR=' "$LOG_FILE" 2>/dev/null; then HAS_OUTPUT_MARKER="true"; fi
  TAIL="$(tr -d '\000' < "$LOG_FILE" | tail -n 12 | sed 's/[|]/\//g')"
fi

if [ "$ROWS" -ge "$EXPECTED_ROWS" ] && [ "$PROC_COUNT" = "0" ]; then DONE="true"; fi
FAILED="false"
if [ "$PROC_COUNT" = "0" ] && [ "$DONE" != "true" ] && [ "$ROWS" -gt 0 ]; then FAILED="true"; fi

printf 'PROC_COUNT=%s\nLOG_FILE=%s\nRUN_DIR=%s\nROWS=%s\nFILES=%s\nSIZE=%s\nDONE=%s\nHAS_OUTPUT_MARKER=%s\nFAILED=%s\nTAIL<<EOF\n%s\nEOF\n' \
  "$PROC_COUNT" "$LOG_FILE" "$RUN_DIR" "$ROWS" "$FILES" "$SIZE" "$DONE" "$HAS_OUTPUT_MARKER" "$FAILED" "$TAIL"
REMOTE
}

log "Waiting for DOE completion"
DEADLINE=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
DONE_HOSTS=""
while true; do
  all_done=true
  for host in "${HOSTS[@]}"; do
    if [[ " $DONE_HOSTS " == *" $host "* ]]; then
      continue
    fi

    echo ""
    echo "==== STATUS $host ===="
    if status_text="$(remote_status "$host")"; then
      echo "$status_text" | sed -n '1,9p'
      echo "$status_text" | sed -n '/TAIL<<EOF/,$p' | head -20
      done_value="$(echo "$status_text" | awk -F= '/^DONE=/{print $2}' | tail -1)"
      failed_value="$(echo "$status_text" | awk -F= '/^FAILED=/{print $2}' | tail -1)"
      if [[ "$done_value" == "true" ]]; then
        DONE_HOSTS="$DONE_HOSTS $host"
        echo "COMPLETE: $host"
      elif [[ "$failed_value" == "true" ]]; then
        echo "ERROR: DOE stopped early on $host. See log above." >&2
        exit 4
      else
        all_done=false
      fi
    else
      echo "WARN: $host unreachable right now; will keep waiting until timeout"
      all_done=false
    fi
  done

  if [[ "$all_done" == "true" ]]; then
    break
  fi

  if (( $(date +%s) > DEADLINE )); then
    echo "ERROR: timeout waiting for DOE completion after ${TIMEOUT_MIN} min" >&2
    exit 2
  fi

  sleep "$POLL_SEC"
done

log "Downloading completed DOE runs"
for host in "${HOSTS[@]}"; do
  echo "---- $host ----"
  RUN_DIR="$(ssh_cam "$host" 'python3 - <<PY
from pathlib import Path
import csv, os
run_tag = os.environ.get("RUN_TAG_FILTER", "")
expected = int(os.environ.get("EXPECTED_ROWS_FILTER", "24"))
base = Path("/home/pi/BM_Devel_Pi/doe_runs")
cands = sorted([p for p in base.glob(f"*_{run_tag}_*") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
for d in cands:
    p = d / "results.csv"
    if not p.exists():
        continue
    try:
        rows = list(csv.DictReader(p.open()))
    except Exception:
        continue
    if len(rows) >= expected:
        print(d)
        break
PY' 2>/dev/null)" || true
  # The environment method above can be fragile with ssh quoting; fallback to direct remote command.
  if [[ -z "$RUN_DIR" ]]; then
    RUN_DIR="$(ssh_cam "$host" "python3 - <<'PY'
from pathlib import Path
import csv
run_tag='$RUN_TAG'
expected=$EXPECTED_ROWS
base=Path('/home/pi/BM_Devel_Pi/doe_runs')
cands=sorted([p for p in base.glob(f'*_{run_tag}_*') if p.is_dir()], key=lambda p:p.stat().st_mtime, reverse=True)
for d in cands:
    p=d/'results.csv'
    if not p.exists():
        continue
    try:
        rows=list(csv.DictReader(p.open()))
    except Exception:
        continue
    if len(rows)>=expected:
        print(d)
        break
PY")"
  fi

  if [[ -z "$RUN_DIR" ]]; then
    echo "ERROR: no completed DOE run found on $host" >&2
    exit 3
  fi

  echo "$host RUN_DIR=$RUN_DIR"
  scp -q -r "pi@$host:${RUN_DIR}" "$BATCH_DIR/"
done

log "Generating contact sheets"
cd "$REPO"
python3 -m pip install pillow pillow-heif >/dev/null

for RESULTS_CSV in "$BATCH_DIR"/*/results.csv; do
  [[ -f "$RESULTS_CSV" ]] || continue
  LOCAL_DIR="$(dirname "$RESULTS_CSV")"
  echo "---- $LOCAL_DIR ----"
  python3 "$CONTACT_SHEET_SCRIPT" \
    --results-csv "$RESULTS_CSV" \
    --export-jpeg-roundtrip \
    --jpeg-quality "$JPEG_ROUNDTRIP_QUALITY" \
    --tile-width "$TILE_WIDTH" \
    --image-height "$IMAGE_HEIGHT"
done

log "Writing combined summary"
COMBINED="$BATCH_DIR/combined_results.csv"
python3 - <<PY
import csv
from pathlib import Path
from collections import Counter
batch=Path('$BATCH_DIR')
combined=batch/'combined_results.csv'
rows=[]
fieldnames=[]
for p in sorted(batch.glob('*/results.csv')):
    with p.open(newline='', encoding='utf-8') as f:
        r=list(csv.DictReader(f))
    for row in r:
        row['local_run_dir']=p.parent.name
    rows.extend(r)
    for row in r:
        for k in row.keys():
            if k not in fieldnames:
                fieldnames.append(k)
if rows:
    with combined.open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(rows)
print('combined_results=', combined)
for p in sorted(batch.glob('*/results.csv')):
    with p.open(newline='', encoding='utf-8') as f:
        rr=list(csv.DictReader(f))
    counts=Counter(x.get('link_budget_status','unknown') for x in rr)
    print('\n' + '='*72)
    print(p.parent.name)
    print('Budget counts:', dict(counts))
    print('-'*72)
    for x in rr:
        status=x.get('link_budget_status','unknown')
        if status in ('pass','warn'):
            print(f"{status.upper():4} {x.get('resolution_key',''):6} q{int(float(x.get('quality',0))):03d} "
                  f"{float(x.get('heic_size_kb',0)):8.1f} KB "
                  f"{int(float(x.get('estimated_bm_buffers',0))):5d} buf "
                  f"{float(x.get('estimated_transmit_minutes',0)):5.1f} min")
PY

log "Done"
echo "BATCH_DIR=$BATCH_DIR"
open "$BATCH_DIR" >/dev/null 2>&1 || true
