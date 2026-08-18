# Sprint16 — Design

Decisions numbered D-S16-N. Where a decision copies an existing
doctrine the source is named — this sprint invents as little as
possible.

## D-S16-1 — One network script, nmcli-only (network_ap.sh rewrite)

`network_ap.sh` is rewritten for NetworkManager and stays the ONLY
place that touches WiFi (same boundary as Sprint15 D-S15-10). Verbs:

- `status`  — current mode (ap | client:<ssid> | connecting), default,
  timer state. Machine-greppable one-liners.
- `default` — apply the YAML boot default (used at boot and by wap 0).
- `ap`      — raise the open AP: SSID `$(hostname)`, no key, static
  192.168.50.1/24, NM shared mode (NM runs its own dnsmasq — no extra
  packages, which is why hostapd/dnsmasq absence on Trixie is fine).
- `hq`      — activate the provisioned `nereus-hq` NM profile.
- `join <ssid> <psk>` — SESSION-ONLY customer join:
  `nmcli connection add save no` (in-memory profile, autoconnect off,
  gone at reboot — verify flag on Trixie NM at bench, gate 4). PSK is
  passed via stdin/file, never argv (ps/log hygiene), never logged.
- `arm-revert <min>` / `revert` / `disarm` — systemd one-shot timer,
  copied from the v6 script's revert-first design: `arm-revert` must
  VERIFY the timer is armed and report success BEFORE any flip; flips
  refuse to run un-armed when invoked via the remote path.

The AP profile and the timer are never persisted/enabled across
reboot — boot always starts from `default` (D-S16-3).

## D-S16-2 — wap tables v7 (command surface)

`wap`: `0` = apply boot default · `1` = AP now · `2` = HQ now.
Immediate-apply via the existing IMMEDIATE_COMMANDS path (v6 wiring
unchanged; fires once, duplicates never re-fire). Remote flips call
`arm-revert` first (D-S16-1); revert target is the boot default.
help/cfg rows updated; cfg reads the live mode marker from `status`.
BM can NOT change the YAML default (lockout-vector doctrine, same list
as capture_mode in the command reference).

## D-S16-3 — Boot apply + the 90 s AP fallback

The video/stills runtime calls `network_ap.sh default` at startup
(same slot where Sprint15 armed nothing — one added call, cron line
unchanged). With `default: ap` that raises the AP immediately. With a
client default, NM tries the profile; a monitor waits
`ap_fallback_s` (90) and raises the AP if no connection was
established — a unit can never boot unreachable (Nick 2026-08-18).
Fallback-AP is still session-only state: next boot tries the default
again fresh.

## D-S16-4 — network: YAML island + settings GUI

Island per SPEC (default / ap_fallback_s / ap_timeout_min), parsed
with the same loud-validation pattern as the video island. Settings
GUI gets a "Network" section: default selector (ap | nereus_hq) using
the existing YAML-write machinery (timestamped
`camera_schedule.yaml.before_gui_*` backups, restart-to-apply), plus a
"Join a WiFi network" form (SSID + password) that calls `join` —
session-only, clearly labeled "until next power cycle". UI shows an
open-network banner whenever mode == ap (D-S16-6).

## D-S16-5 — HQ credentials (provision-time, never in-repo)

`nereus-hq` NM profile installed during provisioning (new
bmcam-provision step: human supplies SSID/PSK at the keyboard or via
a file that is shredded after `nmcli connection add`). The repo,
YAML, logs, and BM bus never carry the PSK. The profile name is the
contract (`hq` verb activates it); its absence makes `wap 2` fail
loudly and harmlessly.

## D-S16-6 — Open-AP exposure posture

Accepted for MVP (Nick): open AP, gallery + settings reachable by
anyone in radio range while AP is up. Mitigations shipped: UI banner
("open network — anyone nearby can view"), AP exposes only :8080
(sshd not bound / nftables rule on the AP interface), and AP is
session-only (revert timer for remote flips; boot default rules
otherwise). WPA2 knob: future, on customer request.

## D-S16-7 — Test strategy

Unit tests, no hardware: island parsing/validation; verb argv
construction (nmcli calls subprocess-injected and faked); session-only
join never passes `save yes`; PSK never appears in argv/logs
(explicit assertion); wap v7 table/help/cfg + immediate-apply dedupe;
boot-apply dispatch; GUI form → join call wiring; version-guard bump
v6→v7. Bench (attended, bmcam000) proves the real NM behavior:
gates 2–7, especially `save no` non-persistence (gate 4) and the 90 s
fallback (gate 6). bmcam003/004 stay on their Sprint15 HIL,
untouched.
