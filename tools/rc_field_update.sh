#!/usr/bin/env bash
# rc_field_update.sh — safe field update of the RC runtime on an armed unit.
#
# Repo path: tools/rc_field_update.sh
# Wraps:     tools/deploy_rc_runtime.sh (untouched, known-good)
#
# Purpose: update a unit that is LIVE (cron-armed, possibly halt-armed)
# without leaving it mid-surgery: disarm -> sync repo -> deploy runtime ->
# patch bm_serial values -> verify UART -> validate -> re-arm.
#
# Run ON the Pi (over SSH), from anywhere:
#   bash ~/repos/bm_cam_legacy/tools/rc_field_update.sh [options]
#
# Options:
#   --repo PATH        repo checkout (default: this script's repo)
#   --ref  REF         git ref to deploy (default: development)
#   --profile NAME     device profile to diff/patch values from
#                      (default: hostname, e.g. bmcam000)
#   --leave-disarmed   do NOT restore crontab at the end (bench/dev mode)
#   --skip-repo-sync   deploy from the checkout as-is (no fetch/checkout)
#   --dry-run          print actions without changing anything
#
# What it patches in the deployed camera_schedule.yaml (values ONLY, comments
# and device-specific config preserved): bm_serial.image_buffer_size,
# bm_serial.image_transmit_delay_seconds — taken from the device profile.
# Anything else that differs between deployed YAML and profile is REPORTED,
# never changed.
#
# Outputs: stage-by-stage log, final PASS/FAIL summary, explicit rollback
# commands. Exits nonzero on any gate failure with the unit left DISARMED
# (safe: it will not cycle/halt until re-armed) — re-run after fixing.
#
# Known limitations: does not edit boot config. If the UART gate fails
# (no /dev/ttyAMA0 or console on serial), it prints the fix commands and
# aborts — apply them manually per sprint09 DEV_LOG / bmcam-provision skill.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
REF="development"
PROFILE="$(hostname 2>/dev/null || echo unknown)"
LEAVE_DISARMED="false"
SKIP_REPO_SYNC="false"
DRY_RUN="false"
DST="/home/pi/BM_Devel_Pi"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --leave-disarmed) LEAVE_DISARMED="true"; shift ;;
    --skip-repo-sync) SKIP_REPO_SYNC="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "[FIELD-UPDATE][ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() { echo "[FIELD-UPDATE] $*"; }
fail() { echo "[FIELD-UPDATE][FAIL] $*" >&2; echo "[FIELD-UPDATE] unit left DISARMED — fix and re-run" >&2; exit 1; }
run() { if [[ "$DRY_RUN" == "true" ]]; then echo "[DRY-RUN] $*"; else "$@"; fi; }

CRON_BACKUP="/home/pi/crontab_before_field_update_${TS}.txt"
YAML="$DST/camera_schedule.yaml"
YAML_BACKUP="$DST/camera_schedule.yaml.before_field_update_${TS}"

log "=== stage 0: preflight ==="
log "repo=$REPO ref=$REF profile=$PROFILE dry_run=$DRY_RUN"
[[ -d "$REPO/.git" ]] || fail "no git repo at $REPO (clone it first: git clone https://github.com/nickraymond/bm_cam_legacy.git)"
[[ -f "$YAML" ]] || fail "no deployed YAML at $YAML — this is not an installed unit; use deploy_rc_runtime.sh --fresh"
if pgrep -f 'rc_progressive_jpeg.py|rc_run_capture_cycle.sh|main_pi_camera.py' >/dev/null; then
  fail "a camera cycle is running — wait for it to finish (or it will halt the box; catch the next wake window)"
fi
DISK_FREE_MB=$(df -m "$DST" | awk 'NR==2 {print $4}')
[[ "$DISK_FREE_MB" -gt 500 ]] || fail "only ${DISK_FREE_MB}MB free on $DST"
log "preflight OK (${DISK_FREE_MB}MB free, no cycle running)"

log "=== stage 1: disarm (crontab backup + disable @reboot RC line) ==="
if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] crontab -l > $CRON_BACKUP; comment @reboot rc_run_capture_cycle.sh line"
else
  (crontab -l 2>/dev/null || true) > "$CRON_BACKUP"
  (crontab -l 2>/dev/null || true) \
    | sed 's|^@reboot \(.*rc_run_capture_cycle\.sh\)$|# DISABLED field_update '"$TS"': @reboot \1|' \
    | crontab -
  log "crontab backed up -> $CRON_BACKUP; RC @reboot line disabled"
fi

log "=== stage 2: repo sync ==="
OLD_SHA="$(git -C "$REPO" rev-parse --short=12 HEAD 2>/dev/null || echo none)"
if [[ "$SKIP_REPO_SYNC" == "true" ]]; then
  log "skipped (--skip-repo-sync); deploying from $OLD_SHA as-is"
else
  run git -C "$REPO" fetch origin || fail "git fetch failed (network?)"
  run git -C "$REPO" checkout "$REF" || fail "git checkout $REF failed (local changes?)"
  run git -C "$REPO" pull --ff-only origin "$REF" || fail "git pull --ff-only failed (diverged checkout)"
fi
NEW_SHA="$(git -C "$REPO" rev-parse --short=12 HEAD 2>/dev/null || echo none)"
log "repo: $OLD_SHA -> $NEW_SHA ($REF)"

