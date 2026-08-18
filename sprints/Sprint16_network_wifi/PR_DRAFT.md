# Sprint16: NetworkManager WiFi control — AP / HQ / ephemeral customer WiFi

PR: `feature/sprint16-network-wifi` → `development`

## What this is

The fleet's WiFi behavior, specced with Nick 2026-08-18 (SPEC.md,
DESIGN.md D-S16-1..7) and bench-rehearsed the same day: one YAML boot
default (`ap` for customer ships, `nereus_hq` for the dev fleet), with
every runtime switch session-only — a power cycle always comes home.

| Piece | What it does |
|---|---|
| `network_ap.sh` (rewrite) | nmcli-only: `default` (with the 90 s AP fallback — a unit can never boot unreachable), open `ap` (SSID = hostname), `hq`, session-only `join`, revert-first timer verbs; ACT-LED fast-blink AP indicator; ssh blocked on the AP interface; PSK via root-0600 passwd-file, never argv/logs |
| `network_config.py` (new) | `network:` island tiny-parser (absent island = pre-Sprint16 no-op) + once-per-boot default apply |
| `rc_progressive_jpeg.py` | boot-default dispatch after overlay resolution (fire-and-forget, both modes) |
| `command_tables.py` v7 | `wap 0/1/2` = boot default / AP now / HQ now (immediate-apply wiring unchanged) |
| Settings GUI | "WiFi at power-on" selector, customer WiFi join form (session-only, WPA2 checks), open-network banner; ALL POSTs now PRG (303) |
| YAML | dev island (`nereus_hq`) in camera_schedule.yaml; ship island (`ap`) + video island in rc_field_template |

## Bench rehearsal (bmcam000, attended with Nick, 2026-08-18)

PASSED live: open-AP join from an iPhone (no password), gallery +
full-res download with banner, auto-revert timer twice (10 + 15 min),
wrong-password join failing safe (AP re-raises itself for retry),
correct customer join (session connection), power-cycle forget (zero
remnant connections), LED indicator, and continuous recording through
every flip and reboot.

Two bugs found live, fixed, regression-tested:
1. The boot-default re-applied on every runtime start and yanked wlan0
   mid-AP-session → once-per-boot `/run` marker.
2. Safari replayed a cached `/restart` POST on page refresh, rebooting
   the camera → Post/Redirect/Get on every settings POST.

Plus a boot race (network dispatch before NM was ready) → `nm-online`
guard + autoconnect-same-SSID counts as home + nmcli stderr logged.

## Remaining (tracked in TRACKER.md §4)

- `wap` over the REAL BM bus (needs a Spotter hosting the unit).
- **SHIP-PREP STEP (Nick): flip `network.default` to `ap` on every
  customer unit before shipment** — the dev fleet deliberately stays
  on `nereus_hq` for remote office access; rc_field_template already
  ships `ap`.
- Clean HQ-absent 90 s fallback run; `default: ap` boot + GUI default
  round-trip (deferred with the ship-prep flip).
- Post-merge: re-point bmcam000 to blessed git bits (currently a
  dev-mode scratch copy of this branch).

## Tests

692 OK (skipped=1) — 673 pre-sprint baseline + 19 new (network island,
PSK hygiene, wap v7, GUI routes incl. the PRG regression); the only
edited existing tests are the designed version-guard bumps (v6→v7) and
the wap-shape asserts updated WITH the table change.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
