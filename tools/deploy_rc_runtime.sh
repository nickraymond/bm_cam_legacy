#!/usr/bin/env bash
# deploy_rc_runtime.sh — install/update the progressive-JPEG RC runtime on a Pi.
#
# Repo path: tools/deploy_rc_runtime.sh
# Reads:     tools/rc_runtime_manifest.txt (the complete RC file set)
#
# One script, two jobs:
#   FRESH INSTALL (new unit):   ./tools/deploy_rc_runtime.sh --fresh --profile rc_field_template --install-cron
#   FIELD UPDATE (existing):    ./tools/deploy_rc_runtime.sh                 # config preserved
#
# Conservative by default:
#   - refuses to run while the boot cycle is ARMED in crontab (disarm first;
#     tools/rc_field_update.sh does that) and without PyYAML (Sprint26 S2)
#   - STAGES the new runtime in <dst>.next and checks it BEFORE touching the
#     live runtime (Sprint26 S2f, DESIGN_supervisor.md §8.3):
#       py_compile of every shipped module
#       a YAML that still enables media_gid is refused
#       print-config parity: the old runtime and the staged one resolve the
#         unit's config identically (--accept-print-config-diff to override,
#         only for a stage whose print-config text changes on purpose)
#       on a config-v2 unit (camera_config.yaml present): the v2 file loads
#         strictly and resolves to the same effective values as the v1 file
#   - tars a backup of the runtime dir, then copies ONLY the manifest files
#   - never touches camera_schedule.yaml / camera_config.yaml unless --fresh/--profile
#   - never touches crontab unless --install-cron is given
#   - writes software_sha.txt, appends deploy_history.log (with the config hash
#     on a v2 unit) and, on a v2 unit, a `deploy` line in config_journal.jsonl
#
# Typical bootstrap on a brand-new unit (after flashing + Tailscale):
#   git clone https://github.com/nickraymond/bm_cam_legacy.git ~/repos/bm_cam_legacy
#   cd ~/repos/bm_cam_legacy && git checkout <release-tag>
#   ./tools/deploy_rc_runtime.sh --fresh --profile rc_field_template --install-cron
#
# Known limitations: run ON the Pi from a git checkout; profiles live in
# device_profiles/<name>/camera_schedule.yaml (v1; migrate with
# tools/config_migrate_v1_v2.py). Test hooks: BMCAM_PYTHON (interpreter),
# BMCAM_CRONTAB_FILE (read instead of `crontab -l`), BMCAM_SERVICE_KEY_DIR.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
DST="/home/pi/BM_Devel_Pi"
BACKUP_DIR="/home/pi/backups"
PROFILE=""
FRESH="false"
INSTALL_CRON="false"
DRY_RUN="false"
ACCEPT_PC_DIFF="false"
CREATE_KEY="false"
PY="${BMCAM_PYTHON:-/usr/bin/python3}"
KEY_DIR="${BMCAM_SERVICE_KEY_DIR:-/home/pi/.config/nereus}"

usage() {
  cat <<'EOF'
Usage: tools/deploy_rc_runtime.sh [options]

Options:
  --repo PATH          Repo checkout root. Default: parent of this script.
  --dst PATH           Live runtime folder. Default: /home/pi/BM_Devel_Pi
  --backup-dir PATH    Backup folder. Default: /home/pi/backups
  --fresh              Fresh install: requires --profile; replaces any existing
                       camera_schedule.yaml (after backing it up); creates the
                       unit's service key if absent.
  --profile NAME       Install device_profiles/NAME/camera_schedule.yaml.
                       Without --fresh, refuses to overwrite an existing YAML.
  --install-cron       Install the RC @reboot crontab line (backs up crontab).
  --accept-print-config-diff
                       Install even though the staged runtime's --print-config
                       text differs from the old one (a stage that changes it on
                       purpose). The diff is printed either way.
  --create-service-key Create the unit's service key if absent (existing units;
                       --fresh does it anyway). Never overwrites a key.
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
    --accept-print-config-diff) ACCEPT_PC_DIFF="true"; shift ;;
    --create-service-key) CREATE_KEY="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[RC-DEPLOY][ERROR] Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

