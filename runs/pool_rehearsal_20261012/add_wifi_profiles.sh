#!/bin/bash
# add_wifi_profiles.sh — Nick runs this ONCE in a Mac terminal during a camera wake (e.g. 10:00–10:07 AM). It:
#   1. waits until bmcam003 + bmcam004 answer ssh (it does nothing outside a wake);
#   2. adds the POOL Wi-Fi profile (con-name pool-wifi, priority -10), then the PHONE HOTSPOT profile (con-name
#      phone-hotspot, priority -5), via tools/pi_wifi_profile.sh. Each asks for the network name (shown) and the
#      password (hidden). The password goes over ssh STDIN only: never in argv, shell history, a file on the Mac,
#      or Claude's view;
#   3. verifies on both cameras, read-only and WITHOUT secrets: profile present, SSID, autoconnect, priority.
# Priorities: home Wi-Fi 0 > phone-hotspot -5 > pool-wifi -10. At home they stay on home Wi-Fi. At the pool the
# hotspot wins when it is on, otherwise the pool Wi-Fi. Adding a profile does NOT switch the current network.
# Skip a network: press Enter at its "add?" question.
# Usage: bash runs/pool_rehearsal_20261012/add_wifi_profiles.sh   (from the repo folder)
set -u
cd "$(dirname "$0")/../.." || exit 1
T=(pi@bmcam003 pi@bmcam004)
O=(-o BatchMode=yes -o ConnectTimeout=4)
echo "Waiting for both cameras to be up (Ctrl-C to quit)..."
until ssh "${O[@]}" pi@bmcam003 true </dev/null 2>/dev/null && ssh "${O[@]}" pi@bmcam004 true </dev/null 2>/dev/null; do sleep 3; printf .; done
echo; echo "Both cameras up at $(date +'%-I:%M:%S %p'). They halt about 8 min after the hour, so answer the prompts promptly."
for spec in "pool-wifi -10 POOL Wi-Fi" "phone-hotspot -5 PHONE HOTSPOT"; do
  set -- $spec; con=$1; pri=$2; shift 2; label="$*"
  read -r -p "Add the $label profile ($con)? [y/N] " yn
  case "$yn" in y|Y|yes) tools/pi_wifi_profile.sh --priority "$pri" "$con" "${T[@]}" || echo "!! $con FAILED on at least one camera";; *) echo "skipped $con";; esac
done
echo; echo "== Verify (no secrets shown)"
for h in "${T[@]}"; do
  ssh "${O[@]}" "$h" 'for c in pool-wifi phone-hotspot; do nmcli -g connection.id connection show "$c" >/dev/null 2>&1 && echo "$(hostname) $c ssid=$(nmcli -g 802-11-wireless.ssid connection show "$c") autoconnect=$(nmcli -g connection.autoconnect connection show "$c") priority=$(nmcli -g connection.autoconnect-priority connection show "$c")" || echo "$(hostname) $c NOT PRESENT"; done; echo "$(hostname) current: $(nmcli -t -g NAME connection show --active | head -1)"' </dev/null 2>&1
done
echo "Done. Tell Claude 'wifi done'."
