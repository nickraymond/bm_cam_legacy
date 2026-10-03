# Bench / field Pi host setup (standard hardening)

Applies to every Pi we run: bmcam units AND bench hosts (nereus000 console monitor). Each item is a
drop-in file: reversible by deleting it, never an edit of a distro file. Provisioning (`bmcam-provision`
skill, Phase 1b) applies the unit items; this file is the reference and the verification.

## Why (history)

| item | found | effect |
|---|---|---|
| Wi-Fi power save OFF | 2026-10-02: nereus000 lost its LAN link outdoors for ~2 h while its console monitor kept logging (power save was ON). Earlier: a freshly rebuilt bmcam003 dropped off the LAN (fixed by hand with the same NM drop-in, commit 8d10339) — never made standard, so later Pis lacked it. `tools/power/low_idle.sh --psave-off` uses `iw`, runtime-only (lost at reboot) and `iw` is not on trixie images. | host unreachable; G4 console evidence at risk |
| logind `RemoveIPC=no` (units) | 2026-10-01, bm #97: pi's last ssh logout wiped `/dev/shm/bmcam` under stay_on | remote `set` acked but not applied |
| journald persistent (bench hosts) | 2026-10-02: nereus000 outage had no previous-boot log | no root cause possible |

## Items

| host | file | content |
|---|---|---|
| all | `/etc/NetworkManager/conf.d/90-wifi-powersave-off.conf` (nereus000: `90-hil-wifi-powersave.conf`) | `[connection]` / `wifi.powersave = 2` |
| bmcam units | `/etc/systemd/logind.conf.d/90-bmcam-removeipc.conf` | `[Login]` / `RemoveIPC=no` |
| bench hosts | `/etc/systemd/journald.conf.d/90-hil-persistent.conf` | `[Journal]` / `Storage=persistent` / `SystemMaxUse=200M` |

## Apply on a running host without waiting for a reboot

- Power save: `sudo nmcli general reload conf`, then re-activate the Wi-Fi connection DETACHED
  (`nohup sudo bash -c "sleep 2; nmcli connection up <conn>" &`), because `nmcli device modify wlan0
  802-11-wireless.powersave 2` is refused ("can't reapply"). The link drops for ~10 s.
- RemoveIPC: only at the next boot (do not restart logind on a running unit).
- journald: `sudo systemctl restart systemd-journald && sudo journalctl --flush`.

## Verify (after a REBOOT — the only proof that it persists)

```bash
ssh pi@<host> 'sudo dmesg | grep brcmf_cfg80211_set_power_mgmt | tail -1; systemd-analyze cat-config systemd/logind.conf | grep RemoveIPC; journalctl --list-boots --no-pager | tail -2'
```

PASS: the last power-mgmt line is `power save disabled` (NM disables it ~1 s after the driver enables it at
boot); units show `RemoveIPC=no`; bench hosts list the previous boot.
Proven on nereus000 2026-10-03 02:22Z: reboot → `power save enabled` @34.8 s → `disabled` @35.8 s.

## Restore

Delete the file; for power save also reload + re-activate (above); for RemoveIPC / journald reboot or
restart journald.
