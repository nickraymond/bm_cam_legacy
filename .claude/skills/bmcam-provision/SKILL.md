---
name: bmcam-provision
description: Provision a brand-new bmcam unit (Pi Zero 2 W) from fresh SD flash to a validated RC runtime — Tailscale, deps, repo clone, deploy_rc_runtime.sh --fresh, and the validation ladder. Use when setting up a new bmcamNNN module or re-provisioning after a re-flash. For updating an EXISTING armed unit (code/config rollout), use the bmcam-field-update skill instead.
---

# bmcam Provision (new unit setup)

Purpose: take a freshly flashed Pi Zero 2 W from "answers on the LAN" to "validated RC runtime on the tailnet, cron armed". Human-in-the-loop at three points: the Pi password (`ssh-copy-id`), the Tailscale auth URL click, and physical camera wiring.

Proven on: bmcam003, Raspberry Pi OS trixie (Debian 13), 2026-07-26.

## Pre-flight (on the Mac, before touching the Pi)

1. **Confirm the deploy tooling is on the ref you'll clone.** The Pi clones `main` over HTTPS (the repo is public — no deploy key needed). Verify:
   `git ls-tree origin/main --name-only tools/ | grep deploy_rc_runtime.sh`
   If missing, the RC tooling branch hasn't been merged — stop and get it onto `main` first (PR merge; the agent may be permission-blocked from `gh pr merge`, so hand the human the command).
2. **Confirm the target.** Do not touch production units (`nereus000`, `bmcam000`). New units are `bmcamNNN` — confirm the number with the human.

## Phase 1 — Tailscale

Follow the existing `pi-tailscale-setup` skill end-to-end (LAN discovery, `ssh-copy-id` by the human, board verification, Tailscale install, detached `tailscale up --hostname=bmcamNNN`, hand the auth URL to the human, verify `ssh pi@bmcamNNN` over the tailnet).

**Trixie gotcha found on bmcam003:** fresh Raspberry Pi OS trixie images may NOT give `pi` passwordless sudo (`sudo -n true` fails). The agent cannot type the password, so hand the human:
```
ssh -t pi@bmcamNNN.local 'echo "pi ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010_pi-nopasswd >/dev/null && sudo chmod 440 /etc/sudoers.d/010_pi-nopasswd && sudo -n true && echo NOPASSWD_OK'
```
Wait for `NOPASSWD_OK` before proceeding — everything below needs non-interactive sudo.

## Phase 2 — Dependencies

Fresh trixie is missing git and the Python camera stack (`yaml` is present; `serial`, `PIL`, `picamera2` are not). Install via apt (never pip — these must match the system libcamera):
```
ssh pi@bmcamNNN 'sudo apt-get install -y git python3-serial python3-pil python3-picamera2'
```
Sanity: `which rpicam-still` should already exist on Raspberry Pi OS; if it doesn't, the wrong OS image was flashed — stop.

## Phase 3 — Clone

```
ssh pi@bmcamNNN 'mkdir -p ~/repos && cd ~/repos && git clone https://github.com/nickraymond/bm_cam_legacy.git && cd bm_cam_legacy && git log --oneline -1'
```

## Phase 4 — BM serial UART boot config (BEFORE deploy — needs a reboot)

`bm_serial.py` hardcodes `/dev/ttyAMA0` (the PL011). A fresh OS image binds the PL011 to Bluetooth, points `/dev/serial0` at the mini-UART, and puts a kernel console on the header pins — any BM transmit crashes on port open (found on bmcam003, Sprint09). Fix it NOW, while the `@reboot` cron is not yet armed — the required reboot is still safe. (After deploy `--install-cron`, a reboot starts a capture cycle and `power_halt` HALTS the box.)

