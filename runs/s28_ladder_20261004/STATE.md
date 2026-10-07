# Test Engineer STATE — READ FIRST after a compaction (written 2026-10-05 ~05:10Z / Sun 22:10 PDT)

Worktree `/Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c`, branch
`feature/r1-hil-test-engineer`. Times UTC unless PDT (= UTC − 7). Older context: `runs/g4_outdoor12h_20261002/STATE.md`
(§12–14). Report ONE line per step to "Engineering Manager coordination"; ≤ 5 bullets to Nick. **Never block**: no
AskUserQuestion, no Browser pane on LAN hosts (both stalled the session ~10 h); ask Nick in plain text and carry on.
Session-only crons do not survive a restart: after compaction run CronList and re-create what is missing (below).

## 1. Bench state + restore per unit

| unit | state now | restore (in order) |
|---|---|---|
| **bmcam004 / SPOT-31593C** (bridge 0e582dd12c1e1480) | JPEG-XL bench. Bus HELD ON (`bridgePowerControllerEnabled 0`, since 04:48Z 10/5, `hil_bus_always_on.sh`). Pi UP, cron DISARMED (`# DISARMED_BUS_ON`), runtime stopped, still on development 34a6222. `libjxl-tools` 0.11.2 (+ libgif7) installed. R0 PASS. | 0 R1 state (05:25Z): e44dde5 DEPLOYED (runtime tar /home/pi/backups/BM_Devel_Pi_before_rc_deploy_bmcam004_20261005T051901Z.tgz), base YAML mode.run=stay_on (backup ~/hil_backup/s28_20261005T0524*Z/camera_config.yaml), cron ARMED, overlay will hold still.format=nrjxl + crop (cid 1000061) · 1 `ssh pi@bmcam004 'sudo apt-get remove -y libjxl-tools && sudo apt-get autoremove -y'` · 1b reset still.format/still.crop via the backend; copy the YAML backup back (per_boot) · 2 redeploy development (`hil_deploy_unit.sh bmcam004 bmcam004/live_20260925`) · 3 `ssh pi@bmcam004 'crontab ~/hil_backup/20261005T044807Z/crontab_ARMED.txt'` · 4 halt (`tuned_halt.sh`) · 5 `hil/tools/hil_restore_schedule.sh SPOT-31593C` (1/3600000/600000) · 6 nereus000 config.json: `bus_on_watch` false, remove `bus_on_spots`; SPOT-31593C `interval_min` 60, `window_minute` 0 |
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

