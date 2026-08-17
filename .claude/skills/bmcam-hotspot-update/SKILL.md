---
name: bmcam-hotspot-update
description: Recover an offline field bmcam unit over an iPhone hotspot and run a remote software update — 2.4 GHz hotspot tricks, catching the Pi on Tailscale, the safe disarm, and which steps the human must run by hand. Use when a deployed unit is offline on Tailscale (no site WiFi) and someone is physically near it with a phone. For the update mechanics on an ARMED self-halting unit use bmcam-field-update; for brand-new units use bmcam-provision.
---

# bmcam Hotspot Recovery + Field Update

Purpose: a deployed bmcam unit (Pi Zero 2 W) has no internet — Tailscale shows
`offline, last seen Nd ago` — and a person with an iPhone is standing next to
it. Get the Pi online, SSH in, and update it. Proven on bmcam001 (Florida,
2026-07-31: offline 20 days → recovered → legacy HEIC runtime → RC runtime at
main 0d03a62, live image verified at Sofar).

Key mental model: **a dark unit is usually a connectivity problem, not a dead
unit.** The camera/Spotter path is cellular and works without WiFi; Tailscale
needs WiFi. Check the Sofar dashboard first — if images still arrive, the Pi
is healthy.

## Phase 0 — since Sprint12: can a REMOTE COMMAND fix it instead?

Halt mode and the transmit window are **no longer SSH-only** (they were the
two settings that forced the 2026-07-31 site visit). If the unit is running
Sprint12+ code with `bm_commands.enabled: true` (fleet default since
2026-07-31), try the cloud mailbox BEFORE anyone drives out:

- wrong/disabled halt → `hlt 1|2|3`, restore YAML with `hlt 0`
- window misconfigured / unit never transmits → `twn 2` (wide, the remote
  un-brick), restore with `twn 0`
- on-demand image or camera-vs-link diagnosis → `trg 2` (capture+send) /
  `trg 3` (reef reference, camera skipped)

Send: `python3 tools/sofar_send_command.py --spotter-id SPOT-XXXXX --id N
--cmd twn --value 2` (or the bm_command_gui retry engine). Re-send until
acked; latency is hours (~hourly [MS] mailbox drain). Full reference:
`docs/bmcam_command_reference.md`. A hotspot session is still required for:
units on pre-Sprint12 code, software updates, and anything in the
provisioning-only list (`REMOTE_CONFIG_AUDIT.md`).

Since Sprint13 (tables v5): anyone at the Spotter USB console — including
the customer — can send `help` (full generated command reference with
copy-paste examples) and `cfg` (resolved settings with per-row source).
Zero quota, prints on the console. If someone is on-site at a console,
have them run `help` before walking them through anything from memory:

    bm pub bmcam/cmd {"id":106,"c":"help"} 1 1

## Phase 1 — iPhone hotspot (the person on-site)

