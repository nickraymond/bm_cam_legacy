# R1RC — R1 release-candidate gate (both bench units, outdoor, production per_boot)

Scope (Nick, RELEASE_PLAN §2c, 2026-10-05): R1 ships Fri 10/9 with **commands** (bm #121 + nvd #84), **JPEG-XL stills**
(bm #120 + nvd #83) and the new **low-gain exposure mode** (PR in progress). This gate runs the development tip that
contains all three on BOTH bench units, unattended, on the production schedule. Owner: Test Engineer (sole bench owner).
Approval: Nick, before Wed 10/7. Evidence: `runs/r1rc_<YYYYMMDD>/`. Times UTC (PDT = UTC − 7).

## Entry conditions (all before the first counted wake)

| # | condition | check |
|---|---|---|
| E1 | bm #120 (incl. e7e4b10 R3.3 fix + 7fa3b2d R0.1) and the low-gain PR merged to development; nvd #83 live; nvd #91 merged (backend nrjxl-crop refusal) or explicitly deferred by the EM | `git log origin/development`, staging deploy note |
| E2 | Sprint28 R4 on bmcam004: PASS (or the EM's written waiver) | `runs/s28_ladder_20261004/RESULTS.md` |
| E3 | low-gain bench test on nereus002 + bmcam004: PASS, with the floor value for the gain (`ag`) and the key/preset names it uses | that run's RESULTS |
| E4 | both units deployed to the SAME development sha (below), snapshots before/after, print-config diff = only the keys the merged PRs add | `snapshots/`, `pulled/*_field_update_*.log` |
| E5 | `libjxl-tools` (cjxl) + numpy present on both units (bmcam003 needs the install; covered by Nick's JPEG-XL bench approval) | R0.4-style readout in gate.log |

## Unit configuration during the gate

| unit | media | still format | exposure | driver |
|---|---|---|---|---|
| **bmcam003 / SPOT-33507C** | alternates still ↔ video hourly (`hil-r1-cmdres.timer`: `set mode.media` every hour + `trg` every 2 h) | nrjxl (default crop 1504,846,1600,900) | low-gain profile ON | backend, Sofar lane (product path) |
| **bmcam004 / SPOT-31593C** | still every wake | nrjxl (default crop) | low-gain profile ON | backend: one `trg` every 4 h (D3 coverage), nothing else |
Both: per_boot, real halt, cron armed, production bus 1 / 3600000 / 600000, video cap 126, heal auto-send on (24/day).
`still.crop` is NOT changed during the gate except in RC.8.

## Criteria (≥ 24 consecutive wakes per unit, + 6 h completion tail)

| id | criterion | PASS when | evidence |
|---|---|---|---|
| RC.1 | every command confirmed at the backend (= R1G.1) | 100 % confirmed (ack, d:1 with the original h, or heartbeat hash); 0 `late` / `superseded` for a command that ran | `hil_cmd_ledger.py` per unit |
| RC.2 | every trg accounted for (= R1G.2) | each trg → its clip delivered (nrjxl, pjpg or video), or `tr=…:budget/fail` reported; 0 silent | ledger + media table + cycle logs |
| RC.3 | no SSH needed (= R1G.3) | 0 writes over ssh between the post-deploy halt and the verdict | gate.log |
| RC.4 | every clip completes, 0 redundant heals (= R1G.4) | 100 % of media complete by window end + 6 h; 0 heal asking for a complete media | `hil_media_table.py`, heal-events vs completion |
| RC.5 | nrjxl delivers and renders, never silent | every still wake: START `fmt=nrjxl` → row complete, `render_state renderable`, display JPEG present; OR START `fmt=pjpg rfb=<reason>` → that pjpg complete. 0 still wakes with neither. Fallbacks ≤ 2/24 per unit, each with its `rfb` reason recorded | console START, `/media/{id}` |
| RC.6 | low-gain profile applied | every still wake's END metadata shows `ag` ≤ the floor from E3 (and `et_us` within the profile's range); video wakes report per the PR | decoded END lines (`ag:`, `dg:`, `et_us:`) per wake |
| RC.7 | no regression | 24/24 wakes per unit; wake→halt ≤ 540 s video, ≤ 570 s nrjxl still; START every wake; heals sent before START and served; peak RSS of the nrjxl search recorded (R0.3 limit 120 MB: currently 136–138 MB, a known finding) | wake reports, cycle logs |
| RC.8 | R3.3 re-run with the fix | on bmcam004: `set still.crop [1505,846,1600,900]` via the backend → backend plan refuses (if nvd #91 is live) or the unit answers `e:xk`; config hash unchanged; then no crop change remains | console, ledger, `<CF>` |
| RC.9 | rig sanity | 0 Spotter resets / charger CRIT counted as unit failures (logged as events, wake restarted); nereus000 on external power throughout | `hil_rig_health` alerts.json / ALERTS.log |

Measured, not gated: D1 minutes (C1 owns the 3 h target), command lag (SPOT-33507C 2 wakes, known limit), the
nrjxl distance / size per wake, CmaFree before/after (counters unavailable on this kernel → dmesg rule).

Known limits carried in (documented, not RC failures): SPOT-33507C 2-wake command lag; lost SET ack → heartbeat hash
path not exercised on hardware (nvd #84 tests); stay_on findings (#126 heal/sync collision, no d:1 re-send, duplicate
answers) do not apply to per_boot production; nrjxl search peak RSS > 120 MB (open with the camera session).

## Deploy (per unit, one bus window each; Wed early)

1. `hil_new_run.sh r1rc R1RC bmcam003 bmcam004`; `hil_unit_snapshot.sh <host> before_rc` inside a window.
2. bmcam003: install `libjxl-tools` first (restore line in gate.log).
3. One-window deploy (pattern `runs/r1fix_cmdres_20261003` / `deploy_121_bmcam003.sh`): catch at boot → back up the
   ARMED crontab → disarm → SIGTERM the cycle → `rc_field_update.sh --ref development --leave-disarmed
   --accept-print-config-diff` (staged in /tmp) → verify `software_sha.txt` = the RC sha → re-arm from the ARMED backup
   → `hil_unit_snapshot.sh <host> after_rc` → halt before :10.
4. Config through the backend (product path, Sofar lane): `still.format=nrjxl` (both), the low-gain profile key(s)
   (per the PR), bmcam004 `mode.media=still`. Wait for each ack (or supersede only if a lost ack blocks it).
5. Start the drivers (bmcam003 hil-r1-cmdres as today; bmcam004 a 4-hourly trg). First counted wake = the first wake
   on which the unit reports the RC sha AND the RC config hash.

## Restore

PASS: the units STAY on the RC (it is what ships); only the bench drivers stop (`hil-r1-cmdres.timer` off).
FAIL / rollback: redeploy development 5ea193a (R1G-passed) with the same one-window pattern; reset `still.format`,
the low-gain keys and `mode.media` via the backend; `apt-get remove -y libjxl-tools && apt-get autoremove -y`;
snapshot equal to the pre-RC snapshot.
