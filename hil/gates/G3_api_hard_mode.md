# G3 — API hard mode (Release R1)

When: Sat 10/3 → Mon 10/5 (PDT). Owner: Test Engineer. Units: **bmcam003** (SPOT-33507C,
primary) and **bmcam004** (SPOT-31593C, spot checks + the IP4 still-unit leg).
Plan: `sprints/Release_R1/RELEASE_PLAN.md` §2 (G3) and D2. Ladder: `sprints/Sprint27_remote_config/LADDER.md`.
Evidence: `runs/g3_hardmode_<YYYYMMDD>/` (`hil/tools/hil_new_run.sh g3_hardmode G3 bmcam003 bmcam004`).

## Pass (RELEASE_PLAN)

Every refused value is refused before send; every sent value is acked; 0 units need SSH.

## Criteria

| id | criterion | PASS when | evidence |
|---|---|---|---|
| G3.1 | backend and repo agree on the catalog | `GET /remote-config/catalog` sha256 == `docs/bmcam_config_catalog.json` sha256 on the deployed bm sha (after the image-key PR, re-generate the cases) | `analysis/plan_<dev>/plan_check.json` |
| G3.2 | bad values refused before send (backend) | `hil_plan_check.py` on both devices: **0 FAIL**; every `depends` row has a written disposition in RESULTS.md | `analysis/plan_<dev>/plan_check.csv` |
| G3.3 | bad values refused by the unit (defence in depth) | N1–N5, IP5 and the E-series negatives below: each answered `ok:0` with the expected `e`; `get` shows the old value; config hash unchanged (`hil_pistate.sh` before/after) | `steps.log` |
| G3.4 | every sent value acked | 100 % of `set`/`reset` commands sent in L1–L14, IP1–IP4, IP6, E-series: ack `ok:1` received; `<CF … key=value@c<id>>` shows the new value | `steps.log`, `console/` |
| G3.5 | every acked value in effect | each step's check (c) in LADDER (sidecar / metadata / START / clip) shows the effect | `pulled/`, `analysis/` |
| G3.6 | video retry (Nick 2026-10-01) | IP6: clip produced; `[VID][WARN] … retrying … without camera controls`; manifest `requested_controls.controls_dropped: true` | `pulled/` |
| G3.7 | Sofar lane end to end | L14: device view `ui_status` sent → saved → in_effect for one change | `api/l14_*.json` |
| G3.8 | 0 units need SSH | no write over ssh to a unit between baseline and restore (snapshots are read-only); every unit answers a console `ping` at the end | `gate.log`, `steps.log` |
| G3.9 | restore | each unit's final config hash == its baseline hash; crontab == the takeover backup; bus setting == recorded start | `snapshots/*_g3_end_*` |
| G3.10 | visible (D4) | ≥ 10 commands incl. ≥ 2 refusals and the L14 send appear on logs.html with their acks | `analysis/logs_spotcheck.md` (screenshots) |

## Preconditions

- [ ] Takeover PASS (`hil/procedures/TAKEOVER.md`): development tip incl. #97 + #98 on both units,
      host fix in, stay_on, bus held, cron armed.
- [ ] Staging runs the Sprint27 API (`/remote-config/*`); `BM_REMOTE_CONFIG` on and both devices
      writable (per-Spotter settings or the env, whichever is live) — check with the sanity case.
- [ ] For IP1–IP6: the image-processing PR (after P0) merged and deployed — else IP rows are BLOCKED.
- [ ] Console sends: Nick's OK on record for the console `cmd.txt` lane for G3 (bench-gotchas).
- [ ] Baseline per unit: `{"id":N,"c":"get","k":["mode","camera","still","video.record","video.send"],"to":"con"}`
      saved, hash `h` noted, one baseline still + one clip saved.

## Steps

### Part A — backend refusal sweep (no unit, no cellular; any time staging is up)

```bash
python3 hil/tools/hil_hardmode_cases.py --out $HIL_RUN_DIR/analysis/cases
scp hil/tools/hil_plan_check.py $HIL_RUN_DIR/analysis/cases/cases.json $HIL_MONITOR:/home/pi/hil_g3/
ssh $HIL_MONITOR 'cd /home/pi/hil_g3 && for d in BMCAM_003 BMCAM_004; do python3 hil_plan_check.py --cases cases.json --device $d --api https://nereus-vision-staging.onrender.com --out plan_$d; done'
scp -r $HIL_MONITOR:/home/pi/hil_g3/plan_BMCAM_00* $HIL_RUN_DIR/analysis/
```

