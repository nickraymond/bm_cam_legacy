#!/usr/bin/env bash
# setup_bm_uart.sh — configure the Pi UART for the Bristlemouth serial link.
#
# Repo path: tools/setup_bm_uart.sh    Run ON the Pi (Zero 2 W / any BT Pi).
#
# Why: bm_serial.py hardcodes /dev/ttyAMA0 @ 115200 (the PL011). A fresh
# Raspberry Pi OS image binds the PL011 to Bluetooth, points /dev/serial0 at
# the mini-UART (ttyS0), and sprays a kernel console on the header pins
# (console=serial0,115200). Result on an unconfigured unit: no /dev/ttyAMA0,
# and any BM transmit crashes on port open (found on bmcam003, Sprint09).
#
# APPLY (default; auto-sudo, reboot required afterwards):
#   ./tools/setup_bm_uart.sh [--dry-run]
#     - backs up /boot/firmware/config.txt and cmdline.txt to /home/pi/backups
#     - ensures enable_uart=1 and dtoverlay=disable-bt (appended under a fresh
#       [all] header so a trailing [cm4]/[pi4] section can't filter it out)
#     - removes console=serial0,115200 (and console=ttyAMA0,...) from cmdline
#     - disables hciuart (BT-over-PL011 service; useless once BT is off)
#
# CHECK (no root needed; use in the validation ladder — exits nonzero on FAIL):
#   ./tools/setup_bm_uart.sh --check
#     - /dev/serial0 -> ttyAMA0, /dev/ttyAMA0 present
#     - no serial console in the LIVE /proc/cmdline
#     - bm_serial open test if /home/pi/BM_Devel_Pi/bm_serial.py is deployed
#
# Known limitations: assumes Bluetooth is expendable (it is on bmcam units).
# The open test briefly owns /dev/ttyAMA0 — it will fail if an RC cycle is
# mid-transmit (check `pgrep -af rc_`), and once the planned BM serial daemon
# owns the port full-time this check should query the daemon instead.

set -euo pipefail

BOOTDIR=""
for d in /boot/firmware /boot; do
  [[ -f "$d/config.txt" ]] && BOOTDIR="$d" && break
done

BACKUP_DIR="/home/pi/backups"
RUNTIME_DIR="/home/pi/BM_Devel_Pi"
MODE="apply"
DRY_RUN="false"

