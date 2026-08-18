# Sprint16 — Tracker

Tick a box only when an artifact proves it. SPECCED 2026-08-18.

## 0. Setup
- [x] Spec session with Nick 2026-08-18: boot model (strict YAML
      default + session-only switches + 90 s AP fallback, Nick's own
      proposal), wap v7, GUI-only creds, open AP + banner — locked in
      SPEC.md
- [x] DESIGN.md D-S16-1..7 written
- [ ] Bench prerequisites: bmcam000 (dev mode, Trixie, tailnet OK —
      done 2026-08-18), Nick's iPhone available for AP joins; HQ NM
      profile = the unit's EXISTING office-WiFi connection, adopted by
      rename to `nereus-hq` (D-S16-5 — no password re-entry needed,
      Nick 2026-08-18)

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

## 4. Bench validation (attended — WiFi flips kill the session)
- [ ] Gate 2: AP → HQ → AP via BM commands
- [ ] Gate 3: open AP, iPhone taps hostname SSID, gallery + download
- [ ] Gate 4: ephemeral customer WiFi + power-cycle forget
- [ ] Gate 5: boot default both values + GUI flip
- [ ] Gate 6: bogus-SSID auto-revert
- [ ] Suite green

## 5. Wrap
- [ ] docs: command reference (replace wap NOT-FIELD-READY), skills
- [ ] PR → development, Nick review

## Hazards carried in
- Every mode flip can drop the operator's own connection — attended
  bench work; timers armed BEFORE flips, no exceptions (D-S15-10).
- Open AP exposes gallery/settings to radio range while active.
- PSKs must never land in logs or the repo; HQ creds are
  unit-provisioned NM profiles only.
- bmcam003/004 are running Sprint15 HIL — do not disturb; Sprint16
  bench work happens on bmcam000 (dev mode).