MANIFEST="$REPO/tools/rc_runtime_manifest.txt"
STAGE="${DST}.next"
log() { echo "[RC-DEPLOY] $*"; }
die() { echo "[RC-DEPLOY][ERROR] $*" >&2; exit 1; }
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
log "destination=$DST (staging in $STAGE)"
log "mode=$([[ "$FRESH" == "true" ]] && echo fresh-install || echo field-update) profile=${PROFILE:-none} cron=$INSTALL_CRON"
log "timestamp=$TS hostname=$HOSTNAME_VALUE python=$PY"

# ---- preflight (Sprint26 S2f) ---------------------------------------------
# PyYAML is a hard dependency: without it three v1 loaders silently read
# defaults (network_type 0x01 = satellite fallback, 300/5.0 pacing).
"$PY" -c "import yaml" 2>/dev/null \
  || die "PyYAML is missing for $PY (sudo apt install python3-yaml); refusing to deploy"
log "preflight: PyYAML present"
# An ARMED unit can boot into a half-copied runtime or halt mid-deploy.
if [[ -n "${BMCAM_CRONTAB_FILE:-}" ]]; then CRON_NOW="$(cat "$BMCAM_CRONTAB_FILE" 2>/dev/null || true)"
else CRON_NOW="$(crontab -l 2>/dev/null || true)"; fi
if printf '%s\n' "$CRON_NOW" | grep -Eq '^[[:space:]]*@reboot[^#]*rc_run_capture_cycle\.sh'; then
  die "the boot cycle is ARMED in crontab — disarm first (tools/rc_field_update.sh does it; bmcam-field-update skill)"
fi
log "preflight: boot cycle not armed"

# ---- stage the new runtime in <dst>.next ------------------------------------
COPIED_PY=()
DEST_NAMES=()
MISSING=0
if [[ "$DRY_RUN" != "true" ]]; then rm -rf "$STAGE"; mkdir -p "$STAGE"; fi
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
  # A dest_name may contain subdirectories (e.g. the src reference images
  # install as reference_images/prepared/<scene>/...). Create the parent so
  # the runtime keeps the repo-relative layout command_tables.py expects.
  dest_dir="$(dirname "$STAGE/$dest_name")"
  [[ "$dest_dir" != "$STAGE" ]] && run mkdir -p "$dest_dir"
  run cp "$src" "$STAGE/$dest_name"
  [[ "$dest_name" == *.sh ]] && run chmod +x "$STAGE/$dest_name"
  [[ "$dest_name" == *.py ]] && COPIED_PY+=("$dest_name")
  DEST_NAMES+=("$dest_name")
done < "$MANIFEST"
if [[ "$MISSING" -gt 0 ]]; then
  echo "[RC-DEPLOY][ERROR] $MISSING manifest file(s) missing — aborting before config/cron steps" >&2
  exit 1
fi
log "staged ${#DEST_NAMES[@]} files in $STAGE"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] cd $STAGE && $PY -m py_compile ${COPIED_PY[*]}"
  echo "[DRY-RUN] media_gid refusal, print-config parity, v2 strict load + migration parity"
