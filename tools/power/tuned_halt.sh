#!/bin/bash
# tuned_halt.sh — Profile B: peripheral-off then clean halt for Pi Zero 2 W
#
# Purpose:  Minimum draw during a waiting window. Applies the low-idle
#           peripheral-offs (HDMI, ACT LED, Bluetooth), then issues a clean
#           systemd halt.
# Usage:    sudo ./tuned_halt.sh
# Outputs:  progress lines, then a 10 s countdown, then the system halts.
#           SSH WILL DROP — a broken pipe / closed connection here is SUCCESS.
# RECOVERY: manual power cycle (unplug/replug USB) is the ONLY way back.
# NON-PERSISTENT: nothing here touches /boot, systemd units, cron, or network
#                 config. The power cycle boots back to stock state.
# Assumptions: Pi Zero 2 W, Bookworm, vcgencmd works, LED at /sys/class/leds/ACT.

set -u

if [[ $(id -u) -ne 0 ]]; then echo "ERROR: run with sudo"; exit 1; fi

echo "=== TUNED-HALT: peripherals off, then clean halt ==="

echo "[1/4] HDMI off"
vcgencmd display_power 0

echo "[2/4] ACT LED off"
echo none > /sys/class/leds/ACT/trigger
echo 0    > /sys/class/leds/ACT/brightness

echo "[3/4] Bluetooth off (rfkill soft-block)"
rfkill block bluetooth

echo "[4/4] Clean halt in 10 seconds — SSH will drop (that is expected/success)."
for s in 10 9 8 7 6 5 4 3 2 1; do echo "  halt in ${s}s"; sleep 1; done

sync
systemctl halt
