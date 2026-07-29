#!/usr/bin/env bash
# deploy_rc_runtime.sh — install/update the progressive-JPEG RC runtime on a Pi.
#
# Repo path: tools/deploy_rc_runtime.sh
# Reads:     tools/rc_runtime_manifest.txt (the complete RC file set)
#
# One script, two jobs:
#   FRESH INSTALL (new unit):   ./tools/deploy_rc_runtime.sh --fresh --profile rc_field_template --install-cron
#   FIELD UPDATE (existing):    ./tools/deploy_rc_runtime.sh                 # config preserved, HEIC files left for rollback
#
# Conservative by default (mirrors deploy_runtime.sh):
#   - tars a backup of the runtime dir before touching it
#   - copies ONLY the manifest files
#   - never touches camera_schedule.yaml unless --fresh/--profile is given
#   - never touches crontab unless --install-cron is given
#   - py_compile gate + optional --print-config smoke after copy
#   - writes software_sha.txt and appends deploy_history.log
#
# Typical bootstrap on a brand-new unit (after flashing + Tailscale):
#   git clone https://github.com/nickraymond/bm_cam_legacy.git ~/repos/bm_cam_legacy
#   cd ~/repos/bm_cam_legacy && git checkout <release-tag>
#   ./tools/deploy_rc_runtime.sh --fresh --profile rc_field_template --install-cron
#
# Known limitations: run ON the Pi from a git checkout; profiles live in
# device_profiles/<name>/camera_schedule.yaml.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
DST="/home/pi/BM_Devel_Pi"
BACKUP_DIR="/home/pi/backups"
PROFILE=""
FRESH="false"
INSTALL_CRON="false"
DRY_RUN="false"

usage() {
  cat <<'EOF'
Usage: tools/deploy_rc_runtime.sh [options]

Options:
  --repo PATH          Repo checkout root. Default: parent of this script.
  --dst PATH           Live runtime folder. Default: /home/pi/BM_Devel_Pi
  --backup-dir PATH    Backup folder. Default: /home/pi/backups
  --fresh              Fresh install: requires --profile; replaces any existing
                       camera_schedule.yaml (after backing it up).
  --profile NAME       Install device_profiles/NAME/camera_schedule.yaml.
                       Without --fresh, refuses to overwrite an existing YAML.
  --install-cron       Install the RC @reboot crontab line (backs up crontab,
                       comments out any active HEIC run_capture_cycle.sh line).
  --dry-run            Print actions without changing anything.
  -h, --help           This help.

The file set comes from tools/rc_runtime_manifest.txt — edit that, not this script.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --dst) DST="$2"; shift 2 ;;
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    --fresh) FRESH="true"; shift ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --install-cron) INSTALL_CRON="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[RC-DEPLOY][ERROR] Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

MANIFEST="$REPO/tools/rc_runtime_manifest.txt"
log() { echo "[RC-DEPLOY] $*"; }
run() { if [[ "$DRY_RUN" == "true" ]]; then echo "[DRY-RUN] $*"; else "$@"; fi; }

[[ -f "$MANIFEST" ]] || { echo "[RC-DEPLOY][ERROR] manifest not found: $MANIFEST" >&2; exit 1; }
if [[ "$FRESH" == "true" && -z "$PROFILE" ]]; then
  echo "[RC-DEPLOY][ERROR] --fresh requires --profile NAME" >&2; exit 2
fi
if [[ -n "$PROFILE" ]]; then
  PROFILE_YAML="$REPO/device_profiles/$PROFILE/camera_schedule.yaml"
  [[ -f "$PROFILE_YAML" ]] || { echo "[RC-DEPLOY][ERROR] profile not found: $PROFILE_YAML" >&2; exit 1; }
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME_VALUE="$(hostname 2>/dev/null || echo unknown_host)"
log "repo=$REPO"
log "manifest=$MANIFEST"
log "destination=$DST"
log "mode=$([[ "$FRESH" == "true" ]] && echo fresh-install || echo field-update) profile=${PROFILE:-none} cron=$INSTALL_CRON"
log "timestamp=$TS hostname=$HOSTNAME_VALUE"

