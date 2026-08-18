# Sprint16 — NetworkManager WiFi control (AP / HQ / customer WiFi)

Status: DRAFT 2026-08-18 (from Nick's written spec, stated twice
2026-08-18; expands TODO-BM-012 and Sprint15 tracker §6). AWAITING
NICK'S SPEC-SESSION REVIEW — no code before that.
Branch (proposed): feature/sprint16-network-wifi, off development.

## Nick's constraints (2026-08-18, quoted intent)

1. All controlled by Bristlemouth commands, on NetworkManager (the
   whole fleet is Trixie/NM as of the 2026-08-18 unification).
2. Behavior 1 — **Nereus HQ WiFi**: log on to the office WiFi using
   credentials stored on the unit.
3. Behavior 2 — **AP mode**: SSID = the unit hostname, NO password
   (confirmed possible: NM open hotspot). Goal: field techs just tap
   the unit's name in their WiFi list.
4. Behavior 3 — **customer WiFi, ephemeral**: user enters a custom
   SSID + password; the unit joins the customer's WiFi for THAT power
   cycle and forgets it on power cycle.
5. **Boot default is a ship switch**: a YAML control AND a settings-UI
   control choosing "default to Nereus HQ wifi" or "default to AP".
   Nick ships customer units defaulting to AP so customers can always
   reach the device AP to download videos.

## Inherited doctrine (not up for relitigation)

- Revert-first (Sprint15 D-S15-10): any REMOTE flip away from a
  working network arms a VERIFIED auto-revert timer BEFORE flipping.
  Reboot returns the unit to its YAML boot default — the universal
  un-brick, and exactly what makes ephemeral customer creds (4) safe.
- Command overlay doctrine: network mode flips are immediate-apply
  (like wap/trg, documented exception); the boot DEFAULT lives in YAML
  and is never rewritten by commands (settings GUI edits it with
  timestamped backups, restart-to-apply — Sprint15 GUI model).
- One YAML island, loud validation, extension-not-fork.

## Known constraints / open questions for the spec session

- BM message size vs SSID+PSK length for the remote (BM) entry path of
  behavior 3; the PRIMARY entry path is the settings GUI (customer is
  already on the AP), which has no size limit.
- Credential handling: HQ creds provisioned as an NM profile (not in
  the repo, not in YAML); customer creds never persisted (NM in-memory
  connection — verify `nmcli connection add save no` on Trixie's NM
  version); log redaction for anything carrying a PSK.
- Open-AP exposure (accepted): gallery + settings reachable by anyone
  in radio range while AP is up. Mitigation candidates (spec session):
  AP-mode banner in UI, optional WPA2 fallback knob, none-for-MVP.
- Command surface (spec session): extend `wap` values (0=boot default,
  1=AP, 2=HQ) in tables v7 + a separate credential-bearing command for
  behavior 3, vs a new `net` command family. Ack semantics when the
  flip drops the sender's own network path.
- Tailscale interaction: AP mode has no internet — tailnet drops while
  AP is up (same as Sprint15 wap; timer + boot default recover it).

## Acceptance gates (bench, attended where WiFi flips)

1. Unit suite green incl. all existing tests (extension, not
   regression).
2. Behavior 1: BM command joins HQ WiFi from AP mode and back.
3. Behavior 2: open AP, SSID = hostname, iPhone joins with no
   password, gallery + download work at the AP address.
4. Behavior 3: customer SSID/PSK entered via settings GUI joins that
   network for the session; after power cycle the creds are GONE
   (verified: no NM profile persists) and the unit is back on its
   YAML boot default.
5. Boot default honored for BOTH YAML values across reboot; settings
   GUI can flip the default (with backup + restart-to-apply).
6. Revert-first proven: bogus SSID via the remote path → auto-revert
   restores the prior network within the timer.
7. Docs: bmcam_command_reference.md updated (replaces the wap
   NOT-FIELD-READY entry), skills updated, PR → development with
   tracker artifacts.

## Not in scope

Persisting customer WiFi across power cycles. Captive portal /
onboarding pages. WPA-Enterprise. Simultaneous AP+client (AP-STA).
Bullseye support (fleet is Trixie-only as of 2026-08-18).