else
  # Syntax gate (Sprint26 S1: moved from every boot to deploy time).
  log "syntax check (py_compile ${#COPIED_PY[@]} files, staged)"
  (cd "$STAGE" && "$PY" -m py_compile "${COPIED_PY[@]}") \
    || die "a shipped module does not compile; nothing installed ($STAGE kept for inspection)"

  CHECK_YAML="$DST/camera_schedule.yaml"
  [[ -n "$PROFILE" ]] && CHECK_YAML="$PROFILE_YAML"
  if [[ -f "$CHECK_YAML" ]]; then
    # REVIEW R1: a YAML that still enables the retired media_gid is refused here
    # (the runtime only warns at boot, so a boot never fails on it).
    (cd "$STAGE" && "$PY" -c "import sys, rc_media_key; sys.exit(1 if rc_media_key.warn_retired_media_gid(sys.argv[1]) else 0)" "$CHECK_YAML") \
      || die "$CHECK_YAML enables media_gid (retired by wire rev 5): remove the island first; nothing installed"
  fi

  WORK="$STAGE/.deploy_check"
  mkdir -p "$WORK"
  YAML="$DST/camera_schedule.yaml"
  if [[ -z "$PROFILE" && -f "$YAML" && -f "$DST/rc_progressive_jpeg.py" ]]; then
    # print-config parity: the old and the staged runtime, each resolving the
    # unit's config the way it would at boot (a pre-S2 runtime reads the v1
    # file; an S2+ one reads camera_config.yaml when present). Paths that
    # differ by design are normalised (tools/config_parity.py).
    (cd "$DST" && "$PY" rc_progressive_jpeg.py --print-config --config-path "$YAML") > "$WORK/pc_old.txt" 2>&1 \
      || log "WARN: old runtime --print-config exited non-zero (see $WORK/pc_old.txt)"
    (cd "$STAGE" && "$PY" rc_progressive_jpeg.py --print-config --config-path "$YAML") > "$WORK/pc_new.txt" 2>&1 \
      || die "staged runtime --print-config failed on $YAML (see $WORK/pc_new.txt); nothing installed"
    if "$PY" "$REPO/tools/config_parity.py" text "$WORK/pc_old.txt" "$WORK/pc_new.txt"; then
      log "print-config parity OK (old vs staged runtime, same unit config)"
    elif [[ "$ACCEPT_PC_DIFF" == "true" ]]; then
      log "print-config differs (above) — ACCEPTED by --accept-print-config-diff"
    else
      die "print-config differs between the old and the staged runtime (diff above); nothing installed. Re-run with --accept-print-config-diff only if that change is intended."
    fi
  fi
  if [[ -z "$PROFILE" && -f "$DST/camera_config.yaml" ]]; then
    # Config v2 unit: the v2 file must load strictly, and resolve to the same
    # effective values as the v1 file it was migrated from (PLAN_S2.md G2).
    (cd "$STAGE" && "$PY" -c "import sys, config_v2; c = config_v2.load_config(sys.argv[1], strict=True); print('[RC-DEPLOY] camera_config.yaml strict load OK, base hash', config_v2.config_hash(c.base))" "$DST/camera_config.yaml") \
      || die "camera_config.yaml does not load strictly (above); nothing installed"
    (cd "$STAGE" && "$PY" rc_progressive_jpeg.py --print-config --json --config-path "$YAML" --config-format v1) > "$WORK/v1.json" 2>&1 \
      || die "staged runtime --print-config --json (v1) failed (see $WORK/v1.json)"
    (cd "$STAGE" && "$PY" rc_progressive_jpeg.py --print-config --json --config-path "$YAML" --config-format v2) > "$WORK/v2.json" 2>&1 \
      || die "staged runtime --print-config --json (v2) failed (see $WORK/v2.json)"
    "$PY" "$REPO/tools/config_parity.py" json "$WORK/v1.json" "$WORK/v2.json" \
      || die "camera_config.yaml does not resolve like camera_schedule.yaml (above); nothing installed"
    log "config v2 parity OK (v2 file vs the v1 file it came from)"
  fi
fi

run mkdir -p "$BACKUP_DIR" "$DST"

# ---- backup ---------------------------------------------------------------
if [[ -d "$DST" ]] && [[ -n "$(ls -A "$DST" 2>/dev/null)" ]]; then
  BACKUP_PATH="$BACKUP_DIR/BM_Devel_Pi_before_rc_deploy_${HOSTNAME_VALUE}_${TS}.tgz"
  log "backing up runtime code/config to $BACKUP_PATH (images/buffers/logs/videos excluded)"
  # videos/ excluded 2026-08-18: a video-mode unit carries GBs of clips
  # (bmcam003: 2.3 GB tarred for 13+ min on a Zero 2W). Clips are data
  # with their own lifecycle (ring buffer), not runtime code/config.
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] tar czf $BACKUP_PATH --exclude images --exclude buffer --exclude cron_logs --exclude videos --exclude __pycache__ -C $(dirname "$DST") $(basename "$DST")"
  else
    tar czf "$BACKUP_PATH" \
      --exclude "$(basename "$DST")/images" --exclude "$(basename "$DST")/buffer" \
      --exclude "$(basename "$DST")/cron_logs" --exclude "$(basename "$DST")/__pycache__" \
      --exclude "$(basename "$DST")/videos" \
      -C "$(dirname "$DST")" "$(basename "$DST")"
  fi
  log "restore command: tar xzf $BACKUP_PATH -C $(dirname "$DST")"
fi