```
ssh pi@bmcamNNN 'cd ~/repos/bm_cam_legacy && ./tools/setup_bm_uart.sh'
ssh pi@bmcamNNN 'sudo reboot'
```
The script backs up `config.txt`/`cmdline.txt` to `/home/pi/backups`, adds `dtoverlay=disable-bt` (with `enable_uart=1`), strips `console=serial0,115200`, and disables `hciuart`. After the Pi returns (~1–2 min), verify — must print `CHECK PASS`:
```
ssh pi@bmcamNNN 'cd ~/repos/bm_cam_legacy && ./tools/setup_bm_uart.sh --check'
```
(The `bm_serial` open test inside `--check` is skipped at this point — the runtime isn't deployed yet. It runs for real in the validation ladder.)

## Phase 5 — Deploy

Dry-run first, real run second (the script is conservative: backs up runtime + crontab, copies only manifest files):
```
ssh pi@bmcamNNN 'cd ~/repos/bm_cam_legacy && ./tools/deploy_rc_runtime.sh --fresh --profile rc_field_template --install-cron --dry-run'
ssh pi@bmcamNNN 'cd ~/repos/bm_cam_legacy && ./tools/deploy_rc_runtime.sh --fresh --profile rc_field_template --install-cron'
```
Verify artifacts (never trust exit codes alone):
- `/home/pi/BM_Devel_Pi/` contains the manifest file set + `camera_schedule.yaml`
- `software_sha.txt` matches the cloned commit
- `crontab -l` shows `@reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh`
- crontab backup exists in `/home/pi/backups/`

**Field update on an existing unit** is the same script with no flags (config + crontab untouched, HEIC left for config-gated rollback): `cd ~/repos/bm_cam_legacy && git pull && ./tools/deploy_rc_runtime.sh`

## Phase 6 — Validation ladder

```
cd ~/repos/bm_cam_legacy && ./tools/setup_bm_uart.sh --check
cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config
cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --capture-only
```
`setup_bm_uart.sh --check` is the transmit-capable gate: it exits nonzero unless `/dev/serial0 -> ttyAMA0`, no serial console is live, AND `BristlemouthSerial()` actually opens `/dev/ttyAMA0` (the runtime is deployed now, so the open test runs). A unit that passes capture but fails this WILL crash on its first `--transmit` in the field — do not skip it. `--print-config` must resolve the full RC config (quality ladder, budget, power_halt, geometry). `--capture-only` must leave a plausible-size JPEG in `/home/pi/BM_Devel_Pi/images/` — logs alone with no JPEG is a FAIL (on bmcam003 that meant the camera ribbon wasn't connected).

**power_halt halts the box after ANY cycle, not just `--transmit`.** The M6 halt runs in a `finally` block keyed on the `power_halt: enabled` config (rc_field_template default: enabled, not dry-run). On bmcam003 a bench `--capture-only` run captured fine and then cleanly halted the Pi — SSH drops with "Connection closed by remote host" and the board needs a physical power cycle. Expect this; warn the human before running any cycle command, and verify artifacts after the power cycle.

## Camera wiring / reboot safety

- CSI ribbons are never hot-plugged. To wire the camera: **disable the RC cron first**, halt, wire, boot, validate, re-enable. With the `@reboot` cron armed and `power_halt` enabled, a reboot can start a cycle that halts the box out from under you.
  - Disable: `crontab -l | sed 's|^@reboot /usr/bin/flock|#BENCH-DISABLED @reboot /usr/bin/flock|' | crontab -`
  - Re-enable: `crontab -l | sed 's|^#BENCH-DISABLED @reboot|@reboot|' | crontab -`
- After any reboot the Pi takes ~1–2 min to reappear on the tailnet; wait with an `until ssh …; do sleep 5; done` loop, not repeated manual attempts.

## Success criteria

- `ssh pi@bmcamNNN` works over the tailnet (not LAN IP) with key auth.
- `tools/setup_bm_uart.sh --check` prints `CHECK PASS` (including the `bm_serial` open test) after the runtime is deployed.
- `--print-config` and `--capture-only` both pass; a real JPEG exists in `images/`.
- `crontab -l` has the RC `@reboot` line ACTIVE (not `#BENCH-DISABLED`) before you walk away.
- No lingering `tailscale up` process; `/tmp/ts_up_root_*.log` removed.

## Known failure modes

- `Permission denied (publickey,password)` → human runs `ssh-copy-id`.
- `sudo: a password is required` → trixie NOPASSWD gotcha above.
- `no cameras available` from rpicam → ribbon not seated / not wired; power down to fix.
- `Pipeline handler in use by another process` → the RC cron cycle owns the camera; check `pgrep -af rc_` and wait or disable cron.
- `[Errno 2] could not open port /dev/ttyAMA0` (or `/dev/serial0 -> ttyS0`) → Phase 4 UART boot config was skipped or the reboot hasn't happened; run `tools/setup_bm_uart.sh`, reboot (cron disarmed!), re-check.
- `bm_serial` open test fails but `/dev/ttyAMA0` exists → an RC cycle may own the port; check `pgrep -af rc_`.
- Empty `images/` with only `.stderr.log`/`.stdout.log` files → capture failed; read the stderr log, do not proceed.