## 6. Update 2026-10-05 09:55Z
- R1/R2: first nrjxl COMPLETE + renderable (media 57389, 09:40:30Z, D1 3 h 32 m: #126 collision + REASK 90 min + sync alignment). Parity R2.1 requested from the nvd session.
- R3 over the Sofar lane (EM-approved): R3.1 sent 09:49Z (encode_max_s 5 + trg) → check 10:13Z cron (2f344883), which sends R3.2. Then R3.3 (bad crop → e:xk), then reset still.raw.* to defaults.
- After R3: restore bmcam004 (§1 list) unless R4 starts right after (R4 = 12 production wakes with nrjxl: then restore bus to hourly + per_boot YAML + armed crontab but KEEP still.format=nrjxl and libjxl).

## 7. Update 2026-10-05 14:22Z (after R3)
- R3: R3.1/R3.2/R3.5 PASS on the wire; R3.3 FAIL (nrjxl rules skipped on a video unit: `_nrjxl_still` needs mode.media=still); resets applied 14:05 (still.crop + still.raw.* -> YAML).
- bmcam004 now: development? NO — still e44dde5 runtime; per_boot YAML (5ac06d1f), cron ARMED, hourly bus restored 14:20:42, power watch OFF.
  still.format=nrjxl overlay + libjxl-tools KEPT pending the EM's R4 decision.
- Remaining restore if no R4: backend reset still.format; `sudo apt-get remove -y libjxl-tools && sudo apt-get autoremove -y`;
  redeploy development (`hil_deploy_unit.sh` needs stay_on/held bus → use the bmcam-field-update one-window pattern like the #121 deploy).
- Fallback pjpgs 57443 (0e9qqx), 57457 (0e9tkv), R3.5 (0e9wca) should heal before START at the hourly boots.
- Mistake logged: YAML backup path guessed (05:24 vs real 05:23:49) → halt before restore → recovered via hil_bus_always_on.sh.
- 14:22Z: EM said R4 YES on bmcam004 → keep nrjxl/libjxl/defaults; mode.media=still sent (cid 1000072, lands 15:05, applies next boot);
  **R4 = 12 production wakes 16:00Z → 03:00Z** (LADDER R4.1–R4.4: each wake delivers an image complete ≤ 3 h, 0 redundant heals,
  halt uptime ≤ 570 s, fallback ≤ 1/12, START uptime recorded). DO NOT change still.crop during R4 (R3.3 defect).
  Read-only checks: sub-frame count vs duplicates (15:00Z wake), CMA alloc_pages_fail before/after the 16:00Z nrjxl capture.
- 16:50Z EM: R3.3 fix on #120 (camera e7e4b10 + R0.1 fallback 7fa3b2d); backend plan refusal in nvd #91 (not merged).
  AFTER the R4 verdict (06:17Z cron): deploy the #120 tip on bmcam004 (per_boot + hourly bus → use the one-window
  deploy pattern of `deploy_121_bmcam003.sh`: catch at boot, disarm, rc_field_update --ref feature/sprint28-camera
  --leave-disarmed (staged in /tmp), verify sha, re-arm, halt) and re-run R3.3 (crop 1505 via the Sofar lane, no trg):
  expect ERR e:xk at the unit; once #91 is merged, a backend plan refusal before send. Ask the EM before the final restore.

## 8. Update 2026-10-05 (scope change, RELEASE_PLAN §2c)
- R1 ships Fri 10/9 = commands + nrjxl (#120) + low-gain exposure. Gate doc written: `hil/gates/R1RC_release_candidate.md`
  (commit 787a592), sent to the EM; Nick approves before Wed 10/7. RC run Wed–Thu on BOTH units, ≥ 24 wakes, per_boot.
- Open in the doc: low-gain key names + `ag` floor come from the PR's bench (nereus002 + bmcam004, Tue) = entry E3.
- Crons alive after compaction: 0d62cb1d (R4 mid-check 15:17 PDT), b3705182 (R4 verdict 23:17 PDT).
- 18:45Z R4 mid-check: wakes 2+3 fell back pjpg rfb=floor → R4.3 FAIL locked; EM took it to the camera session + Nick. R4 runs to 03:00Z.
- G2 refresh: BMCAM_003 9 gets sent 18:07Z (check cron 8d9bce79, 20:47Z); BMCAM_004 refresh goes with the Tue 17:00Z deploy window.
- Tuning JSON handed (vc4/imx708_wide.json in use, pulled/bmcam003_tuning/).
- PROPOSED to the EM (await OK + low-gain slot): Tue ~14:30Z set still.raw.keep_crop=true on BMCAM_004 (backend Sofar lane) →
  16:00Z daylight capture keeps the PGM → 17:00Z window: #120 tip deploy + pull PGM + backend reset keep_crop + BMCAM_004 refresh.
- R5 APPROVED (EM): bmcam003, #133 b19a4aa (cap default 30000 first, 16667 night comparison). Crons (PDT): 9245b2a3 Tue 07:43 deploy prep (15:00Z window), 078816a7 Tue 19:15 cap 16667,
  (new id) Tue 23:25 refusal check + cap back to 30000, 5ef77726 Wed 00:47 R5 verdict pull (gates #133 merge before the RC deploy).
  Other Tue crons: 4e46c2a0 07:27 keep_crop on 004, d5c1be49 09:47 bmcam004 17:00Z window. R4 verdict b3705182 Mon 23:17.
- 22:17Z: **External event X1**: SPOT-31593C upload stall from ~20:16Z (Notecard not draining; 33507C normal). Spotter untouched; Nick decides reset.
  Check cron 7c62bcc2 (18:17 PDT). keep_crop step re-made (new id, Tue 07:27 PDT) with the uplink-down fallback (local YAML + one detached capture in the 17:00Z window).
- B3a (Nick's new JPEG-XL route): R6 DRAFT `hil/gates/S28_R6_b3a_pi_check.md`, option (a) inside RC / (b) Wed daytime; EM confirms tonight. RC deploy plan ON HOLD.
- X1 reset (Nick GO via EM 22:45Z; rule narrowed 23:2xZ: reset only if 00Z rows absent or backlog rising): crons (new id) (17:55 PDT decision check) + b6f5011d (18:11 PDT: `debug reset` on SPOT-31593C if STUCK,
  halt stub boot, hil_bridge_phase.sh SPOT-31593C utc, re-measure report minute, confirm 02:00Z rows). Old check 7c62bcc2 deleted.
- R6 (B3a) option (b) Wed; Tue B0 encoder de-risk YES (EM) in the 17:00Z window if it fits, else next window: hil/tools/hil_b0_cjxl_guard.sh.

## 9. Update 2026-10-06 ~00:45Z (PLAN CHANGE: R4 stopped, B3a, bus-on tests)
- R4 STOPPED by Nick at 8/12 (verdict in RESULTS). #120 merged to development bbda9bf. R5 on bmcam003 DROPPED; keep_crop capture DROPPED.
- Tonight bmcam004: 02:00Z window deploy **1d0ff4f** (dev + #133 + #134 + sunrise tools; cron ec12a431, 18:46 PDT; hil_deploy_window.sh). Production stays layout bayer4 / profile auto;
  backend can't set v9/v10 keys. 03:00Z window (cron 0bcc12c2): sunrise-loop SMOKE (2 pairs) first, then B0 (LOWGAIN=0); B0 remainder 04:00Z (cron ce833a49).
- X1 crons still live: 59bc278d (17:55 PDT decision), b6f5011d (18:11 PDT reset only if RESET).
- Tue (Nick GO via EM 00:15Z, bus-on + restore, bench only): bmcam004 sunrise loop 13:15–15:00Z (camera session's script; bus-on ~13:05Z; restore ~15:05–15:20Z);
  bmcam003 T1 burst test 16:00–19:00Z (hil/gates/T1_burst_queue_bmcam003.md). Never both bus-on at once (nereus000 adapter). VBAT decline = stop.
- RC deploy Wed ~22:00Z on both units: ON HOLD until the EM confirms the development tip + config.
- Tue crons: 37e36833 (05:45 PDT) sunrise run bmcam004 13:00–15:20Z (needs the camera session's loop script; restore by 15:45Z);
  ee0006ee (08:40 PDT) T1 on bmcam003 15:50–19:xxZ (hil_t1_burst.py; synthetic TST payloads approved; plan.csv committed before first burst).
- 10/6 03:00Z: smoke PASS (2/2), B0 e5 6/10 on 1d0ff4f (3 attempts/still, search 28 s of 30, VmPeak 126 MiB). Crons now:
  15e82baf (20:46 PDT) B0 remainder 04:00Z + keep DNG in /home/pi/b0_dng; 423d19b5 (Tue 05:45 PDT) sunrise run w/ 9efc3b4 tools;
  ee0006ee (Tue 08:40 PDT) T1 on 003; 7f84d0d7 (Tue 09:46 PDT) B0 re-run on 4584436 (staged, same DNG) in the 17:00Z bmcam004 window.
- 04:03Z: B0 baseline 12/12 done (RESULTS "B3a B0"). Tue crons (PDT): e3e7bc73 05:45 sunrise (KEEP_DNG, pull 2 daylight DNGs to day_dng/);
  ee0006ee 08:40 T1 on 003; 17adaade 09:46 B0 re-run 4584436 (day + night); 8bb292ea 10:46 B1 pull-forward (deploy 4584436 at 18:00Z,
  layout=rgb LOCAL, B1 still at 19:00Z; needs the camera session's YAML form + Render on nvd #102 23a4324).

## 10. STATE 2026-10-07 ~02:40Z (READ FIRST after a compaction; supersedes §§1–9 for bench state)

EM handover: a NEW EM session takes over soon (docs/em/, af059d7); report to the old "Engineering Manager coordination"
until the new one messages; expect 3-line status (task, blockers, context fill) + tests defined up front.

| unit | state | restore |
|---|---|---|
| bmcam004 / SPOT-31593C | runtime **104ee3c** (= 4584436 B3a fix; #120+#133+#134); local camera_config.yaml: `still: raw: layout: rgb` (backup camera_config.yaml.bak_b1_20261006T180142Z) + `uplink: lane:` ON 3600/375/20/180 (backup …bak_lane_20261007T0000*Z); cron ARMED; bridge **ticks mode, windows ~:02:25–:02:33** (commit 02:02:22Z, early: leading-zero bug); hard power cut 02:04:25Z mid-cycle → health check at the 03:02Z wake | layout: `hil/tools/hil_b3a_layout.sh bmcam004 bayer4`; lane: copy the lane backup back or `hil_lane_block.sh bmcam004 off`; phase: `hil_bridge_phase.sh SPOT-31593C utc` (only with EM OK: no more resets tonight) |
| bmcam003 / SPOT-33507C | development a50636e; production hourly bus (restored 00:19Z 10/7); cron armed; hil-r1-cmdres.timer RUNNING | — |
| nereus000 | bus_on_watch off; spotter-monitor, rig health, dashboard | — |

Results today: R4 STOPPED (verdict); sunrise low-gain PASS (runs/s28_lowgain_sunrise_20261006); B0 baseline + fix
(RESULTS "B3a B0"); B1 PASS format/decode/render, 57901 complete via heal + QC PASS, 57914 healing (send to QC
[a53739] when complete: cron 6aa75fe3); T1 (runs/t1_burst_20261006) a/b SUPPORTED, c NOT; T2 (runs/t2_msgsize_20261006)
best 288 B @ 0.6 s (464 B/s, +55 %), 0.6 s region needs repeats; X1 forensic (runs/s28_ladder_20261004 RESULTS).
Gotchas: hil/procedures/BENCH_GOTCHAS.md.

Crons (session-only): a11e210d 20:05 PDT bmcam004 health check after the hard cut (STOP + tell EM if off);
1a076de6 :24 PDT 20/21/22 lane wake reports (power-on, [PHASE], queue_full, burst-end margin, heal-vs-health-check,
first-send completion; (B) re-phase decision data after 2–3 wakes — do NOT re-phase); 6aa75fe3 57914 → QC.
RC deploy (Wed ~22:00Z per R1RC) ON HOLD until the EM confirms the development tip + config (B3a layout, lane, low-gain).
- New EM = "Bristlemouth camera program EM handover" (local_7c78dab7…; also "EM: Bristlemouth program (new)"). Report there now.
  Cards running: HEALTH-0302, LANE-W1..3 (flag early if it answers itself or clearly fails), B1-57914. RC deploy ON HOLD:
  the EM sends the development tip sha + RC config (layout, lane, exposure profile) by Wed 11 AM PDT after Nick confirms.
  OPEN MISMATCH: R1RC gate doc says low-gain ON both units; the EM handover says "exposure auto" — the EM settles it with
  Nick; update hil/gates/R1RC_release_candidate.md (unit config table + RC.6) to match before deploying. Before the RC
  deploy, rewrite this STATE so a fresh TE can take over mid-gate.
- 10/7 ~03:5xZ HOLD (Nick via EM): no new testing until two desk analyses land (NOAA reef buoy field data; SD-log re-mine). No bridge changes, no Spotter resets, no re-phase, no new cards. Passive only: LANE-W2/W3 logging (cron 98e1ef7f), 57914 -> QC (6aa75fe3). RC deploy on hold. Any rig action tonight -> tell the EM first.
