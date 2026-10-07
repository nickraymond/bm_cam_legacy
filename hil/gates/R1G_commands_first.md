# R1G — R1 re-gate, COMMANDS FIRST (bmcam003 / SPOT-33507C)

Ruling (Nick, EM chat 2026-10-03 ~13:50 PDT; RELEASE_PLAN §2b, bm #124): R1 exits on command resilience. The 3 h D1
target moves to C1 (`C1_comms_reliability.md`). Rig: the repair rig bmcam003 / SPOT-33507C, production hourly bus,
per_boot + real halt, cap 126, on the R1 candidate (development + bm #121 once merged). Owner: Test Engineer.
Evidence: `runs/r1g_commands_<YYYYMMDD>/` (the #121 gate itself lives in `runs/r1fix_cmdres_20261003/`).

## Command load (product path, unattended)

`hil-r1-cmdres.timer` (nereus000): `set mode.media` every hour + `trg` every 3 h, backend Sofar lane, BMCAM_003 only.
Heal auto-send on. No console sends, no ssh writes.

## Criteria (all over ≥ 24 consecutive wakes)

| id | criterion | PASS when | evidence |
|---|---|---|---|
| R1G.1 | every command confirmed at the backend | 100 % of commands end in a confirmed backend status: direct ack, re-sent `d:1` ack (original `h`), or heartbeat/`<CF>` hash (`in_effect`); guarded keys may sit at `awaiting_cfm`. 0 `late` / `superseded` for a command the console shows ran | `hil_cmd_ledger.py` (backend status vs console OK) |
| R1G.2 | every trg accounted for | each trg → its clip delivered, OR the unit reports `tr=…:budget` / `tr=…:fail` and the backend shows it. 0 silent (no clip and no outcome) | ledger + `hil_media_table.py` + cycle logs |
| R1G.3 | no SSH needed | 0 writes over ssh to the unit during the window (reads of cron_logs allowed) | gate.log |
| R1G.4 | every clip completes eventually, 0 redundant heals | 100 % of media rows captured in the window complete by window end + 6 h; 0 heal commands asking for chunks the backend held complete at send time | `hil_media_table.py`, `heals.csv` (G4 method) |
| R1G.5 | no regression | 24/24 wakes, wake→halt ≤ 540 s, START/END per wake, heals sent and served | wake reports, cycle logs |

Measured, not gated: command lag in wakes, arrival minute vs report minute, D1 minutes (for C1).

## Order

1. bm #121 gate (`R1F_cmd_resilience.md` § fix verification, ≥ 6 wakes) → EM merges #121 on PASS.
2. bmcam003 to the merged development tip (the same one-window deploy), then this 24-wake re-gate.

## Recorded coverage (EM 2026-10-05)

- Lost ack → `d:1` re-send with the ORIGINAL `h` → backend upgrade: PROVEN on hardware (stress slot 00:20Z 10/5,
  1000076 trg + 1000077 get recovered at the next wake).
- Lost SET ack → `in_effect` via the heartbeat hash: covered by nvd #84 DB tests + the live d:1 path; the heartbeat path
  was NOT exercised on hardware. Limit: the hash cannot prove a toggle to an equal value.

## Known limit for R1 (Nick, EM chat 2026-10-03 ~21:50 PDT)

SPOT-33507C commands take effect **2 wakes** after they are sent (its hourly report + hub.sync is at :10, after the
:08 halt, so the unit only sees a command at the next wake). Documented, not gated. The timing fix is chosen AFTER the
C1 :15 results on bmcam004 (zero-energy option first). "Listen until ~:13" below is NOT applied for R1.

## Option (NOT for R1; kept for the C1 decision): listen until ~:13 so SPOT-33507C's commands land the same hour

Why: SPOT-33507C's hourly report + hub.sync is at :10:00 (boot-anchored since its 10/3 00:04Z reset). Every
command reaches the Spotter 56–209 s after it (G4 ledger: 58–118 s typical, 2nd/3rd command of a batch ~45 s apart)
= :11:00–:13:30, after the Pi's :08:20 halt, so the unit only sees it at the next wake: lag 2 wakes instead of 1.

Change (three values, one variable = "listen longer"):
| item | today | proposed | lane / approval |
|---|---|---|---|
| `video.send.budget_min` and `still.budget_min` (deployed `max_run_time_min`) | 8 (halt at uptime ≈ 480 s ≈ :08:20) | 13 (halt ≈ :13:20) | backend remote-config set (product path) |
| `commands.listen_tail_s` | 150 (trimmed to 85–127 s by the budget) | 420 | same |
| SPOT-33507C bridge `sampleDurationMs` | 600000 (bus off :10:00) | 840000 (bus off :14:00) | bridge cfg: **Nick's OK in the TE chat** (not covered by the C1 OK) |
Catch rate: rx ≤ :13:20 covers ~90 % of the G4 arrivals (all first commands of a batch; the 3rd of a 3-command
batch can land at :13:30 → next hour, as today).

Energy cost (measured 2026-10-04 04:00Z on SPOT-33507C's bridge power lines: Pi listening ≈ 0.8 W on the bus,
halted Pi + mote ≈ 0.4 W while the bus is on):
- longer listen: +300 s × (0.8 − 0.4) W ≈ 120 J/wake; longer bus window: +240 s × 0.4 W ≈ 96 J/wake;
- total ≈ +216 J/wake ≈ 0.06 Wh/h ≈ **+1.4 Wh/day**; today's wake ≈ 480 s × ~0.9 W + 100 s × 0.4 W ≈ 470 J → **≈ +45 %
  camera energy per wake**. (Assumption: the 0.8 W listen level holds for the extra minutes; the burst peaks 1.7–2.6 W
  are unchanged.)
Zero-energy alternative: move SPOT-33507C's window after its report (uptime timebase, :15 like C1) → commands land
in the off period and the mote hands them over at boot, lag 1. Fragile on this Spotter (its rebootctl resets re-phase
both the window and the report). The C1 A/B on SPOT-31593C will show what :15 does.
Measure (after #121 passes, with approval): 12 wakes at 13 / 420 / 840000: lag per command, % same-hour, wake→halt,
bus energy per wake from the bridge power lines; restore = the three values back.