# ---- install the staged files (only after every check passed) --------------
for dest_name in "${DEST_NAMES[@]}"; do
  dest_dir="$(dirname "$DST/$dest_name")"
  [[ "$dest_dir" != "$DST" ]] && run mkdir -p "$dest_dir"
  log "copy $dest_name"
  run cp -p "$STAGE/$dest_name" "$DST/$dest_name"
done
run rm -rf "$STAGE"
log "installed ${#DEST_NAMES[@]} files; staging dir removed"

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
  if [[ -f "$DST/camera_config.yaml" ]]; then
    log "WARN: $DST/camera_config.yaml (config v2) exists and still governs this unit;"
    log "      migrate the new profile: tools/config_migrate_v1_v2.py --app $DST --write --force"
  fi
else
  log "camera_schedule.yaml untouched (device-specific; use --profile to install one)"
fi

# ---- service key (Sprint26 S2f, DESIGN §6.3; used by signed commands in S4) --
if [[ "$FRESH" == "true" || "$CREATE_KEY" == "true" ]]; then
  KEY="$KEY_DIR/service.key"
  if [[ -f "$KEY" ]]; then
    log "service key present ($KEY), not replaced"
  elif [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] create $KEY (32 random bytes, hex, mode 600)"
  else
    mkdir -p "$KEY_DIR" && chmod 700 "$KEY_DIR"
    (umask 077 && "$PY" -c "import secrets; print(secrets.token_hex(32))" > "$KEY")
    chmod 600 "$KEY"
    log "service key CREATED: $KEY (mode 600). Copy it to Nereus NOW, then keep it off the repo:"
    log "  scp pi@$HOSTNAME_VALUE:$KEY ~/.config/nereus/unit_keys/$HOSTNAME_VALUE.key && chmod 600 ~/.config/nereus/unit_keys/$HOSTNAME_VALUE.key"
  fi
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
CFG_HASH=""
if [[ "$DRY_RUN" != "true" && -f "$DST/camera_config.yaml" ]]; then
  CFG_HASH="$(cd "$DST" && "$PY" -c "
import sys, config_v2
v1, v2 = sys.argv[1], sys.argv[2]
b = config_v2.load_for_boot(v1, v2, sys.argv[3])
print(b.config.hash if b.config else 'fallback:' + str(b.level))" \
    "$DST/camera_schedule.yaml" "$DST/camera_config.yaml" "$DST/camera_config.lkg.json" 2>/dev/null || echo unknown)"
  log "config v2 effective hash: $CFG_HASH"
fi
if command -v git >/dev/null 2>&1 && git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  SHA="$(git -C "$REPO" rev-parse --short=12 HEAD 2>/dev/null || true)"
  if [[ -n "$SHA" ]]; then
    log "write software_sha.txt=$SHA"
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "[DRY-RUN] printf '$SHA' > $DST/software_sha.txt"
    else
      printf '%s\n' "$SHA" > "$DST/software_sha.txt"
      printf '%s %s mode=%s profile=%s cron=%s%s\n' "$TS" "$SHA" \
        "$([[ "$FRESH" == "true" ]] && echo fresh || echo update)" "${PROFILE:-none}" "$INSTALL_CRON" \
        "$([[ -n "$CFG_HASH" ]] && echo " cfg=$CFG_HASH")" \
        >> "$DST/deploy_history.log"
      if [[ -n "$CFG_HASH" ]]; then
        (cd "$DST" && "$PY" -c "
import sys, config_journal, config_v2
state = sys.argv[1]
config_journal.append(config_journal.path_beside(state), 'deploy', key='*', new=sys.argv[2], h=sys.argv[3])" \
          "$DST/bm_command_state_v2.json" "$SHA" "$CFG_HASH") || log "WARN: journal append failed"
      fi
    fi
  fi
fi

# Config smoke test — only where the runtime deps exist (always true on a Pi).
if [[ "$DRY_RUN" != "true" ]] && [[ -f "$DST/camera_schedule.yaml" ]] \
   && "$PY" -c "import serial" >/dev/null 2>&1; then
  log "config smoke test: rc_progressive_jpeg.py --print-config"
  (cd "$DST" && "$PY" rc_progressive_jpeg.py --print-config --config-path "$DST/camera_schedule.yaml")
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
