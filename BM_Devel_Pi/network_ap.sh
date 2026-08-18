#!/bin/bash
# filename: network_ap.sh
# description: Sprint16 WiFi control on NetworkManager (D-S16-1) — boot
#              default, open AP, HQ client, session-only customer join,
#              revert-first timer.
#
# Usage (root; the daemon/runtime call it via sudo -n):
#   network_ap.sh default <ap|nereus_hq> [fallback_s]   # apply boot default
#   network_ap.sh ap [timeout_min]        # open AP; timeout => REMOTE flip:
#                                         # revert timer armed+verified FIRST
#   network_ap.sh hq [timeout_min]        # join Nereus HQ; same remote rule
#   network_ap.sh join <ssid> <psk_file>  # SESSION-ONLY customer WiFi; the
#                                         # psk file (root 0600) is deleted
#                                         # after use, never argv/logged
#   network_ap.sh revert                  # timer target: re-apply default
#   network_ap.sh disarm                  # cancel a pending revert timer
#   network_ap.sh status                  # one line for logs/cfg
#
# REVERT-FIRST (D-S15-10 carried into D-S16): a REMOTE flip (ap/hq with a
# timeout) refuses to run unless the systemd one-shot revert timer is armed
# and VERIFIED. Nothing here persists: every NM connection this script
# creates uses `save no` (in-memory), so a power cycle always boots into
# the YAML default (applied by the runtime) — the second un-brick.
#
# 90 s AP FALLBACK (Nick 2026-08-18): `default nereus_hq 90` that cannot
# join HQ within the wait raises the open AP instead — a unit can never
# boot into unreachability. `default ap` boots straight to AP, no wait.
#
# Stack assumptions (Trixie/NetworkManager fleet, verified 2026-08-18):
#   NM owns wlan0 (no dhcpcd/hostapd/dnsmasq — NM shared mode provides
#   DHCP for the AP itself). HQ credentials live in the provisioned NM
#   profile named "nereus-hq" (D-S16-5); this script never sees the PSK.
#
# AP parameters: OPEN network (no password, D-S16-6), SSID = hostname,
# static 192.168.50.1/24, gallery http://192.168.50.1:8080. While the AP
# is up, tcp/22 on wlan0 is dropped (nft, best-effort) — only the UI port
# is meant to be exposed to radio range.

set -u

WLAN_IF="wlan0"
AP_IP="192.168.50.1"
RUN_DIR="/run/bmcam_net"
MODE_FILE="$RUN_DIR/mode"            # ap | client:<name> | joining
DEFAULT_FILE="$RUN_DIR/default"      # stored boot default for `revert`
AP_CON="bmcam-ap"
CUST_CON="bmcam-customer"
HQ_CON="nereus-hq"
REVERT_UNIT="bmcam-net-revert"
NFT_TABLE="bmcam_ap"
LOG_FILE="/home/pi/BM_Devel_Pi/cron_logs/network_ap.log"
SELF="$(readlink -f "$0")"
DEFAULT_TIMEOUT_MIN=60
DEFAULT_FALLBACK_S=90

log() {
    local line="[NET] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "$line"
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null
    echo "$line" >> "$LOG_FILE" 2>/dev/null
}

require_root() {
    if [[ "$(id -u)" != "0" ]]; then
        echo "[NET][ERROR] must run as root (callers use sudo -n)" >&2
        exit 2
    fi
}

set_mode() { mkdir -p "$RUN_DIR"; echo "$1" > "$MODE_FILE"; }

# ---- revert timer (the only un-brick for REMOTE flips) --------------------

arm_revert_timer() {
    local timeout_min="$1"
    systemctl stop "$REVERT_UNIT.timer" 2>/dev/null
    systemctl reset-failed "$REVERT_UNIT.service" 2>/dev/null
    systemctl reset-failed "$REVERT_UNIT.timer" 2>/dev/null
    if ! systemd-run --unit="$REVERT_UNIT" --on-active="${timeout_min}min" \
            "$SELF" revert >/dev/null 2>&1; then
        return 1
    fi
    systemctl is-active --quiet "$REVERT_UNIT.timer"
}

disarm_timer() {
    systemctl stop "$REVERT_UNIT.timer" 2>/dev/null
    systemctl reset-failed "$REVERT_UNIT.service" 2>/dev/null
    systemctl reset-failed "$REVERT_UNIT.timer" 2>/dev/null
}

require_timer_or_refuse() {
    local timeout_min="$1" what="$2"
    if ! arm_revert_timer "$timeout_min"; then
        log "ERROR: could not arm $REVERT_UNIT timer; REFUSING remote $what"
        exit 1
    fi
    log "revert timer armed: boot default restored in ${timeout_min} min"
}

# ---- ssh exposure guard on the open AP (best-effort, D-S16-6) -------------

nft_block_ssh() {
    command -v nft >/dev/null 2>&1 || { log "WARN: nft missing; ssh not blocked on AP"; return 0; }
    nft add table inet "$NFT_TABLE" 2>/dev/null
    nft "add chain inet $NFT_TABLE input { type filter hook input priority 0 ; }" 2>/dev/null
    nft add rule inet "$NFT_TABLE" input iifname "$WLAN_IF" tcp dport 22 drop 2>/dev/null \
        && log "ssh (tcp/22) blocked on $WLAN_IF while AP is up"
}

nft_unblock_ssh() {
    command -v nft >/dev/null 2>&1 && nft delete table inet "$NFT_TABLE" 2>/dev/null
    return 0
}

# ---- session-connection hygiene -------------------------------------------