~800 cases × 0.25 s ≈ 4 min per device. Review every FAIL and every `depends` row; a FAIL is
a finding for the spec owner (Sprint27 session) until shown to be a model error.

### Part B — LADDER on bmcam003 (console lane first, one change at a time, ≥ 65 s apart)

L1–L14 as written in LADDER.md, then N1–N5, then IP1–IP6 (when deployed). Each step:

```bash
hil/tools/hil_pistate.sh L2.before bmcam003
hil/tools/hil_cmd.sh L2 '{"id":<remote id>,"c":"set","kv":{"camera.controls_enabled":true,"camera.exposure.enabled":true,"camera.exposure.ev":-1.0}}' 10 SPOT-33507C
hil/tools/hil_cmd.sh L2.trg '{"id":<id+1>,"c":"trg","v":2}' 10 SPOT-33507C
hil/tools/hil_pistate.sh L2.after bmcam003
```

When nvd is on staging, record each change first with `POST /devices/{id}/remote-config/changes`
`"lane":"console"` (on nereus000), publish its `console_line`, then mark it sent (LADDER "How each
step is sent"). Ids: remote range above the unit's last for recorded changes; console range
(1–99,999) for negatives the backend would refuse.

**Cellular budget.** Every capture check over cellular costs a full burst. Use a one-shot
`save_local` trigger (`trg` with kv `mode.output: save_local`, proven in S5 L7) for visual
checks and pull the file (read-only scp). Cellular captures only for L6, L9, L10, L14 and the
D3 checks: ≈ 6 still bursts + 4 clips. Anything beyond that goes to Nick via the EM first.

### Part C — Test Engineer edge cases (E-series)

| # | case | expected |
|---|---|---|
| E1 | boundary values sent to the unit: `camera.exposure.ev` −8.0 / 8.0, `analogue_gain` 64.0, `shutter_us` 1, `lens_position` 0.0 / 32.0, `video.record.bitrate_mbps` 0.1, `video.send.duration_s` 1.0, `still.message_cap` 500, `still.budget_min` 30 | each acked `ok:1`, `<CF>` shows it, the next capture is produced (any image), reset → baseline |
| E2 | just outside: ev 8.01, gain 64.1, message_cap 501 (console range id) | backend refuses (Part A); unit `e:val`/`e:xk`; nothing stored |
| E3 | too_big: a 7-key set over the console | backend `too_big`; `hil_console.sh` refuses a > 256 B line locally; a 256 B line reaches the unit |
| E4 | next_boot keys: `mode.media` still → video, `mode.interval_s` 0 → 3600 → 0, `mode.heartbeat_s` 300 → 600 → 300 | ack, exit 72, restart in ≤ 10 s, next `<WS>`/START shows it |
| E5 | back-to-back: two sets ≥ 65 s apart before the next action; then a set whose id is LOWER than the last | both acked in id order; the lower id `e:old` |
| E6 | duplicate: re-publish the same id + JSON | original answer + duplicate marker; nothing re-applied (state sha unchanged except result_cache) |
| E7 | set during a clip (L13) and during a still burst | ack after the action; value applies at the NEXT action |
| E8 | reset: one key; 4 keys; a key that was never set | ack ok; `<CF>` back to the YAML value; the never-set key is a no-op ack |
| E9 | recover from a bad-but-accepted video setting using commands only (video retry + reset) | clip still produced (IP6), `reset` restores, no SSH |
| E10 | command while the unit is mid-restart (exit 72 window) | the command is either answered after restart or lost visibly (no ack) and re-sent: never applied twice |

### Part D — UI demo window (G2, Nick)

Offer the UI session ("Build remote-config UI for Release R1 (G2)") a fixed window with the bench
idle, e.g. Sat 10/3 13:00–15:00 PDT. During it: no Test Engineer sends; Nick sends from the UI;
the Test Engineer only snapshots before/after and records Nick's command ids in RESULTS.md.

## Restore

1. `reset` every key touched (≤ 4 per command), `get` → hash == baseline (G3.9).
2. `hil_unit_snapshot.sh <host> g3_end` on both units; crontab == takeover backup.
3. The bench stays in G3 state (stay_on, bus held) until the freeze step switches to production
   config (RELEASE_PLAN: 10 min/hr bus window, heal cap 24/day), which is the G4 setup.

## Not covered by G3

- 24 h behaviour (G4/G5), outdoor RF, solar power.
- Power cut mid-change (an SD hard-cut risk on the bench; not run without Nick's OK).