usage() {
  cat <<'EOF'
Usage: tools/setup_bm_uart.sh [--check] [--dry-run] [-h|--help]

  (no args)   Apply the UART boot config (backs up, edits, needs a reboot).
  --check     Validate only; exit 0 = transmit-capable, nonzero = not.
  --dry-run   Apply mode: print actions without changing anything.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[BM-UART][ERROR] Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

log() { echo "[BM-UART] $*"; }
run() { if [[ "$DRY_RUN" == "true" ]]; then echo "[DRY-RUN] $*"; else "$@"; fi; }

# ---- check mode -------------------------------------------------------------
if [[ "$MODE" == "check" ]]; then
  FAIL=0
  ok()   { echo "[BM-UART][PASS] $*"; }
  bad()  { echo "[BM-UART][FAIL] $*"; FAIL=1; }

  if [[ -e /dev/serial0 ]]; then
    TARGET="$(readlink /dev/serial0 || true)"
    if [[ "$TARGET" == "ttyAMA0" ]]; then
      ok "/dev/serial0 -> ttyAMA0 (PL011)"
    else
      bad "/dev/serial0 -> ${TARGET:-?} (want ttyAMA0; mini-UART means BT still owns the PL011)"
    fi
  else
    bad "/dev/serial0 missing (enable_uart not set?)"
  fi

  [[ -e /dev/ttyAMA0 ]] && ok "/dev/ttyAMA0 present" || bad "/dev/ttyAMA0 missing (dtoverlay=disable-bt not active)"

  if [[ ! -r /proc/cmdline ]]; then
    bad "/proc/cmdline unreadable — not a Linux host? check must run ON the Pi"
  elif grep -qE 'console=(serial0|ttyAMA0|ttyS0)' /proc/cmdline; then
    bad "kernel serial console active in /proc/cmdline (will spray the header pins)"
  else
    ok "no kernel serial console on the UART"
  fi

  if systemctl is-enabled hciuart >/dev/null 2>&1; then
    bad "hciuart service still enabled"
  else
    ok "hciuart disabled/absent"
  fi

  # Transmit-capable proof: actually open the port the way the runtime does.
  if [[ -f "$RUNTIME_DIR/bm_serial.py" ]]; then
    if (cd "$RUNTIME_DIR" && python3 -c "import bm_serial; bm_serial.BristlemouthSerial().deinit()") 2>/tmp/bm_uart_open_test.err; then
      ok "bm_serial open test (BristlemouthSerial on /dev/ttyAMA0)"
    else
      bad "bm_serial open test failed — $(tail -1 /tmp/bm_uart_open_test.err 2>/dev/null); if an RC cycle owns the port, check 'pgrep -af rc_'"
    fi
  else
    log "open test skipped ($RUNTIME_DIR/bm_serial.py not deployed yet)"
  fi

  if [[ "$FAIL" -eq 0 ]]; then
    log "CHECK PASS — UART is BM-transmit-capable"
  else
    log "CHECK FAIL — run tools/setup_bm_uart.sh (then reboot) to fix"
  fi
  exit "$FAIL"
fi

# ---- apply mode -------------------------------------------------------------
[[ -n "$BOOTDIR" ]] || { echo "[BM-UART][ERROR] no config.txt under /boot/firmware or /boot — is this a Pi?" >&2; exit 1; }

# Boot-partition edits need root; re-exec under sudo so 'pi' can call this directly.
if [[ "$EUID" -ne 0 && "$DRY_RUN" != "true" ]]; then
  log "re-executing under sudo (boot config edits need root)"
  exec sudo "$0" "$@"
fi

CONFIG="$BOOTDIR/config.txt"
CMDLINE="$BOOTDIR/cmdline.txt"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
CHANGED=0

log "boot dir: $BOOTDIR"
run mkdir -p "$BACKUP_DIR"
log "backup: $CONFIG -> $BACKUP_DIR/config.txt.before_bm_uart_${TS}"
run cp "$CONFIG" "$BACKUP_DIR/config.txt.before_bm_uart_${TS}"
log "backup: $CMDLINE -> $BACKUP_DIR/cmdline.txt.before_bm_uart_${TS}"
run cp "$CMDLINE" "$BACKUP_DIR/cmdline.txt.before_bm_uart_${TS}"
log "restore: sudo cp $BACKUP_DIR/{config.txt,cmdline.txt}.before_bm_uart_${TS} $BOOTDIR/ (then reboot)"

# config.txt: enable_uart=1 + dtoverlay=disable-bt, each under its own [all]
# header so they apply no matter which conditional section the file ends in.
for SETTING in "enable_uart=1" "dtoverlay=disable-bt"; do
  if grep -qE "^\s*${SETTING}\s*$" "$CONFIG"; then
    log "config.txt: $SETTING already present"
  else
    log "config.txt: appending [all] $SETTING"
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "[DRY-RUN] printf '\\n[all]\\n%s\\n' '$SETTING' >> $CONFIG"
    else
      printf '\n[all]\n%s\n' "$SETTING" >> "$CONFIG"
    fi
    CHANGED=1
  fi
done

# cmdline.txt: drop any kernel console on the UART (keeps console=tty1).
if grep -qE 'console=(serial0|ttyAMA0|ttyS0),[0-9]+' "$CMDLINE"; then
  log "cmdline.txt: removing serial console token"
  run sed -i -E 's/console=(serial0|ttyAMA0|ttyS0),[0-9]+ ?//g' "$CMDLINE"
  CHANGED=1
else
  log "cmdline.txt: no serial console token (already clean)"
fi

# hciuart binds BT to the PL011 at boot; dead weight once BT is disabled.
if systemctl is-enabled hciuart >/dev/null 2>&1; then
  log "disabling hciuart service"
  run systemctl disable --now hciuart
  CHANGED=1
else
  log "hciuart already disabled or not installed"
fi

if [[ "$CHANGED" -eq 1 ]]; then
  log "DONE — changes applied. REBOOT REQUIRED before the UART moves to the PL011."
  log "WARNING: if the RC @reboot cron is armed, rebooting starts a capture cycle"
  log "and power_halt will HALT the box — disable the cron line first if benching."
  log "after reboot, verify with: tools/setup_bm_uart.sh --check"
else
  log "DONE — nothing to change (already configured). Verify: tools/setup_bm_uart.sh --check"
fi