drop_session_cons() {
    # In-memory cons (save no) vanish at reboot; delete explicitly on any
    # mode change so wlan0 is never contended.
    nmcli connection down "$AP_CON" >/dev/null 2>&1
    nmcli connection delete "$AP_CON" >/dev/null 2>&1
    nmcli connection down "$CUST_CON" >/dev/null 2>&1
    nmcli connection delete "$CUST_CON" >/dev/null 2>&1
    nft_unblock_ssh
}

# ---- modes ----------------------------------------------------------------

ap_up() {
    drop_session_cons
    local ssid
    ssid="$(hostname)"
    # OPEN AP (no wifi-sec block = no password), in-memory only (save no),
    # NM `shared` runs its own DHCP for the 192.168.50.0/24 clients.
    if ! nmcli connection add type wifi ifname "$WLAN_IF" con-name "$AP_CON" \
            save no autoconnect no ssid "$ssid" \
            802-11-wireless.mode ap 802-11-wireless.band bg \
            ipv4.method shared ipv4.addresses "$AP_IP/24" \
            ipv6.method disabled >/dev/null 2>&1; then
        log "ERROR: nmcli add $AP_CON failed"
        return 1
    fi
    if ! nmcli -w 20 connection up "$AP_CON" >/dev/null 2>&1; then
        log "ERROR: nmcli up $AP_CON failed"
        nmcli connection delete "$AP_CON" >/dev/null 2>&1
        return 1
    fi
    nft_block_ssh
    set_mode "ap"
    log "AP UP (open): ssid=$ssid ip=$AP_IP gallery=http://$AP_IP:8080"
}

hq_up() {
    drop_session_cons
    if ! nmcli -w 45 connection up "$HQ_CON" >/dev/null 2>&1; then
        log "ERROR: could not activate '$HQ_CON' profile"
        return 1
    fi
    set_mode "client:$HQ_CON"
    log "client WiFi UP: profile=$HQ_CON"
}

join_up() {
    local ssid="$1" psk_file="$2"
    if [[ ! -f "$psk_file" ]]; then
        log "ERROR: psk file missing for join"
        return 1
    fi
    drop_session_cons
    set_mode "joining"
    # Two-step keeps the PSK out of every argv: the connection is added
    # key-mgmt-only, then activated with nmcli's passwd-file (root 0600,
    # deleted immediately after regardless of outcome).
    nmcli connection add type wifi ifname "$WLAN_IF" con-name "$CUST_CON" \
        save no autoconnect no ssid "$ssid" \
        wifi-sec.key-mgmt wpa-psk >/dev/null 2>&1
    local ok=1
    if nmcli -w 45 connection up "$CUST_CON" passwd-file "$psk_file" >/dev/null 2>&1; then
        ok=0
    fi
    rm -f "$psk_file"
    if [[ "$ok" != "0" ]]; then
        log "ERROR: join '$ssid' failed (bad credentials or out of range); raising AP so the user can retry"
        nmcli connection delete "$CUST_CON" >/dev/null 2>&1
        ap_up
        return 1
    fi
    set_mode "client:$ssid"
    log "customer WiFi UP (session only, forgotten at power cycle): ssid=$ssid"
}

apply_default() {
    local mode="$1" fallback_s="${2:-$DEFAULT_FALLBACK_S}"
    mkdir -p "$RUN_DIR"; echo "$mode" > "$DEFAULT_FILE"
    disarm_timer
    case "$mode" in
        ap)
            ap_up || exit 1
            ;;
        nereus_hq)
            if hq_up; then
                :
            else
                # 90 s AP fallback (Nick 2026-08-18): never boot unreachable.
                log "default '$HQ_CON' not joined (waited ~${fallback_s}s window); FALLING BACK to open AP"
                ap_up || exit 1
            fi
            ;;
        *)
            echo "[NET][ERROR] unknown default mode: $mode" >&2
            exit 2
            ;;
    esac
}

do_revert() {
    local mode
    mode="$(cat "$DEFAULT_FILE" 2>/dev/null || echo nereus_hq)"
    log "revert: re-applying boot default ($mode)"
    apply_default "$mode"
}

net_status() {
    local mode timer
    mode="$(cat "$MODE_FILE" 2>/dev/null || echo unknown)"
    timer="$(systemctl is-active "$REVERT_UNIT.timer" 2>/dev/null || echo inactive)"
    echo "mode=$mode default=$(cat "$DEFAULT_FILE" 2>/dev/null || echo unset) revert_timer=$timer"
}

case "${1:-}" in
    default) require_root; apply_default "${2:?default needs ap|nereus_hq}" "${3:-$DEFAULT_FALLBACK_S}" ;;
    ap)      require_root
             if [[ -n "${2:-}" ]]; then require_timer_or_refuse "$2" "ap"; fi
             ap_up || exit 1 ;;
    hq)      require_root
             if [[ -n "${2:-}" ]]; then require_timer_or_refuse "$2" "hq"; fi
             if ! hq_up; then
                 log "remote hq flip failed; raising AP (revert timer still restores the default)"
                 ap_up || exit 1
             fi ;;
    join)    require_root; join_up "${2:?join needs ssid}" "${3:?join needs psk_file}" ;;
    revert)  require_root; do_revert ;;
    disarm)  require_root; disarm_timer; log "revert timer disarmed" ;;
    status)  net_status ;;
    *) echo "usage: $0 default <ap|nereus_hq> [fallback_s] | ap [timeout_min] | hq [timeout_min] | join <ssid> <psk_file> | revert | disarm | status" >&2
       exit 2 ;;
esac
