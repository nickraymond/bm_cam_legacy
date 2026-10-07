# PRUNE — bm PR #143 (sent-record aging by Spotter key time, not the Pi clock) on real hardware (bmcam004)

Card from the EM (Nick approved in the EM chat) 2026-10-07 ~23:45Z. Owner: Test Engineer (TE2). Evidence:
`runs/prune_gate_20261007/`. Times UTC (PDT = UTC − 7). If PASS the EM merges #143 to development.

**Question:** does #143 behave on real hardware, with no regression?

## Fixed

- Unit bmcam004 / SPOT-31593C: per_boot + halt, cron ARMED, bridge utc (ticks 0, restored 2026-10-07 23:12Z), hourly
  window :00–:10. Local YAML unchanged: `still: raw: layout: rgb` (B3a) + `uplink: lane:` ON, as before the card.
- R3-PACE on bmcam003 is not touched.
- No commands from us. No change to the Pi clock: the induced clock jump is out of scope on hardware (unit tests cover it).

## Deploy ref (correction to the card; see the EM thread)

bmcam004 runs **104ee3c** (feature/sprint28-b3a, not in development). #143's head c8bea4de is development-based, so a
whole-ref deploy would remove the B3a code that bmcam004's `layout: rgb` YAML uses. That would be a regression caused
by the test itself. The deploy ref is therefore **`hil/prune143-on-104ee3c` @ 0a6e411** = 104ee3c + #143's
`BM_Devel_Pi/rc_media_key.py`, `BM_Devel_Pi/rc_still_storage.py` and their tests. Its runtime diff vs 104ee3c is exactly #143's
(rc_media_key +83/−20, rc_still_storage +19/−11). Desk: 1843 passed / 3 skipped / 0 failed (104ee3c alone: 1833). The
branch is bench-only and never merged. **EM OK 2026-10-08 ~00:00Z:** the merge candidate is #143's development-based head
c8bea4de; the bench proof is on 104ee3c + #143 (same runtime diff). One variable (#143) vs bmcam004's current runtime.

Deploy = `hil/tools/hil_deploy_window.sh bmcam004 hil/prune143-on-104ee3c 0a6e411 <profile> <window> prune143`:
one window, snapshot before/after, **print-config diff must be empty** (no `ACCEPT_DIFF`). Rollback = the same tool
with ref 104ee3c (or the runtime tar rc_field_update writes in /home/pi/backups).

## Measures per wake (cycle log pulled from the unit + SPOT-31593C console)

1. START sent and the keyed send OK: `[KEY] media key K from Spotter UTC …` then `[KEY] sent record: …`, START on the console.
2. Prune order: any `[KEY] pruned N … (by key time)` / `[KEY][WARN] … behind a gap` line comes AFTER `[KEY] media key`
   in the same wake. No prune line on a wake without a key.
3. sent/ before vs after, read-only on the unit (records = `*.sent.json`, ages = key time via `rc_media_key.decode_key`):
   every deleted record had key age > 14 d at that wake's key; **0 records with key age ≤ 14 d deleted**. Expected
   deletions are computed from the before-listing.
4. wake→halt (Pi on → halted current on the console, `hil_wake_report.sh`).

## Rules (pre-registered before the deploy)

- n = 3 consecutive normal wakes after the deploy wake (the deploy wake is not counted).
- **PASS** = every wake meets 1–3, AND its wake→halt is within baseline ±10 %. Baseline = median wake→halt of the last 3
  normal pre-deploy wakes on 104ee3c with this config.
- **STOP → roll back to 104ee3c**: any missing START, any deletion of a record ≤ 14 d by key time (or of an undatable
  record), or wake→halt > baseline + 10 %.
- A wake whose Spotter time read fails (no key) is not a failure on its own. It must show no prune line and sent/
  unchanged, and it does not count toward n.
- Note: if no record is older than 14 d, check 2 cannot see a prune line. The proof is then the order of `[KEY] media key` →
  `[KEY] sent record`, plus sent/ unchanged except the new record. The expected-deletion count is written before each wake.
