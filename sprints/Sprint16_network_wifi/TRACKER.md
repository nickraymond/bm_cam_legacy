# Sprint16 — Tracker

Tick a box only when an artifact proves it. SPECCED 2026-08-18.

## 0. Setup
- [x] Spec session with Nick 2026-08-18: boot model (strict YAML
      default + session-only switches + 90 s AP fallback, Nick's own
      proposal), wap v7, GUI-only creds, open AP + banner — locked in
      SPEC.md
- [x] DESIGN.md D-S16-1..7 written
- [x] Bench prerequisites: bmcam000 (dev mode, Trixie, tailnet OK —
      done 2026-08-18); HQ NM profile ADOPTED 2026-08-18: the netplan
      profile (netplan-wlan0-Ford, /run-generated so a rename would
      not persist) was CLONED to a persistent `nereus-hq` connection
      (nmcli clone — creds copied on-unit, never seen off-unit;
      original left intact as fallback, key-mgmt wpa-psk verified).
      Sprint16 runtime staged to the unit (dev-mode scratch copy;
      blessed deploy after merge), network island added to unit YAML
      (backup camera_schedule.yaml.before_sprint16_net_*).
      UNATTENDED BENCH PASS (2026-08-18T06:20Z): `default nereus_hq
      90` applied live on the steady-state path — switched netplan →
      nereus-hq profile with NO SSH drop, mode file client:nereus-hq,
      stored default correct, tailnet intact, cfg row renders
      "client WiFi (nereus-hq)". Hardware note: fresh provisions lack
      ~/BM_Devel_Pi/cron_logs until first cron run — the script
      handles it (mkdir -p in log()); only external shell redirects
      into that dir need the mkdir first.
      STILL NEEDED: Nick's iPhone for the AP-join gates (§4)

## 1. nmcli network layer (network_ap.sh rewrite)
- [x] `status` / `default` / `ap` / `hq` / `join <ssid> <psk_file>` /
      `revert` / `disarm` verbs, NM-backed (in-memory `save no` cons;
      NM shared mode = no hostapd/dnsmasq); revert-first timer
      arm+verify preserved from D-S15-10 (remote flips REFUSE without
      it); open AP SSID=$(hostname); 90 s AP fallback in `default`;
      join failure auto-raises the AP for retry; tcp/22 dropped on
      wlan0 while AP up (nft best-effort); PSK via root-0600
      passwd-file, deleted after use, never argv — 2026-08-18
- [ ] BENCH: ephemeral customer join proven non-persistent across
      reboot (`nmcli con` empty of bmcam-* after power cycle)
- [x] Unit tests: hook argv construction + PSK-hygiene assertions
      (tests/test_network_config.py; the script's nmcli calls are
      proven on the bench, not mocked — same doctrine as encoders)

## 2. YAML island + settings GUI
- [x] `network:` island (default: nereus_hq | ap, ap_fallback_s,
      ap_timeout_min) — network_config.py, house tiny-parser (no
      PyYAML); absent island = None = boot no-op (extension contract);
      present-but-invalid raises; GUI saves validate it too
- [x] Boot-time apply in rc_progressive_jpeg main() (fire-and-forget,
      both stills + video paths; skipped for --print-config)
- [x] Settings GUI: "WiFi at power-on" selector (YAML write, backup +
      restart-to-apply unchanged) + session-only join form (SSID/PSK,
      WPA2 length checks) + open-AP banner on gallery AND settings
      (live /run/bmcam_net/mode) — videoui_server.py, video_settings.py
- [x] Unit tests: island parse/validate/dispatch (test_network_config),
      GUI routes incl. injected join_fn + banner tracking
      (test_video_settings)

## 3. BM command surface
- [x] wap v7: 0=boot default / 1=AP now / 2=HQ now; TABLES_VERSION
      6→7 (version-guard tests updated WITH the bump); immediate-apply
      wiring unchanged; help/cfg rows updated (open hotspot wording,
      72-char console width respected); cfg reads live mode file
      (ap / client:<name> / joining)
- [x] Behavior-3 credentials: GUI-only for MVP (locked in spec) —
      no credential-bearing BM command this sprint
