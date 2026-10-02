# G5 — outdoor solar 24 h, unattended (Release R1)

When: Thu 10/8 08:00 → Fri 10/9 08:00 PDT (+3 h completion tail to 11:00). Owner: Test Engineer.
Units: bmcam003 (SPOT-33507C), bmcam004 (SPOT-31593C) on RC1, production config (as G4).
**Spotter solar power, no nereus000, no human.** Commands only via cellular (backend Sofar lane).
Plan: RELEASE_PLAN §2 (G5), D1, D3, D4, D5. Evidence: `runs/g5_solar24h_<YYYYMMDD>/`.
After ship, G5 keeps running through the weekend as a soak (not a gate).

## Pass (RELEASE_PLAN)

D1, D3, D4 hold for 24 h; commands only via cellular.

## Criteria

| id | criterion | PASS when | evidence |
|---|---|---|---|
| G5.1 | production config, unattended | G4.1 read-back still true at 08:00 (from the G4 end record + first START `cfg=` hash); USB consoles disconnected from nereus000 by 08:00; no human action logged 08:00 → 08:00 | `gate.log`, `api/` |
| G5.2 | every wake happened | 24/24 windows per unit with a `<WS>` or START row at Sofar/backend (no console: the backend is the evidence) | `analysis/windows.csv` |
| G5.3 | D1 complete within 3 h | 100 % of media captured in the 24 h complete ≤ 3 h after capture | `analysis/media.csv` |
| G5.4 | D1 0 redundant heals | as G4.4 | `analysis/heals.csv` |
| G5.5 | heal cap 24/day | heal commands per Spotter ≤ 24 in the 24 h | `analysis/heals.csv` |
| G5.6 | D3 over cellular | ≥ 2 `trg` per unit sent through the backend send endpoint (Sofar lane), each acked and → one complete media row ≤ 3 h | `api/trg_*.json`, `analysis/media.csv` |
| G5.7 | remote recovery reachable | a cellular `ping` near the end (≥ 06:00 Fri) answered by each unit | `api/ping_*.json` |
| G5.8 | D4 visible | every command, ack and heal in the 24 h on logs.html | `analysis/logs_check.csv` |
| G5.9 | power held | Spotter battery / solar data (Sofar API, or the SD card after) show no brown-out; units woke on schedule through the night | `analysis/power.csv` |

## Preconditions

- [ ] G4 PASS (or its re-run).
- [ ] Nick: Spotters on solar, box outside, consoles unplugged from nereus000 (note: check whether
      unplugging the USB console changes the Spotter's power path before relying on it overnight).
- [ ] Cellular spend for 24 h × 2 units + 4 trg + 2 ping + heals ≤ the plan; else Nick decides first.
- [ ] Backend send path live for both Spotters (per-Spotter `remote_commands` setting on; `BM_COMMAND_SEND`).
- [ ] Never send to SPOT-33361C (field): every send goes through `hil/tools/hil_sofar_change.sh`
      (rig allow-list guard; `hil/tools/test_hil_guards.sh` must print 0 failures before G5).

## Steps

1. 07:30 Thu: confirm last G4 state, both units' last START hash == RC1 config hash. Log 08:00 start.
2. Trg schedule (cellular, backend `/send`): unit A at 10:00 and 22:00, unit B at 11:00 and 23:00.
   Cellular commands land only if the unit's listen span overlaps the Spotter's report minute
   (spec §6): send ≥ 10 min before a window, record `sent_at`, ack time, media key.
3. Watch every ~3 h from the backend only (media completeness, heals, acks). Do not intervene: a
   failure is recorded, not fixed (unattended is the test). Exception: a unit dark ≥ 3 windows →
   tell the EM (Nick decides whether to stop).
4. 06:00 Fri: cellular `ping` each unit. 08:00: end of captures; 11:00: end of the D1 tail.
5. After: Nick pulls the Spotter SD cards (power + MS logs) when convenient; analysis via the
   nereus-spotter-sd-analysis skill (G5.9 can be finished from the cards).

## Restore

None for the gate (units keep soaking in production config). Post-weekend: console back on
nereus000, snapshot both units, compare with the RC1 freeze snapshot.
