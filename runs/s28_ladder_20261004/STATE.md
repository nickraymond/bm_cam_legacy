# Test Engineer STATE — READ FIRST after a compaction (written 2026-10-05 ~05:10Z / Sun 22:10 PDT)

Worktree `/Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c`, branch
`feature/r1-hil-test-engineer`. Times UTC unless PDT (= UTC − 7). Older context: `runs/g4_outdoor12h_20261002/STATE.md`
(§12–14). Report ONE line per step to "Engineering Manager coordination"; ≤ 5 bullets to Nick. **Never block**: no
AskUserQuestion, no Browser pane on LAN hosts (both stalled the session ~10 h); ask Nick in plain text and carry on.
Session-only crons do not survive a restart: after compaction run CronList and re-create what is missing (below).

## 1. Bench state + restore per unit

| unit | state now | restore (in order) |
|---|---|---|
| **bmcam004 / SPOT-31593C** (bridge 0e582dd12c1e1480) | JPEG-XL bench. Bus HELD ON (`bridgePowerControllerEnabled 0`, since 04:48Z 10/5, `hil_bus_always_on.sh`). Pi UP, cron DISARMED (`# DISARMED_BUS_ON`), runtime stopped, still on development 34a6222. `libjxl-tools` 0.11.2 (+ libgif7) installed. R0 PASS. | 1 `ssh pi@bmcam004 'sudo apt-get remove -y libjxl-tools && sudo apt-get autoremove -y'` · 2 if #120 was deployed: redeploy development (`hil_deploy_unit.sh bmcam004 <profile>`) · 3 `ssh pi@bmcam004 'crontab ~/hil_backup/20261005T044807Z/crontab_ARMED.txt'` · 4 halt (`tuned_halt.sh`) · 5 `hil/tools/hil_restore_schedule.sh SPOT-31593C` (1/3600000/600000) · 6 nereus000 config.json: `bus_on_watch` false, remove `bus_on_spots`; SPOT-31593C `interval_min` 60, `window_minute` 0 |
| **bmcam003 / SPOT-33507C** (bridge c3c564b91856226c) | Repair rig. development a50636e (= bm #121, merged 5ea193a) since 05:01Z 10/4; production hourly bus; per_boot + halt; cap 126; cron armed. R1G running (wakes counted from 06:00Z 10/4). | driver: `sudo systemctl disable --now hil-r1-cmdres.timer && sudo rm /etc/systemd/system/hil-r1-cmdres.* && sudo systemctl daemon-reload` (nereus000). Unit itself needs no restore. |

nereus000 (192.168.1.45): spotter-monitor; `hil-rig-health.timer` (5 min) + `hil-rig-dashboard.service` (:8095, page
http://192.168.1.45:8095/); `hil-r1-cmdres.timer` (BMCAM_003 hourly set + trg every even hour, until 2026-10-10);
ack-loss unit `hil-r1-ackloss` finished. Files: /home/pi/hil_health/{alerts.json,health.csv,metrics.jsonl,events.jsonl,
ALERTS.log,summary.log,annotations.jsonl,config.json}, /home/pi/hil_r1/{cmdres.jsonl,ackloss.log,*.py}.

## 2. Next steps

1. **JPEG-XL R1 on bmcam004 after the EM says "#83 LIVE"** (merge ~05:35Z): run folder `runs/s28_ladder_20261004`
   (`hil/.current_run`). Deploy e44dde5: `hil/tools/hil_deploy_unit.sh bmcam004 <device profile> --ref
   feature/sprint28-camera --accept-print-config-diff` (wrapper hardcodes `--ref development` but a later `--ref`
   wins; registry v8 adds 5 keys → only those in the print-config diff). Unit stays stay_on/disarmed per LADDER R1.
   Then `hil/tools/hil_refresh.sh BMCAM_004 SPOT-31593C`, set `still.format=nrjxl` (+ crop 1504,846,1600,900) via the
   backend (`hil_change.sh` / remote-config), trigger one still (`hil_step.sh … trg kv med:still`), reassemble with the
   e44dde5 `hil_s28_reassemble.py`, R2 render at the backend (Nick's acceptance: a JPEG-XL still rendered on his
   frontend). R0 tools live ONLY in the detached e44dde5 worktree
   `/private/tmp/claude-501/…/scratchpad/s28_e44dde5` (with this branch's hil/hil.env copied in).
2. **R1G verdict** (bmcam003) ~Mon 04:00–08:00 PDT (11:17Z cron): `hil/gates/R1G_commands_first.md` R1G.1–5 via
   `hil_cmd_ledger.py` (on nereus000, `--since 2026-10-04T06:00`), `hil_media_table.py`, wake reports; write the R1G
   section in `runs/r1fix_cmdres_20261003/RESULTS.md`. Known: item 2 (d:1 recovery) PROVEN 03:00Z 10/5; item 3 covered
   by nvd #84 tests; 1000043 = deploy event; stress slots excluded from R1G.1; trg 1000069 backend status `answered`
   though its clip exists (recheck linkage); SPOT-33507C 2-wake command lag = documented R1 known limit.
3. **Temporary power watch** while SPOT-31593C's bus is held on (config `bus_on_watch`); remove at the restore.
   **Rig fact (Nick 2026-10-05): both Spotters charge from nereus000's USB hub** → the bus-on camera load comes
   through nereus000's marginal adapter (IOUT 0.5 → 0.9 A with the bus held on; 1.2 A in windows). Sustained VBAT
   decline outside :00–:12 = STOP long bus-on runs; short R1–R3 OK; flag before any multi-hour bus-on step (R4 runs on
   the production schedule: fine). Recommendation to Nick: bigger nereus000 adapter or a separate Spotter supply.
4. C1 phase 2 (message size) = parked: needs Nick's OK on the wire change + signed `uplink.chunk_chars`.

## 3. Session crons (session-only; re-create after a restart)

| id | when (PDT) | task |
|---|---|---|
| 2cfaa372 | Sun 22:17 | R1G 24th wake (05:00Z): wake report SPOT-33507C, ledger, log; check "#83 LIVE" → R1 |
| 1cd97c04 | Mon 04:17 | R1G verdict |

## 4. Nick's approvals (typed in THIS chat) and scope

| approval | scope |
|---|---|
| bridge cfg for production schedule (10/2) | done |
| C1 bridge changes on SPOT-31593C ("full scope", 10/3 21:2x PDT) + 90-min interval ("Yes, 90-min :00/:30", 10/4 07:5x PDT) | C1 only, SPOT-31593C only. C1 DONE (no significant effect, `runs/c1_comms_20261003/RESULTS.md`) |
| bus-always-on on SPOT-31593C ("Yes approved.", 10/4 15:46Z) | JPEG-XL R0–R3 on bmcam004, plus its restore |
| via EM (Nick's rulings, not prompts): libjxl install/remove on bench 003/004; standing `post` from nereus000 to 003/004 each wake (R1.1, not built); merges by the EM | bench units only, never field units |
NOT approved: camera ssh key from nereus000, hourly `sensors`, ntfy push, LED; "listen until :13" for R1; any SPOT-33507C
bridge change; anything to field Spotter SPOT-33361C (never).

## 5. Results so far

G4 FAIL (`runs/g4_outdoor12h_20261002/RESULTS.md`); bm #121 gate items 1+4 PASS, 2 PASS (stress), 3 covered;
C1 phase 1 no significant effect; S28 R0 bmcam004 PASS (`runs/s28_ladder_20261004/RESULTS.md`); rig dashboard v3 live.
