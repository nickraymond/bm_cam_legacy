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
- [ ] `status` / `default` / `ap` / `hq` / `join <ssid> <psk>` /
      `revert` verbs, NM-backed; revert-first timer arm+verify
      preserved from D-S15-10; open AP with SSID=$(hostname)
- [ ] Ephemeral customer join proven non-persistent across reboot
- [ ] Unit tests (nmcli subprocess-injected, no hardware)

## 2. YAML island + settings GUI
- [ ] `network:` island (boot default: nereus_hq | ap) + validation
- [ ] Boot-time apply of the default
- [ ] Settings GUI: default selector + customer-WiFi entry form
      (backup + restart-to-apply model unchanged)
- [ ] Unit tests

## 3. BM command surface
- [ ] Tables bump + immediate-apply wiring + help/cfg rows
- [ ] Credential-bearing path for behavior 3 (size cap solved or
      explicitly GUI-only for MVP)
- [ ] Unit tests

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