log "=== stage 3: runtime deploy (deploy_rc_runtime.sh, YAML+cron untouched) ==="
run bash "$REPO/tools/deploy_rc_runtime.sh" --repo "$REPO" --dst "$DST" \
  $([[ "$DRY_RUN" == "true" ]] && echo --dry-run) \
  || fail "deploy_rc_runtime.sh failed — runtime backup + restore command are in its output above"

log "=== stage 4: bm_serial value patch (from device profile) ==="
PROFILE_YAML="$REPO/device_profiles/$PROFILE/camera_schedule.yaml"
[[ -f "$PROFILE_YAML" ]] || fail "no profile at device_profiles/$PROFILE/ (pass --profile NAME)"
get_val() { # get_val FILE KEY -> value of "  KEY: value" inside the bm_serial block
  sed -n '/^bm_serial:/,/^[a-z_]/p' "$1" | sed -n "s/^  $2: *//p" | head -1
}
for key in image_buffer_size image_transmit_delay_seconds network_type; do
  DEPLOYED_VAL="$(get_val "$YAML" "$key")"
  PROFILE_VAL="$(get_val "$PROFILE_YAML" "$key")"
  log "  $key: deployed=$DEPLOYED_VAL profile=$PROFILE_VAL"
done
# report non-bm_serial drift, never patch it
if ! diff -q "$YAML" "$PROFILE_YAML" >/dev/null 2>&1; then
  log "  NOTE: deployed YAML differs from profile beyond values printed above —"
  log "  full diff (deployed vs profile) for the record, NOT auto-applied:"
  diff "$YAML" "$PROFILE_YAML" | sed 's/^/  | /' || true
fi
if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] patch image_buffer_size + image_transmit_delay_seconds in $YAML"
else
  cp "$YAML" "$YAML_BACKUP"
  NEW_CHUNK="$(get_val "$PROFILE_YAML" image_buffer_size)"
  NEW_DELAY="$(get_val "$PROFILE_YAML" image_transmit_delay_seconds)"
  [[ -n "$NEW_CHUNK" && -n "$NEW_DELAY" ]] || fail "profile bm_serial values missing/unparseable"
  sed -i "/^bm_serial:/,/^[a-z_]/{s/^  image_buffer_size: .*/  image_buffer_size: $NEW_CHUNK/; s/^  image_transmit_delay_seconds: .*/  image_transmit_delay_seconds: $NEW_DELAY/;}" "$YAML"
  log "patched: image_buffer_size=$NEW_CHUNK image_transmit_delay_seconds=$NEW_DELAY (backup: $YAML_BACKUP)"
fi

log "=== stage 5: UART hygiene gate ==="
if [[ "$(readlink /dev/serial0 2>/dev/null)" != "ttyAMA0" ]]; then
  log "  /dev/serial0 -> $(readlink /dev/serial0 2>/dev/null || echo MISSING) (want ttyAMA0)"
  log "  FIX (backup first, then reboot): add 'dtoverlay=disable-bt' to /boot/firmware/config.txt;"
  log "  remove 'console=serial0,115200' from /boot/firmware/cmdline.txt (see sprint09 DEV_LOG)"
  fail "PL011 not on the header — BM transmit cannot work"
fi
if grep -q "console=serial0\|console=ttyS0\|console=ttyAMA0" /proc/cmdline; then
  fail "kernel serial console is on the UART — remove console=serial0,115200 from cmdline.txt"
fi
log "UART OK: serial0 -> ttyAMA0, no kernel console on the link"

log "=== stage 6: validation ladder (no quota) ==="
if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] print-config + BristlemouthSerial UART open test"
else
  (cd "$DST" && /usr/bin/python3 rc_progressive_jpeg.py --print-config --config-path "$YAML") \
    || fail "print-config failed after update"
  (cd "$DST" && /usr/bin/python3 -c "
import bm_serial
port, baud = bm_serial.load_uart_config()
b = bm_serial.BristlemouthSerial()
print(f'[FIELD-UPDATE] UART open OK: {b.uart.port} @ {b.uart.baudrate}, network {b.describe_network_type()}')
b.deinit()") || fail "BristlemouthSerial UART open failed"
fi
log "validation OK (real-transmit check is a manual step — see summary)"

log "=== stage 7: re-arm ==="
if [[ "$LEAVE_DISARMED" == "true" ]]; then
  log "crontab left DISARMED (--leave-disarmed); restore with: crontab $CRON_BACKUP"
elif [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] crontab $CRON_BACKUP"
else
  crontab "$CRON_BACKUP"
  log "crontab restored from $CRON_BACKUP"
fi
HALT_STATE="$(sed -n '/^power_halt:/,/^[a-z_]/p' "$YAML" | grep -E 'enabled|dry_run' | xargs)"
log "power_halt state (verify matches intent for this unit): $HALT_STATE"

log "=== SUMMARY: PASS ==="
log "sha: $OLD_SHA -> $NEW_SHA | values: chunk=$(get_val "$YAML" image_buffer_size) delay=$(get_val "$YAML" image_transmit_delay_seconds)"
log "rollback: crontab $CRON_BACKUP; cp $YAML_BACKUP $YAML; runtime tar in /home/pi/backups (see deploy output)"
log "recommended live check (SPENDS QUOTA; with power_halt enabled the box HALTS after):"
log "  cd $DST && python3 -u rc_progressive_jpeg.py --transmit --skip-time-window"
