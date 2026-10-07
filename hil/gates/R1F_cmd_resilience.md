# R1F-CMD — command resilience baseline + fix verification (repair rig bmcam003)

When: from Sat 10/3 ~12:20 PDT (19:20Z), open-ended until the EM's fixes are verified. Owner: Test Engineer.
Unit: **bmcam003 / SPOT-33507C only** (the repair rig, Nick 2026-10-03 via the EM). R1 code (development 34a6222),
production hourly bus (1 / 3600000 / 600000), per_boot + real halt, `video.send.message_cap` 126.
Evidence: `runs/r1fix_cmdres_<YYYYMMDD>/`.

## Question

Is every backend command delivered, executed AND confirmed at the backend? (D1 completeness is second.)

## Drive (unattended, product path)

`hil-r1-cmdres.timer` on nereus000 runs `hil_g4_alternator.py --devices BMCAM_003` hourly at :20 UTC:
- every hour: `set mode.media` still ↔ video (Sofar lane, supersede), so each set has a visible effect (START type);
- every 3 h (20, 23, 02, 05, 08, 11, 14, 17 UTC): a `trg` (v2), ≥ 70 s after the set.
Heal auto-send stays on (backend). Console: observe only.

## Measure (per command: `hil/tools/hil_cmd_ledger.py`, read-only, on nereus000)

| field | source |
|---|---|
| backend status / backend ack | `GET /admin/devices/BMCAM_003/commands` |
| Spotter received (time) | console `Remote message received … "id":<id>` |
| report before rx, rx − report (s) | console LEGACY len-50 report (the hub.sync hypothesis) |
| unit OK / ERR (time, text) | console `[bmcam003] OK id=<id>` |
| arrival wake, lag (wakes) | reply time vs send time |
| effect | set: START type of the next boot (`hil_media_table.py`); trg: its media row (key, complete, minutes) |

## Criteria (for the fix verification; the baseline only records)

| id | PASS when |
|---|---|
| C1 | every command: unit OK on the console (delivered + executed) |
| C2 | every command: backend ack, or a backend status that matches the unit's real outcome (no "late"/"superseded" for a command that ran) |
| C3 | every trg → one media row delivered complete (no `clip NOT sent — budget`) |
| C4 | lag per command recorded; arrival minute vs report minute recorded (hypothesis: commands are fetched only at the Spotter's hourly report sync) |

## Restore

`sudo systemctl disable --now hil-r1-cmdres.timer && sudo rm /etc/systemd/system/hil-r1-cmdres.* && sudo systemctl daemon-reload`
on nereus000. The unit keeps whatever media the last set gave it (production config otherwise unchanged).

## Baseline already known (G4, 10/3 04–16Z, `runs/g4_outdoor12h_20261002/analysis/cmd_ledger_*.csv`)

32/32 commands reached the Spotter 56–209 s after its hourly report and got a unit OK. SPOT-33507C reports at :10
(after the :08 halt) → reply one wake later (lag 2); SPOT-31593C at :05 → same wake (lag 1). Backend ack 23/32;
2 trg clips captured but not sent (budget double count).

## Fix verification plan: bm #121 (camera) + nvd #84 (backend) — PREPARED, not started (EM go needed)

Fixes under test: #121 = trg budget double-count fix + ack re-send + `tr=` trigger outcome in the reply;
#84 = backend hash fallback (a set confirmed by a later `<CF>`/WS hash), `tr=` parsing, late `d:1` upgrade.
Order (EM): #84 merged to staging first → this deploy of the #121 branch tip to bmcam003 → EM merges #121 on PASS.

1. Baseline freeze: `hil_cmd_ledger.py --device BMCAM_003 --since 2026-10-03T19:00` → `analysis/ledger_baseline.csv`
   (the R1F-CMD hours on R1 code); `hil_media_table.py` for the same span.
2. Confirm #84 is live: `GET /admin/devices/BMCAM_003/commands` returns the new status fields / a known
   late command re-evaluated (EM confirms the staging deploy).
3. `hil_unit_snapshot.sh bmcam003 before_121` inside a window; then `hil_deploy_unit.sh bmcam003 <#121 sha>` in the
   next window (armed per_boot unit: catch awake, disarm, deploy, re-arm; print-config diff must be empty —
   #121 adds no keys); `after_121` snapshot: sha = #121, config hash unchanged, cron armed.
4. Run ≥ 12 wakes with the same driver (hourly set) and **trg every 2 h** (6 trgs) — `hil-r1-cmdres.timer`
   `--trg-hours` changed to even hours for the gate (one edit, logged).
5. PASS when, over those 12 wakes:
   - C1: 100 % of commands get a unit OK (console);
   - C2: 0 commands that ran show `late`/`superseded`-without-effect at the backend (acks re-sent or hash-confirmed);
     every backend status = the unit's real outcome (ledger vs console);
   - C3: 6/6 trg → one media row, complete; 0 `clip NOT sent — budget`; backend trg status `triggered` with the
     media key from `tr=`;
   - regression: 12/12 wakes, wake→halt ≤ 540 s, 0 Spotter-side changes, heals still sent/served, normal clips'
     first-send loss and D1 not worse than the baseline (same media mix).
6. FAIL / rollback: `hil_deploy_unit.sh bmcam003 34a6222` (development R1) in the next window; snapshot = before_121.
