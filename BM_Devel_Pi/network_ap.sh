#!/bin/bash
# filename: network_ap.sh
# description: Sprint15 wap — WiFi AP flip with a hard auto-revert (D-S15-10).
#
# Usage (root; the daemon calls it via sudo -n):
#   network_ap.sh up [timeout_min]   # arm revert timer FIRST, then flip to AP
#   network_ap.sh down               # revert to client WiFi (also the timer's job)
#   network_ap.sh status             # one-line state for logs/cfg
#
# REVERT-FIRST DESIGN (the only un-brick): the systemd one-shot timer that
# calls `down` is armed and VERIFIED before anything touches the network.
# If the timer cannot be armed, the flip is refused. A reboot also comes up
# in client WiFi (nothing here persists), so a power cycle is a second
# un-brick.
#
# Stack assumptions (bmcam000, Pi OS bullseye, verified 2026-08-18):
#   dhcpcd + a separate wpa_supplicant service manage wlan0 (no
#   NetworkManager, no eth0); hostapd + dnsmasq installed but disabled.
#
# AP parameters: SSID <hostname>-video, WPA2 passphrase "bristlemouth",
# static 192.168.50.1/24, DHCP 192.168.50.10-99. Gallery at
# http://192.168.50.1:8080 (videoui_server binds 0.0.0.0 — no change).

set -u

WLAN_IF="wlan0"
AP_IP="192.168.50.1"
RUN_DIR="/run/bmcam_ap"
MARKER="$RUN_DIR/ap_active"
REVERT_UNIT="bmcam-ap-revert"
LOG_FILE="/home/pi/BM_Devel_Pi/cron_logs/network_ap.log"
SELF="$(readlink -f "$0")"
DEFAULT_TIMEOUT_MIN=60

log() {
    local line="[WAP] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "$line"
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null
    echo "$line" >> "$LOG_FILE" 2>/dev/null
}

require_root() {
    if [[ "$(id -u)" != "0" ]]; then
        echo "[WAP][ERROR] must run as root (daemon uses sudo -n)" >&2
        exit 2
    fi
}

write_confs() {
    mkdir -p "$RUN_DIR"
    local ssid
    ssid="$(hostname)-video"
    cat > "$RUN_DIR/hostapd.conf" <<EOF
interface=$WLAN_IF
driver=nl80211
ssid=$ssid
hw_mode=g
channel=6
auth_algs=1
wpa=2
wpa_passphrase=bristlemouth
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
    # port=0 disables DNS entirely — the phone only needs a DHCP lease to
    # reach http://192.168.50.1:8080 by IP.
    cat > "$RUN_DIR/dnsmasq.conf" <<EOF
interface=$WLAN_IF
bind-interfaces
port=0
dhcp-range=192.168.50.10,192.168.50.99,255.255.255.0,1h
EOF
    log "confs written (ssid=$ssid)"
}

arm_revert_timer() {
    local timeout_min="$1"
    systemctl stop "$REVERT_UNIT.timer" 2>/dev/null
    systemctl reset-failed "$REVERT_UNIT.service" 2>/dev/null
    systemctl reset-failed "$REVERT_UNIT.timer" 2>/dev/null
    if ! systemd-run --unit="$REVERT_UNIT" --on-active="${timeout_min}min" \
            "$SELF" down >/dev/null 2>&1; then
        return 1
    fi
    systemctl is-active --quiet "$REVERT_UNIT.timer"
}

ap_up() {
    local timeout_min="${1:-$DEFAULT_TIMEOUT_MIN}"
    if [[ -f "$MARKER" ]]; then
        log "already in AP mode ($(cat "$MARKER" 2>/dev/null)); nothing to do"
        exit 0
    fi
    # 1. The un-brick comes FIRST. No timer, no flip.
    if ! arm_revert_timer "$timeout_min"; then
        log "ERROR: could not arm the $REVERT_UNIT revert timer; REFUSING to flip"
        exit 1
    fi
    log "revert timer armed: client WiFi restored in ${timeout_min} min"

    # 2. Flip: release client WiFi, static IP, hostapd + dnsmasq.
    write_confs
    systemctl stop wpa_supplicant 2>/dev/null
    dhcpcd -k "$WLAN_IF" >/dev/null 2>&1
    sleep 1
    ip addr flush dev "$WLAN_IF"
    ip addr add "$AP_IP/24" dev "$WLAN_IF"
    ip link set "$WLAN_IF" up
    if ! hostapd -B "$RUN_DIR/hostapd.conf" >/dev/null 2>&1; then
        log "ERROR: hostapd failed to start; reverting immediately"
        ap_down
        exit 1
    fi
    if ! dnsmasq --conf-file="$RUN_DIR/dnsmasq.conf" \
            --pid-file="$RUN_DIR/dnsmasq.pid" >/dev/null 2>&1; then
        log "ERROR: dnsmasq failed to start; reverting immediately"
        ap_down
        exit 1
    fi
    date -u +%Y-%m-%dT%H:%M:%SZ > "$MARKER"
    log "AP UP: ssid=$(hostname)-video ip=$AP_IP gallery=http://$AP_IP:8080"
}

ap_down() {
    log "reverting to client WiFi"
    pkill -F "$RUN_DIR/dnsmasq.pid" 2>/dev/null
    pkill -x hostapd 2>/dev/null
    sleep 1
    ip addr flush dev "$WLAN_IF"
    rm -f "$MARKER"
    # Cancel a still-pending revert timer (early wap 0); harmless if this
    # IS the timer firing.
    systemctl stop "$REVERT_UNIT.timer" 2>/dev/null
    systemctl reset-failed "$REVERT_UNIT.service" 2>/dev/null
    systemctl reset-failed "$REVERT_UNIT.timer" 2>/dev/null
    # Hand wlan0 back to the client stack.
    systemctl start wpa_supplicant 2>/dev/null
    systemctl restart dhcpcd 2>/dev/null
    log "client WiFi restored (dhcpcd + wpa_supplicant restarted)"
}

ap_status() {
    if [[ -f "$MARKER" ]]; then
        echo "AP mode since $(cat "$MARKER") (revert timer: $(systemctl is-active "$REVERT_UNIT.timer" 2>/dev/null))"
    else
        echo "client WiFi (normal)"
    fi
}

case "${1:-}" in
    up)     require_root; ap_up "${2:-$DEFAULT_TIMEOUT_MIN}" ;;
    down)   require_root; ap_down ;;
    status) ap_status ;;
    *) echo "usage: $0 up [timeout_min] | down | status" >&2; exit 2 ;;
esac
