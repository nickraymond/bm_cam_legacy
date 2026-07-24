#!/bin/bash
# low_idle.sh — Profile A: runtime-only low-power idle for Pi Zero 2 W (WiFi stays ON)
#
# Purpose:  Lowest draw while still SSH-reachable. Turns off HDMI, ACT LED,
#           Bluetooth; sets CPU governor to powersave. WiFi power_save is left
#           ON (stock) by default for apples-to-apples vs baseline — measured
#           2026-07-24 that forcing it off costs more than the other savings.
# Usage:    sudo ./low_idle.sh              # apply profile (power_save stays on)
#           sudo ./low_idle.sh --psave-off  # also force WiFi power_save off (snappier SSH, +~20mW)
#           sudo ./low_idle.sh --revert     # undo at runtime
# Outputs:  progress lines to stdout; verification of each setting.
# NON-PERSISTENT: nothing here touches /boot, systemd, cron, or network config.
#                 A reboot fully restores stock behavior (reboot = rollback),
#                 EXCEPT Bluetooth: systemd-rfkill persists soft-block state
#                 across reboots (verified on Bookworm, 2026-07-24). Run
#                 --revert (or `sudo rfkill unblock bluetooth`) after a reboot
#                 if Bluetooth must be restored.
# Assumptions: Pi Zero 2 W, Bookworm, vcgencmd works, LED is /sys/class/leds/ACT,
#              iw lives at /usr/sbin/iw (not in default PATH on this image).
# Known limitations: does not touch USB (marginal on the Zero), does not
#              disable WiFi (never — SSH lifeline).

set -u
IW=/usr/sbin/iw

if [[ $(id -u) -ne 0 ]]; then echo "ERROR: run with sudo"; exit 1; fi

revert=0
psave_off=0
[[ "${1:-}" == "--revert" ]] && revert=1
[[ "${1:-}" == "--psave-off" ]] && psave_off=1

if [[ $revert -eq 0 ]]; then
    echo "=== LOW-IDLE: applying runtime power profile ==="

    echo "[1/5] HDMI off"
    vcgencmd display_power 0

    echo "[2/5] ACT LED off (trigger=none, brightness=0)"
    echo none > /sys/class/leds/ACT/trigger
    echo 0    > /sys/class/leds/ACT/brightness

    echo "[3/5] Bluetooth off (rfkill soft-block, runtime only)"
    rfkill block bluetooth

    echo "[4/5] CPU governor -> powersave"
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo powersave > "$g"
    done

    if [[ $psave_off -eq 1 ]]; then
        echo "[5/5] WiFi power_save OFF (--psave-off: snappier SSH, costs ~20mW)"
        $IW dev wlan0 set power_save off
    else
        echo "[5/5] WiFi power_save left as-is (stock: on)"
    fi

    echo "=== verification ==="
else
    echo "=== LOW-IDLE: reverting to stock runtime state ==="

    echo "[1/5] HDMI on"
    vcgencmd display_power 1

    echo "[2/5] ACT LED restore (trigger=actpwr)"
    echo actpwr > /sys/class/leds/ACT/trigger

    echo "[3/5] Bluetooth unblock"
    rfkill unblock bluetooth

    echo "[4/5] CPU governor -> ondemand"
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo ondemand > "$g"
    done

    echo "[5/5] WiFi power_save ON (stock state on this image)"
    $IW dev wlan0 set power_save on

    echo "=== verification ==="
fi

echo "display_power : $(vcgencmd display_power)"
echo "ACT trigger   : $(tr -d '\n' < /sys/class/leds/ACT/trigger | grep -oE '\[[a-z0-9-]+\]')"
echo "ACT brightness: $(cat /sys/class/leds/ACT/brightness)"
echo "bluetooth     : $(rfkill list bluetooth | grep 'Soft blocked')"
echo "governor      : $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
echo "wifi psave    : $($IW dev wlan0 get power_save)"
echo "wlan0         : $(ip -brief link show wlan0)"
echo "=== done ==="
