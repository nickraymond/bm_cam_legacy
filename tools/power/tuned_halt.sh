#!/bin/bash
# tuned_halt.sh — Profile B: peripheral-off then clean halt for Pi Zero 2 W
#
# Purpose:  Minimum draw during a waiting window. Applies the low-idle
#           peripheral-offs (HDMI, ACT LED, Bluetooth), then issues a clean
#           systemd halt.
# Usage:    sudo ./tuned_halt.sh              # systemctl halt
#           sudo ./tuned_halt.sh --poweroff   # systemctl poweroff (A/B: may reach a deeper firmware state)
# Outputs:  progress lines, then a 10 s countdown, then the system halts.
#           SSH WILL DROP — a broken pipe / closed connection here is SUCCESS.
# RECOVERY: manual power cycle (unplug/replug USB) is the ONLY way back.
# NON-PERSISTENT: nothing here touches /boot, systemd units, cron, or network
#                 config. The power cycle boots back to stock state,
#                 EXCEPT Bluetooth: systemd-rfkill persists soft-block state
#                 across reboots (verified on Bookworm, 2026-07-24). After
#                 recovery, run `sudo rfkill unblock bluetooth` if needed.
# Assumptions: Pi Zero 2 W, Bookworm, vcgencmd works, LED at /sys/class/leds/ACT.

set -u

if [[ $(id -u) -ne 0 ]]; then echo "ERROR: run with sudo"; exit 1; fi

stop_cmd=halt
[[ "${1:-}" == "--poweroff" ]] && stop_cmd=poweroff

echo "=== TUNED-HALT: peripherals off, then clean ${stop_cmd} ==="

echo "[1/4] HDMI off"
vcgencmd display_power 0

echo "[2/4] ACT LED off"
echo none > /sys/class/leds/ACT/trigger
echo 0    > /sys/class/leds/ACT/brightness

echo "[3/4] Bluetooth off (rfkill soft-block)"
rfkill block bluetooth

echo "[4/4] Clean ${stop_cmd} in 10 seconds — SSH will drop (that is expected/success)."
for s in 10 9 8 7 6 5 4 3 2 1; do echo "  ${stop_cmd} in ${s}s"; sleep 1; done

sync
systemctl "$stop_cmd"