run mkdir -p "$BACKUP_DIR" "$DST"

# ---- backup ---------------------------------------------------------------
if [[ -d "$DST" ]] && [[ -n "$(ls -A "$DST" 2>/dev/null)" ]]; then
  BACKUP_PATH="$BACKUP_DIR/BM_Devel_Pi_before_rc_deploy_${HOSTNAME_VALUE}_${TS}.tgz"
  log "backing up runtime code/config to $BACKUP_PATH (images/buffers/logs excluded)"
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] tar czf $BACKUP_PATH --exclude images --exclude buffer --exclude cron_logs --exclude __pycache__ -C $(dirname "$DST") $(basename "$DST")"
  else
    tar czf "$BACKUP_PATH" \
      --exclude "$(basename "$DST")/images" --exclude "$(basename "$DST")/buffer" \
      --exclude "$(basename "$DST")/cron_logs" --exclude "$(basename "$DST")/__pycache__" \
      -C "$(dirname "$DST")" "$(basename "$DST")"
  fi
  log "restore command: tar xzf $BACKUP_PATH -C $(dirname "$DST")"
fi

# ---- copy the manifest ----------------------------------------------------
COPIED_PY=()
MISSING=0
while IFS= read -r raw; do
  line="${raw%%#*}"; line="$(echo "$line" | xargs || true)"
  [[ -z "$line" ]] && continue
  src_rel="${line%% -> *}"
  if [[ "$line" == *" -> "* ]]; then dest_name="${line##* -> }"; else dest_name="$(basename "$src_rel")"; fi
  src="$REPO/$src_rel"
  if [[ ! -f "$src" ]]; then
    echo "[RC-DEPLOY][ERROR] manifest file missing in repo: $src_rel" >&2
    MISSING=$((MISSING + 1)); continue
  fi
  log "copy $src_rel -> $dest_name"
  # A dest_name may contain subdirectories (e.g. the src reference images
  # install as reference_images/prepared/<scene>/...). Create the parent so
  # the runtime keeps the repo-relative layout command_tables.py expects.
  dest_dir="$(dirname "$DST/$dest_name")"
  [[ "$dest_dir" != "$DST" ]] && run mkdir -p "$dest_dir"
  run cp "$src" "$DST/$dest_name"
  [[ "$dest_name" == *.py ]] && COPIED_PY+=("$dest_name")
  [[ "$dest_name" == *.sh ]] && run chmod +x "$DST/$dest_name"
done < "$MANIFEST"
if [[ "$MISSING" -gt 0 ]]; then
  echo "[RC-DEPLOY][ERROR] $MISSING manifest file(s) missing — aborting before config/cron steps" >&2
  exit 1
fi

# ---- device config --------------------------------------------------------
if [[ -n "$PROFILE" ]]; then
  if [[ -f "$DST/camera_schedule.yaml" && "$FRESH" != "true" ]]; then
    echo "[RC-DEPLOY][ERROR] $DST/camera_schedule.yaml exists; refusing to overwrite without --fresh" >&2
    exit 1
  fi
  if [[ -f "$DST/camera_schedule.yaml" ]]; then
    log "backing up existing camera_schedule.yaml -> camera_schedule.yaml.before_rc_deploy_${TS}"
    run cp "$DST/camera_schedule.yaml" "$DST/camera_schedule.yaml.before_rc_deploy_${TS}"
  fi
  log "installing profile YAML: device_profiles/$PROFILE/camera_schedule.yaml"
  run cp "$PROFILE_YAML" "$DST/camera_schedule.yaml"
else
  log "camera_schedule.yaml untouched (device-specific; use --profile to install one)"
fi

