#!/usr/bin/env bash
# Deploy the BM legacy camera runtime from the Git checkout into the live runtime folder.
#
# Intended repo path:
#   tools/deploy_runtime.sh
#
# Default behavior is conservative:
#   - backs up /home/pi/BM_Devel_Pi
#   - copies only the explicitly approved runtime files
#   - does NOT overwrite camera_schedule.yaml unless requested
#   - runs a Python syntax check after copying
#
# Typical use on a Pi:
#   cd /home/pi/repos/bm_cam_legacy
#   git pull
#   ./tools/deploy_runtime.sh
#
# Include schedule file intentionally:
#   ./tools/deploy_runtime.sh --include-schedule
#
# Sync all top-level files from BM_Devel_Pi intentionally:
#   ./tools/deploy_runtime.sh --all-runtime-files
#
# Notes:
#   --all-runtime-files still excludes generated/runtime directories and common artifacts.
#   For production cameras, prefer the default explicit file list.

set -euo pipefail

SRC="/home/pi/repos/bm_cam_legacy/BM_Devel_Pi"
DST="/home/pi/BM_Devel_Pi"
BACKUP_DIR="/home/pi/backups"
INCLUDE_SCHEDULE="false"
ALL_RUNTIME_FILES="false"
DRY_RUN="false"

usage() {
  cat <<'EOF'
Usage: tools/deploy_runtime.sh [options]

Options:
  --src PATH                 Source runtime folder. Default: /home/pi/repos/bm_cam_legacy/BM_Devel_Pi
  --dst PATH                 Live runtime folder. Default: /home/pi/BM_Devel_Pi
  --backup-dir PATH          Backup folder. Default: /home/pi/backups
  --include-schedule         Also copy camera_schedule.yaml from repo to live runtime.
  --all-runtime-files        Copy all top-level source files except generated artifacts.
                             Use for development only; default explicit list is safer.
  --dry-run                  Print actions without copying files.
  -h, --help                 Show this help.

Default copied files:
  main_pi_camera.py
  process_image_v2.py
  bm_serial.py
  spotter_time_sync.py
  run_capture_cycle.sh
  read_CPU_temp.py

camera_schedule.yaml is intentionally NOT copied by default because it is device-specific.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src)
      SRC="$2"
      shift 2
      ;;
    --dst)
      DST="$2"
      shift 2
      ;;
    --backup-dir)
      BACKUP_DIR="$2"
      shift 2
      ;;
    --include-schedule)
      INCLUDE_SCHEDULE="true"
      shift
      ;;
    --all-runtime-files)
      ALL_RUNTIME_FILES="true"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[DEPLOY][ERROR] Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log() {
  echo "[DEPLOY] $*"
}

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

if [[ ! -d "$SRC" ]]; then
  echo "[DEPLOY][ERROR] Source folder does not exist: $SRC" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME_VALUE="$(hostname 2>/dev/null || echo unknown_host)"

log "source=$SRC"
log "destination=$DST"
log "backup_dir=$BACKUP_DIR"
log "timestamp=$TS"
log "hostname=$HOSTNAME_VALUE"

run mkdir -p "$BACKUP_DIR"
run mkdir -p "$DST"

if [[ -d "$DST" ]]; then
  BACKUP_PATH="$BACKUP_DIR/BM_Devel_Pi_before_deploy_${HOSTNAME_VALUE}_${TS}.tgz"
  log "backing up current runtime to $BACKUP_PATH"
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] tar -czf '$BACKUP_PATH' -C '$(dirname "$DST")' '$(basename "$DST")'"
  else
    tar -czf "$BACKUP_PATH" -C "$(dirname "$DST")" "$(basename "$DST")"
  fi
fi

copy_file() {
  local name="$1"
  if [[ ! -f "$SRC/$name" ]]; then
    log "skip missing optional file: $name"
    return 0
  fi
  log "copy $name"
  run cp "$SRC/$name" "$DST/$name"
}

if [[ "$ALL_RUNTIME_FILES" == "true" ]]; then
  log "copy mode: all top-level runtime files, excluding generated artifacts"
  while IFS= read -r -d '' path; do
    name="$(basename "$path")"
    case "$name" in
      camera_schedule.yaml)
        if [[ "$INCLUDE_SCHEDULE" == "true" ]]; then
          copy_file "$name"
        else
          log "skip device-specific file by default: $name"
        fi
        ;;
      *.py|*.sh|*.md|*.txt|*.yaml|*.yml)
        copy_file "$name"
        ;;
      *)
        log "skip non-runtime top-level file: $name"
        ;;
    esac
  done < <(find "$SRC" -maxdepth 1 -type f -print0 | sort -z)
else
  log "copy mode: explicit approved runtime file list"
  copy_file "main_pi_camera.py"
  copy_file "process_image_v2.py"
  copy_file "bm_serial.py"
  copy_file "spotter_time_sync.py"
  copy_file "run_capture_cycle.sh"
  copy_file "read_CPU_temp.py"

  if [[ "$INCLUDE_SCHEDULE" == "true" ]]; then
    copy_file "camera_schedule.yaml"
  else
    log "skip camera_schedule.yaml by default; keep local device/test settings"
  fi
fi

if [[ -f "$DST/run_capture_cycle.sh" ]]; then
  log "chmod +x run_capture_cycle.sh"
  run chmod +x "$DST/run_capture_cycle.sh"
fi

# Record source commit in the runtime folder if this script is run from a git checkout.
if command -v git >/dev/null 2>&1 && git -C "$(dirname "$SRC")" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  SHA="$(git -C "$(dirname "$SRC")" rev-parse --short=12 HEAD 2>/dev/null || true)"
  if [[ -n "$SHA" ]]; then
    log "write software_sha.txt=$SHA"
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "[DRY-RUN] printf '%s\n' '$SHA' > '$DST/software_sha.txt'"
    else
      printf '%s\n' "$SHA" > "$DST/software_sha.txt"
    fi
  fi
fi

log "syntax check"
if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] cd '$DST' && /usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py spotter_time_sync.py bm_serial.py"
else
  cd "$DST"
  /usr/bin/python3 -m py_compile main_pi_camera.py process_image_v2.py spotter_time_sync.py bm_serial.py
fi

log "deployed files:"
if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] ls -lh '$DST'"
else
  ls -lh "$DST" | sed -n '1,80p'
fi

log "deploy complete"
log "next checks:"
log "  cd $DST && /usr/bin/python3 -u spotter_time_sync.py"
log "  cd $DST && /usr/bin/python3 -u main_pi_camera.py --transmit --resolution-key 480p --image-quality 10 --skip-time-window"
