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