- [x] Unit tests: table shape, v=2 valid / v=3 invalid, argv with and
      without island (test_wap_command updated for v7)

Suite after chunks 1-3: **691 OK (skipped=1)** — 673 baseline + 18
new, all existing tests untouched except the designed version-guard
and wap-shape updates (2026-08-18).

## 4. Bench validation (attended rehearsal with Nick, 2026-08-18,
##    bmcam000 recording video THROUGHOUT — network_ap.log is the
##    evidence trail, 06:59-07:20Z)
- [x] Gate 3: open AP — SSID `bmcam000`, no password, iPhone joined,
      gallery loaded w/ open-network banner, clip played + full-res
      download (Nick, rounds 2+5); ACT-LED fast-blink indicator
      confirmed visually, normal flicker after revert
- [x] Auto-revert timer: PASS TWICE (10-min and 15-min timers armed
      pre-flip, fired on schedule, HQ + tailnet restored; log lines
      06:34:55 + 07:08:04→revert)
- [x] Gate 4: customer join via /settings — wrong-password attempt
      FAILED SAFE (07:11:37 "raising AP so the user can retry", AP +
      LED returned on their own, Nick observed); correct join
      07:13:31 (session con `bmcam-customer`, tailnet rode it);
      FORGET-PROOF: power cycle → zero bmcam-* connections remain,
      unit back on nereus-hq default
- [x] Recording unaffected by ALL network flips/reboots (clips
      accumulated continuously; crash contract absorbed every reboot)
- [x] TWO LIVE BUGS found + fixed + regression-tested mid-rehearsal:
      (1) boot-default re-applied on every RUNTIME start, yanking
      wlan0 mid-AP-session → once-per-boot /run marker;
      (2) Safari replayed a cached /restart POST on refresh →
      PRG 303 on all settings POSTs. Plus boot-race fix (nm-online
      guard, autoconnect-same-SSID counts as HQ, nmcli stderr logged)
      and the ACT-LED AP indicator (Nick feature request, live-coded)
- [x] Suite green: 692 OK (skipped=1)
- [ ] Gate 2 REMAINING: wap 0/1/2 over the REAL BM bus (needs a
      Spotter hosting bmcam000; daemon immediate-apply path is
      unit-tested, script paths bench-proven — the bus delivery is
      the only untested hop)
- [ ] Gate 5 REMAINING (SHIP-PREP STEP, Nick 2026-08-18): boot with
      `default: ap` + GUI default round-trip. Nick keeps the bench
      unit on nereus_hq for image-quality work — FLIP TO `ap` (GUI or
      YAML) BEFORE SHIPPING ANY UNIT TO A CUSTOMER. rc_field_template
      already ships `default: ap`
- [ ] Gate 6 partial: AP-raise path proven via join-failure re-raise
      + the (buggy-boot) fallback firing; a clean HQ-absent 90 s
      fallback run is future evidence

## Bench findings for the docs (2026-08-18)
- Both office routers broadcast SSID "Ford": a client-mode unit's LAN
  IP roams between 192.168.1.x (UniFi) and 192.168.86.x (Nest) across
  boots. The tailnet name is the only stable address — and this is the
  argument for the customer ship default being AP.
- Settings GUI edits-not-authors: a unit's YAML must CONTAIN every GUI
  key (video island added to rc_field_template; bmcam000 patched).

## 5. Wrap
- [x] docs: command reference wap v7 rewrite, tracker evidence
- [ ] PR → development, Nick review
- [ ] Post-merge: re-point bmcam000 to blessed git bits (it runs a
      dev-mode scratch copy of this branch)

## Hazards carried in
- Every mode flip can drop the operator's own connection — attended
  bench work; timers armed BEFORE flips, no exceptions (D-S15-10).
- Open AP exposes gallery/settings to radio range while active.
- PSKs must never land in logs or the repo; HQ creds are
  unit-provisioned NM profiles only.
- bmcam003/004 are running Sprint15 HIL — do not disturb; Sprint16
  bench work happens on bmcam000 (dev mode).