The Pi auto-joins only networks it already knows, so the hotspot must CLONE a
known SSID+password (usually Nick's bench WiFi). Checklist, in order:

1. **Force 2.4 GHz — the critical step.** Pi Zero 2 W radio is 2.4 GHz ONLY.
   Settings → Personal Hotspot → **"Maximize Compatibility" ON**. Without it,
   newer iPhones broadcast 5 GHz and the Pi will never see the network.
2. **Hotspot SSID = iPhone name**, must match byte-for-byte (case-sensitive).
   Change at Settings → General → About → Name. iOS Smart Punctuation turns
   `'` into a curly `'` that will NOT match `wpa_supplicant.conf` — disable
   Settings → General → Keyboard → Smart Punctuation before typing a name
   containing an apostrophe.
3. **Keep the hotspot discoverable**: stay ON the Personal Hotspot settings
   screen with the screen awake until the Pi connects (iOS stops advertising
   when the screen locks with no clients).
4. **Power-cycle the camera with the hotspot already up.** The Pi joins known
   networks most reliably at boot — and if the unit was wedged, this clears
   that too. A long-dark unit often will NOT join without this power cycle.
5. **Success signal on the phone**: blue "1 connection" banner/pill. No banner
   after ~3–5 min = SSID/password mismatch or the Pi isn't booting.
6. Phone within a few meters — the Zero 2 W antenna is weak and housings
   attenuate.

## Phase 2 — catch it on Tailscale (Mac side)

- The Mac App Store Tailscale has no CLI on PATH. Use:
  `/Applications/Tailscale.app/Contents/MacOS/Tailscale status|ping ...`
- The App Store build does NOT support `tailscale ssh` — use plain
  `ssh pi@bmcamNNN` (MagicDNS resolves it).
- Pre-stage a watcher BEFORE the power cycle (background loop pinging every
  ~20 s) so no one stares at a terminal.
- First SSH may print `# Tailscale SSH requires an additional check. To
  authenticate, visit: https://login.tailscale.com/a/...` — the HUMAN must
  open that URL and approve. The pending ssh completes after approval.
- Expect a DERP relay (e.g. `relay "mia"`, 150–400 ms). Fine for admin; scp
  of a 1.3 MB image takes ~10 s.

## Phase 3 — stabilize before touching anything

If the unit self-halts (bmcam000-style) follow bmcam-field-update's watcher
race. Even a non-halting unit boots into its `@reboot` cycle — disarm before
it transmits garbage or holds the camera. Hard-won safety rules:

- **Never `pkill -f <pattern>` where the pattern appears in your own ssh
  command line** — pkill matches the remote shell carrying the pattern text
  and kills your session mid-script (exit 255, remaining commands lost).
  Quote-split the pattern (`'main[_]pi_camera'`) or pkill by exact name.
- **Never pipe `crontab -l | sed ... | crontab -`** — if sed errors, an EMPTY
  crontab gets installed. Write to a file, verify, then `crontab file`. Take
  the armed backup FIRST (`crontab -l > backup_<TS>.txt`); it is the re-arm
  source later. macOS/BSD-vs-GNU sed `-E` paren differences are what bit us.
- Survey read-only before changing: processes, crontab, repo sha,
  `software_sha.txt`, deployed YAML values, `/dev/serial0` target, CMA.

## Phase 4 — the update

Use `tools/rc_field_update.sh` (stage via `/tmp`, never scp into the repo
tree) per the bmcam-field-update skill. Extra facts from bmcam001:

- A unit still on the LEGACY runtime (cron → `run_capture_cycle.sh` →
  `main_pi_camera.py`, old YAML schema with `image_pipeline`) needs MORE than
  the surgical script: the deployed YAML must be REPLACED with the modern RC
  schema (build from `BM_Devel_Pi/camera_schedule.yaml` at the target ref +
  device deltas; commit the result to `device_profiles/<unit>/`) and the cron
  line switched to `rc_run_capture_cycle.sh`.
- rc_field_update stage 4 FAILS if the device profile has no `bm_serial:`
  block — old RTC-era profiles don't. Fix the profile in the repo, don't
  hand-patch around it.
- Device deltas that matter: bmcam001 = `time_source: rtc` +
  `set_system_clock_from_spotter: false` (older bridge fw, good RTC);
  all others = `spotter_utc`. All units: America/New_York, window
  10:00–15:00, `capture_mode: progressive_jpeg`, bm_serial 384/1.0,
  manual focus 1.82, and (production model, 2026-07-31) REAL power_halt
  — every unit halts at cycle end; an external power cycle wakes it.
  With real halt, ANY cycle (even a bench --transmit test) halts the
  box: re-arm cron BEFORE the validation transmit, and treat SSH death
  ~2 min after the transmit finishes as SUCCESS.
- Remote commands can NOT change the daily transmit window — the v2 command
  table (`roi foc awb exp win txd cap src ping`) has no window command;
  `win` is the per-cycle run-time budget. Window changes need SSH (or a
  future command-table addition).

## Permissions: what the agent can and cannot run

The Claude Code permission classifier blocks some remote mutations. Do not
fight it — split the work:

- BLOCKED for the agent (hand the exact command to the human, staged and
  ready): overwriting the deployed `camera_schedule.yaml`
  (stage the new file to `/tmp/` on the Pi first, give the human a one-liner
  that backs up → parse-checks → installs), and `gh pr merge` to
  main/development.
- ALLOWED in practice: ssh read-only surveys, scp to `/tmp`, crontab edits
  via file, launching capture/transmit cycles, killing cycle processes.
- Some read-only ssh one-liners get blocked spuriously — rephrase (drop
  pgrep, use a running Monitor instead) rather than retrying verbatim.

## Phase 5 — verify like bmcam001

1. `rc_progressive_jpeg.py --print-config` gate.
2. `--capture-only` smoke → scp the native JPEG down and LOOK at it.
3. Detached live cycle: `nohup python3 -u rc_progressive_jpeg.py --transmit
   > cron_logs/<tag>.log 2>&1 &` (log on the Pi, never through the ssh pipe).
   Healthy: q90 accepted, ~142 msgs at 384/1.0, `sent=N/N complete=True`.
4. Sofar `api/sensor-data` for the unit's Spotter ID (token env
   `SOFAR_API_TOKEN_BM_REEF`; rows are hex; keep the date window tight).
   **13–30 min lag is NORMAL — do not diagnose failure from 0 rows early.**
   PASS = chunk indexes 0..N-1 + START + END.
5. Re-arm cron (RC line), leave rollback backups timestamped on the unit.

After the hotspot drops, the unit going dark on Tailscale is NORMAL.

## Known unit facts (2026-07-31)

| unit | Spotter | time_source | notes |
|---|---|---|---|
| bmcam001 | SPOT-33361C | rtc | Florida (Sombrero); node 0x57ef9a36411412f7 |
| bmcam002 | SPOT-33361C | spotter_utc | same Spotter; node 0x2f58e75e9f6554b5 |
| bmcam000 | SPOT-31593C | spotter_utc | bench; production config 2026-07-31 |
| bmcam003 | SPOT-33507C | spotter_utc | bench; production config 2026-07-31 |

Halt status: bmcam000/003 run REAL halt now; bmcam001/002 still have
halt disabled on-device — apply at the next hotspot session:
`ssh pi@bmcamNNN 'cd /home/pi/BM_Devel_Pi && cp camera_schedule.yaml \
  camera_schedule.yaml.before_halt_enable && sed -i \
  -e "/^power_halt:/,/^[a-z_]/{s/enabled: false/enabled: true/; \
  s/dry_run: true/dry_run: false/;}" camera_schedule.yaml'`

Mac-side python (python.org build) lacks root certs — use `curl` with a
`-K` config file (keeps the token off argv) for Sofar API queries.