# ---- crontab --------------------------------------------------------------
RC_CRON_LINE="@reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock $DST/rc_run_capture_cycle.sh"
if [[ "$INSTALL_CRON" == "true" ]]; then
  CRON_BACKUP="$BACKUP_DIR/crontab_before_rc_deploy_${HOSTNAME_VALUE}_${TS}.txt"
  log "backing up crontab to $CRON_BACKUP"
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] crontab -l > $CRON_BACKUP; comment HEIC @reboot line; ensure: $RC_CRON_LINE"
  else
    (crontab -l 2>/dev/null || true) > "$CRON_BACKUP"
    NEW_CRON="$( (crontab -l 2>/dev/null || true) \
      | sed 's|^@reboot \(.*run_capture_cycle\.sh\)$|# DISABLED by rc deploy '"$TS"': @reboot \1|' \
      | grep -vF "$RC_CRON_LINE" || true )"
    { [[ -n "$NEW_CRON" ]] && printf '%s\n' "$NEW_CRON"; printf '%s\n' "$RC_CRON_LINE"; } | crontab -
    log "crontab installed: $RC_CRON_LINE"
  fi
else
  log "crontab untouched (use --install-cron to arm the boot cycle)"
fi

# ---- record + verify ------------------------------------------------------
if command -v git >/dev/null 2>&1 && git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  SHA="$(git -C "$REPO" rev-parse --short=12 HEAD 2>/dev/null || true)"
  if [[ -n "$SHA" ]]; then
    log "write software_sha.txt=$SHA"
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "[DRY-RUN] printf '$SHA' > $DST/software_sha.txt"
    else
      printf '%s\n' "$SHA" > "$DST/software_sha.txt"
      printf '%s %s mode=%s profile=%s cron=%s\n' "$TS" "$SHA" \
        "$([[ "$FRESH" == "true" ]] && echo fresh || echo update)" "${PROFILE:-none}" "$INSTALL_CRON" \
        >> "$DST/deploy_history.log"
    fi
  fi
fi

log "syntax check (py_compile ${#COPIED_PY[@]} files)"
if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] cd $DST && python3 -m py_compile ${COPIED_PY[*]}"
else
  (cd "$DST" && /usr/bin/python3 -m py_compile "${COPIED_PY[@]}")
fi

# Config smoke test — only where the runtime deps exist (always true on a Pi).
if [[ "$DRY_RUN" != "true" ]] && [[ -f "$DST/camera_schedule.yaml" ]] \
   && /usr/bin/python3 -c "import serial" >/dev/null 2>&1; then
  log "config smoke test: rc_progressive_jpeg.py --print-config"
  (cd "$DST" && /usr/bin/python3 rc_progressive_jpeg.py --print-config --config-path "$DST/camera_schedule.yaml")
else
  log "config smoke test skipped (dry-run, no YAML yet, or pyserial unavailable off-device)"
fi

# UART transmit-capable check (on-Pi only) — bm_serial.py needs /dev/serial0
# -> ttyAMA0 (PL011). A fresh OS image leaves the PL011 on Bluetooth and a
# kernel console on the pins; any BM transmit then crashes on port open
# (bmcam003, Sprint09). Warn loudly here; the ladder check hard-fails.
if [[ "$DRY_RUN" != "true" ]] && [[ -e /proc/device-tree/model ]]; then
  if [[ "$(readlink /dev/serial0 2>/dev/null || true)" != "ttyAMA0" ]] \
     || grep -qE 'console=(serial0|ttyAMA0|ttyS0)' /proc/cmdline; then
    echo "[RC-DEPLOY][WARN] UART is NOT BM-transmit-capable (/dev/serial0 must -> ttyAMA0, no serial console)" >&2
    echo "[RC-DEPLOY][WARN] fix: $REPO/tools/setup_bm_uart.sh, reboot, then tools/setup_bm_uart.sh --check" >&2
  else
    log "UART check: /dev/serial0 -> ttyAMA0, no serial console (BM-transmit-capable)"
  fi
fi

log "deploy complete"
log "next (new unit validation ladder — NOTE: with power_halt enabled, ANY cycle HALTS the box at cycle end):"
log "  cd $REPO && ./tools/setup_bm_uart.sh --check   # UART transmit-capable gate"
log "  cd $DST && python3 rc_progressive_jpeg.py --print-config"
log "  cd $DST && python3 rc_progressive_jpeg.py --capture-only"
log "  cd $DST && python3 rc_progressive_jpeg.py --compress-only <native.jpg>"
log "  cd $DST && python3 -u rc_progressive_jpeg.py --transmit"
