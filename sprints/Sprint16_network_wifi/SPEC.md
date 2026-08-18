# Sprint16 — NetworkManager WiFi control (AP / HQ / customer WiFi)

Status: SPECCED 2026-08-18 (spec session with Nick — model + all four
decisions confirmed, incl. the 90 s AP fallback). Expands TODO-BM-012;
supersedes the Sprint15 tracker §6 wap deferral.
Branch: feature/sprint16-network-wifi, off development.

## Nick's constraints (2026-08-18, binding)

1. All controlled by Bristlemouth commands, on NetworkManager (the
   whole fleet is Trixie/NM as of the 2026-08-18 unification).
2. Behavior 1 — **Nereus HQ WiFi**: join the office network using
   credentials stored on the unit.
3. Behavior 2 — **AP mode**: SSID = the unit hostname, NO password —
   field techs and customers just tap the unit's name in their WiFi
   list. Gallery + settings served on the AP.
4. Behavior 3 — **customer WiFi, ephemeral**: user-supplied SSID +
   password, valid for the CURRENT power cycle only, forgotten on
   power cycle.
5. Boot default is a ship switch (YAML + settings-GUI control):
   production units default to AP; the dev fleet defaults to
   Nereus HQ for remote access from the office.

## The model (locked with Nick, 2026-08-18)

- The YAML holds ONE value: the boot default — `ap` (production/ship)
  or `nereus_hq` (dev fleet, current setting).
- Every runtime switch is SESSION-ONLY: `wap 1`/`wap 2` over BM, or a
  customer entering WiFi credentials in the settings page. Power
  cycle = back to the YAML default. A shipped unit always wakes up as
  an AP regardless of yesterday's network.
- Changing the DEFAULT is the rare, deliberate act (ship prep, dev
  setup): settings GUI writes the YAML with its usual timestamped
  backup + restart-to-apply. (SSH works too; BM cannot change the
  default — session flips only.)
- **90 s AP fallback (Nick-proposed + confirmed)**: if the default
  network is not joined within ~90 s of boot, raise the AP anyway.
  Applies to any client-WiFi default; units defaulting to `ap` boot
  straight into AP with no wait. A unit can never boot into
  unreachability.

## Decisions (spec session, 2026-08-18)

1. **Boot model**: as above (strict YAML default + session-only
   switches + 90 s AP fallback).
2. **Command surface**: extend `wap` (tables v7): `0` = return to boot
   default · `1` = AP now · `2` = Nereus HQ now. Immediate-apply
   (documented exception, as merged in v6). No new command family.
3. **Customer credentials entry**: settings GUI ONLY for MVP (customer
   is already on the AP; no BM size cap, no PSK on the bus). A
   credential-bearing BM command is future work, explicitly not a
   blocker (Nick).
4. **AP security**: open network + an "open network" banner in the UI;
   only port 8080 is exposed on the AP (no SSH). Optional WPA2 knob is
   future work if a customer asks.
5. **HQ credentials**: provisioned per-unit as a normal NetworkManager
   profile (bmcam-provision skill step). Never in the repo, never in
   the YAML, never in logs.
6. **Auto-revert doctrine carried from D-S15-10**: any REMOTE (BM)
   flip arms a VERIFIED auto-revert timer BEFORE flipping (default
   60 min), revert target = the boot default. Reboot is the second
   un-brick.

## YAML (the island)

```yaml
network:
  default: nereus_hq       # ap | nereus_hq  (ship value: ap)
  ap_fallback_s: 90        # raise AP if the default network is not
                           # joined this many seconds after boot;
                           # ignored when default is ap
  ap_timeout_min: 60       # auto-revert timer for REMOTE flips
```

## Acceptance gates (bench, attended where WiFi flips)

1. Unit suite green incl. all existing tests (extension, not
   regression).
2. `wap 2` from AP joins HQ; `wap 1` from HQ raises AP; `wap 0`
   returns to the YAML default — all session-only.
3. Open AP: SSID = hostname, iPhone joins with no password, gallery +
   full-res download work; UI shows the open-network banner.
4. Customer WiFi via settings GUI joins for the session; after a power
   cycle the credentials are GONE (no NM profile persists) and the
   unit is back on its YAML default.
5. Boot default honored for BOTH values across reboot; settings GUI
   flips the default (backup + restart-to-apply).
6. 90 s fallback: default = nereus_hq with the HQ network absent →
   AP is up within ~2 min of boot.
7. Revert-first: bogus/unreachable target via BM → auto-revert
   restores the boot default within the timer.
8. Docs: bmcam_command_reference.md (replaces the wap NOT-FIELD-READY
   entry), skills updated, PR → development with tracker artifacts.

## Not in scope

Persisting customer WiFi across power cycles. BM-carried credentials.
Captive portal. WPA-Enterprise. AP+client simultaneous (AP-STA).
Bullseye support. Changing the boot default over BM.
