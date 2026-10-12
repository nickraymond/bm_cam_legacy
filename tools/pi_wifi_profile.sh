#!/bin/bash
# pi_wifi_profile.sh — save a NetworkManager Wi-Fi profile on one or more Pis WITHOUT the password
# ever being in the Mac's shell history, in an ssh argv, or in any process list on the Pi.
#
# Purpose:  pool day 2026-10-13: the optional pool Wi-Fi (Tue AM) on bmcam003, bmcam004 and nereus000,
#           or a home-Wi-Fi profile on a host that lacks one (the phone hotspot clones the home
#           SSID + password, so the units need nothing). EM rule: no plaintext password in argv/history.
# How:      prompts once on the Mac (SSID visible, password silent), sends both over ssh STDIN;
#           on the Pi a root shell reads them and writes the keyfile with bash's BUILTIN printf
#           (no exec, so nothing in ps), umask 077, chmod 600, then `nmcli connection reload`.
#           Keyfile: /etc/NetworkManager/system-connections/<con-name>.nmconnection (Bookworm NM).
#           A previous file of that name is backed up to /var/backups/nm-<con-name>.<UTC>.nmconnection
#           (root 0600) before it is replaced. Adding a profile does NOT switch the current network.
# Inputs:   $1 con-name (e.g. phone-hotspot | pool-wifi), $2.. ssh targets (pi@bmcam003 ...)
#           --priority N (before the con-name): connection.autoconnect-priority (default 0;
#           e.g. -10 for pool-wifi so a reboot prefers the hotspot)
# Outputs:  per host: "OK <host> <con-name> autoconnect=yes" or a FAILED line; exit 0 only if all OK.
# Example:  tools/pi_wifi_profile.sh home-wifi pi@nereus000.local
#           tools/pi_wifi_profile.sh --priority -10 pool-wifi pi@172.20.10.4 pi@172.20.10.5 pi@172.20.10.3
# Limits:   WPA-PSK only (8..63 chars); refuses a backslash or a leading/trailing space in the SSID or
#           password (keyfile escaping); needs key ssh + passwordless sudo for pi (as on the units).
#           The password sits in the root-0600 keyfile at rest, which is how NetworkManager stores it.
# Test hooks: PI_WIFI_SUDO (default "sudo -n"), NM_DIR, NM_BACKUP_DIR, NMCLI (passed to the remote).
set -u
PRIO=0
if [ "${1:-}" = "--priority" ]; then PRIO="${2:?--priority N}"; shift 2; fi
CON="${1:?usage: $0 [--priority N] <con-name> <user@host> [...]}"; shift
[ $# -ge 1 ] || { echo "usage: $0 [--priority N] <con-name> <user@host> [...]" >&2; exit 2; }
case "$CON" in *[!A-Za-z0-9_.-]*) echo "[wifi] refusing con-name '$CON' (use [A-Za-z0-9_.-])" >&2; exit 2;; esac
case "$PRIO" in -[0-9]*|[0-9]*) ;; *) echo "[wifi] --priority must be an integer" >&2; exit 2;; esac

IFS= read -r -p "SSID for '$CON' (exact, case-sensitive): " SSID
IFS= read -r -s -p "Password (hidden): " PSK; echo
[ -n "$SSID" ] || { echo "[wifi] empty SSID" >&2; exit 2; }
[ ${#PSK} -ge 8 ] && [ ${#PSK} -le 63 ] || { unset PSK; echo "[wifi] WPA password must be 8..63 chars" >&2; exit 2; }
case "$SSID$PSK" in *\\*) unset PSK; echo "[wifi] refusing a backslash in SSID/password (keyfile escaping)" >&2; exit 2;; esac
for v in "$SSID" "$PSK"; do
  [ "$v" = "${v# }" ] && [ "$v" = "${v% }" ] || { unset PSK; echo "[wifi] refusing a leading/trailing space" >&2; exit 2; }
done

# Remote program: no secrets in it (they arrive on stdin). Runs as root via `sudo -n env K=V ... bash -c`
# (sudo resets the environment, so the non-secret settings ride in env's argv).
REMOTE='set -u; umask 077
IFS= read -r S; IFS= read -r P
D="${NM_DIR:-/etc/NetworkManager/system-connections}"; B="${NM_BACKUP_DIR:-/var/backups}"
F="$D/$CON.nmconnection"; mkdir -p "$D" "$B"
[ -f "$F" ] && cp -p "$F" "$B/nm-$CON.$(date -u +%Y%m%dT%H%M%SZ).nmconnection" && echo "backup: $B/nm-$CON.*"
U=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen)
T=$(mktemp "$B/.nm-$CON.XXXXXX")
printf "[connection]\nid=%s\nuuid=%s\ntype=wifi\ninterface-name=wlan0\nautoconnect=true\nautoconnect-priority=%s\n\n[wifi]\nmode=infrastructure\nssid=%s\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n" "$CON" "$U" "$PRIO" "$S" "$P" > "$T"
unset P; chmod 600 "$T"; mv -f "$T" "$F"
${NMCLI:-nmcli} connection reload || { echo "nmcli reload failed"; exit 1; }
N=$(${NMCLI:-nmcli} -g NAME,AUTOCONNECT connection show | grep -c "^$CON:")
[ "$N" -ge 1 ] || { echo "profile $CON not loaded by NetworkManager"; exit 1; }
[ "$N" -eq 1 ] || echo "WARN: $N profiles named $CON (an older one from nmcli add?); delete the extra by UUID"
echo "loaded"'

SUDO="${PI_WIFI_SUDO-sudo -n}"
RC=0
for H in "$@"; do
  OUT=$(printf '%s\n%s\n' "$SSID" "$PSK" | ssh -o BatchMode=yes -o ConnectTimeout=10 "$H" \
        "$SUDO env CON=$CON PRIO=$PRIO ${NM_DIR:+NM_DIR=$NM_DIR} ${NM_BACKUP_DIR:+NM_BACKUP_DIR=$NM_BACKUP_DIR} ${NMCLI:+NMCLI=$NMCLI} bash -c $(printf %q "$REMOTE")" 2>&1)
  if [ $? -eq 0 ] && printf '%s' "$OUT" | grep -q "^loaded"; then
    echo "OK $H $CON autoconnect=yes priority=$PRIO$(printf '%s' "$OUT" | grep -E '^(WARN|backup)' | sed 's/^/ | /' | tr '\n' ' ')"
  else
    echo "FAILED $H $CON: $(printf '%s' "$OUT" | tail -3 | tr '\n' ' ')"; RC=1
  fi
done
unset PSK SSID
exit $RC
