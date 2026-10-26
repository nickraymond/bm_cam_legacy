# Runbook: bmcam002 on-land update for NOAA, Tue 10/27 (backup day Thu 10/29)

TE 006, Mon 10/26. Spec: `~/NereusData/ops/pe_specs/SPEC_e4_noaa_release.md` rev 12.4 (sha256 ceb47d797438): must dos 3, 7, 8;
R12-1; F1–F9. Envelope: nereus-software-engineering-team `approvals/2026-10-27_noaa_update.md` (Lead 004). bmcam002 is a
**field unit**. Every on-unit step and every SPOT-33361C step runs only on Nick's own words in the executing TE chat
(TEAM_RULES §6). Rehearsed on bmcam004 first (`runs/rehearsal_noaa_update_20261026/TEST_CARD.md`). Rehearsal timings
replace the time boxes below once they exist.

## What changes, in one line
bmcam002 goes from legacy **0d03a62** + the shipped v1 profile (sha256 117876ae0928…) to bm development **613fb2c1**
(Nick's S3 GO, TE5 chat, Mon 10:00 AM) + the v2 target profile
`device_profiles/bmcam002/noaa_20261027/camera_config.yaml` (sha256 **412bcafe00cd…**, config hash **fe87abab**).

## Who does what
- **NOAA staff (on site):** phone hotspot + power-cycling bmcam002 (section P). Nothing else.
- **Nick (Mac, Tailscale SSH):** types every command in sections C–R, one at a time, and reads the result back.
- **TE (on the line):** says which command is next, checks each result against the "want" line, keeps the log and the
  clock, and calls a stop.
- **TE, backend only (section H, after the pass line):** must dos 7–8 for SPOT-33361C, on Nick's words in the TE chat.
- Log: the TE writes every command, its output and the time (`date`) to
  `~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/runbook.log`.

## Before the day (TE, Mon evening; none of this touches bmcam002)
1. The rehearsal card has passed (QE, Nick's OK, run) and its timings are written into this file's time boxes.
2. Nick has asked NOAA (must do 1): bmcam002 on land and powered; the hotspot phone; whether a Spotter is attached;
   NOAA's start time; a staff member free for ~30 min. **The hotspot SSID and password bmcam002 already knows: unknown
   (TE5 never recorded it; the 8/18 deployment doc doesn't name it). Nick names it.** The Pi joins only a network it
   already knows (skill bmcam-hotspot-update, Phase 1).
3. Staged on the Mac (TE), each with its sha256 written in the log:
   - `~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/stage/`, filled with:
     - `rc_field_update.sh` from 613fb2c1;
     - `camera_config.yaml` from this PR's merge (sha256 412bcafe00cd…);
     - `tools/noaa_catch_disarm.sh` and `tools/noaa_capture_check.py` from this PR.
   - an nvd export at the `staging` sha deployed Tue morning (`git archive <sha> backend/app/services/nrjxl.py`).
     How the TE reads the deployed sha: **unknown**; the RC soak card's precondition 1 used the sha the Lead posted.
     The TE asks the Lead by 8 AM Tue.
4. Envelope approved @ sha by Nick; §10 freeze: no merges to bm `development` or nvd `staging` from Mon 6 PM.

## P. NOAA phone steps (one page; read to NOAA staff on the call)
1. iPhone: Settings → Personal Hotspot → **Maximize Compatibility ON** (the Pi only sees 2.4 GHz).
2. The phone's name (= hotspot SSID) and the hotspot password must match what Nick gives, **letter for letter**. Turn off
   Settings → General → Keyboard → Smart Punctuation before typing an apostrophe.
3. Stay on the Personal Hotspot screen with the screen awake until the camera connects.
4. With the hotspot ON: unplug bmcam002's power, wait 10 s, plug it back in.
5. Success = a blue "1 connection" banner on the phone within ~3–5 min. Tell Nick when you see it.
6. Keep the phone within a few metres of the camera. Don't unplug the camera again unless Nick asks.

## C. Catch it awake (time box 20 min from the power-cycle, F1)
bmcam002 is armed with a real halt. The window is 10:00–15:00 ET, and 9:30 AM PT = 12:30 PM ET is inside it, so every boot
runs a cycle. With no Spotter on land, the cycle waits up to 60 s for Spotter UTC, then skips and halts (fallback OFF).
That leaves ~1–2 min. Start the catch loop BEFORE step P4.
- C1 (Nick, Mac): `/Applications/Tailscale.app/Contents/MacOS/Tailscale ping bmcam002`. Want: pong. Tailscale IP on
  8/18: 100.118.150.44 (aoml-production `docs/DEPLOYMENT_AOML_REEF_2026-08-18.md`).
- C2 (Nick, Mac):
  `until ssh -o ConnectTimeout=3 pi@bmcam002 'bash -s' < ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/stage/noaa_catch_disarm.sh; do sleep 2; done`
  - Want: `[CATCH] DISARMED`, then the `[SURVEY]` lines.
  - If ssh prints a "Tailscale SSH requires an additional check" URL, Nick opens it and approves.
- **Stop (F1):** nothing in 20 min → NOAA power-cycles once more with the hotspot up (P4). Still nothing after another
  15 min → backup day 10/29; NOAA keeps bmcam002 on land.

## 0. Step 0: is it the unit we think? (T0 = the time C2 prints DISARMED; 60-min box starts, F2)
Read from C2's survey (no new command):
- `software_sha.txt` = `0d03a62cf565`;
- `camera_schedule.yaml` sha256 = `117876ae0928b4946c511a8219071150beab6b6f646c9304dc2657b80b16f363` (md5 37fd4ffb905d…).
  The reference is aoml-production 6cb38dd `docs/aoml_reef_2026-08-18/shipped/bmcam002_camera_schedule.yaml`, NOT
  `device_profiles/bmcam002/camera_schedule.yaml` (that one has a provenance header).
- `camera_config.yaml`, `bm_command_state.json`, `bm_command_state_v2.json`, `FORCE_SHRINK_FALLBACK`, `NO_HALT` absent;
- repo checkout at `/home/pi/repos/bm_cam_legacy`; PyYAML present; > 500 MB free; serial0 → ttyAMA0.
- the saved armed crontab (`crontab_backup_fieldupdate_<TS>.txt`) has the cycle line
  `@reboot /bin/bash /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh` (8/18 deployment doc), now shown commented
  (`# DISABLED noaa_catch …`).
  - Another wrapper here → stop at step 0, because A1 expects that line.
  - If the survey says `already disarmed`, the re-arm file is the one it names (that earlier run's backup), not a new one.

**Stop (F3):** sha or yaml hash differ → stop, change nothing, ask Nick. Any other survey miss (no checkout, no PyYAML, a
v2 or state file present) → stop and ask Nick; the rehearsal didn't cover it.

## D. SD download first (box 10 min; F5, §10)
- D1 (Nick, Mac), two commands, one directory each. The Mac's `/usr/bin/rsync` is 2.6.9, which rejects two remote
  sources in one command (QE 011 R1):
  - `rsync -a --stats pi@bmcam002:/home/pi/BM_Devel_Pi/images ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/sd/`
  - `rsync -a --stats pi@bmcam002:/home/pi/BM_Devel_Pi/cron_logs ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/sd/`
- D2 (Nick, Mac): `ssh pi@bmcam002 'find /home/pi/BM_Devel_Pi/images /home/pi/BM_Devel_Pi/cron_logs -type f | wc -l; du -sk /home/pi/BM_Devel_Pi/images /home/pi/BM_Devel_Pi/cron_logs'`
- Want: the file count and KB match the Mac copy (TE checks with `find … | wc -l` and `du -sk` locally).
- Over the hotspot + a Tailscale relay, this is ~10 s per MB (skill bmcam-hotspot-update). If C2's `du` is over ~50 MB,
  the TE says so before D1 and Nick decides: copy all (longer box) or `cron_logs` + the newest images only.

## U. Update (box 20 min)
- U1 (Nick, Mac): `scp ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/stage/{rc_field_update.sh,camera_config.yaml} pi@bmcam002:/tmp/`
- U2 (Nick): `ssh pi@bmcam002 'sha256sum /tmp/rc_field_update.sh /tmp/camera_config.yaml'`
  - Want: both equal the staged sha256 in the log (camera_config.yaml 412bcafe00cd…).
- U3 (Nick): `ssh pi@bmcam002 'git -C /home/pi/repos/bm_cam_legacy fetch origin && git -C /home/pi/repos/bm_cam_legacy checkout --detach 613fb2c15037'`
  - Want: `HEAD is now at 613fb2c1`.
  - `rc_field_update.sh --ref` pulls a branch, so the exact sha is checked out here and the script runs with
    `--skip-repo-sync`.
  - **Don't use `--ref development`:** once this PR merges, development is no longer 613fb2c1, and software_sha would
    differ from the soak's.
- U4 (Nick): `ssh pi@bmcam002 'bash /tmp/rc_field_update.sh --repo /home/pi/repos/bm_cam_legacy --skip-repo-sync --profile bmcam002 --leave-disarmed --accept-print-config-diff'`
  - Want: every stage OK and a printed rollback block. The TE copies the `tar xzf …BM_Devel_Pi_before_rc_deploy_…tgz`
    line into the log.
  - **Ignore the `crontab …` rollback line it prints** (QE 011 N3). Its crontab backup was taken after C2 disarmed the
    unit, so it is a disarmed crontab. Re-arming (A1, R4) always uses C2's armed file.
  - **Expected print-config diff (F6), desk-checked Mon on the shipped file:**
    - a new `[BOOT] main_entry uptime_s=…` line;
    - `source=yaml` added to the `power_halt:` and `schedule:` lines (`2026-10-26_rehearsal_noaa_update/desk/pc_diff.txt`).
  - Any other diff line → stop and roll back (R).
  - `--profile bmcam002` only feeds stage 4's bm_serial patch: 384 / 1.0, the same values as shipped.
- U5 (Nick): `ssh pi@bmcam002 'cat /home/pi/BM_Devel_Pi/software_sha.txt; test ! -e /home/pi/BM_Devel_Pi/FORCE_SHRINK_FALLBACK && echo FSF_ABSENT'`
  - Want: `613fb2c15037…` and `FSF_ABSENT` (envelope trap 3).

## M. v1 → v2 migration, then the target profile (box 10 min)
- M1 (Nick): `ssh pi@bmcam002 'python3 /home/pi/repos/bm_cam_legacy/tools/config_migrate_v1_v2.py --app /home/pi/BM_Devel_Pi'`
  - Want: exit 0, `result: OK`, `[MIGRATE] dry-run OK`; Mon's desk run on the shipped file is `desk/migrate_dry.txt`.
  - Exit 3 (refused) → stop, roll back (R).
- M2 (Nick): `ssh pi@bmcam002 'python3 /home/pi/repos/bm_cam_legacy/tools/config_migrate_v1_v2.py --app /home/pi/BM_Devel_Pi --write'`
  - Want: `wrote …/camera_config.yaml` and `OK`. The v1 files are untouched.
- M3 (Nick): `ssh pi@bmcam002 'cd /home/pi/BM_Devel_Pi && cp camera_config.yaml camera_config.yaml.migrated_1027 && cp /tmp/camera_config.yaml camera_config.yaml && sha256sum camera_config.yaml'`
  - Want: 412bcafe00cd….
  - This installs the target over the migrated file. Rev 12.2: the target is a v2 file, so the timezone, window and
    fallback are in BASE (trap 2).
- M4 (Nick): `ssh pi@bmcam002 'cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config'`
  - Want:
    - `[CFG] config v2 … hash=fe87abab … overlay=0 key(s)`;
    - `schedule: window=10:00-15:00 tz=America/New_York`;
    - `crop_xywh=(1504, 846, 1600, 900)`;
    - 0 `[CFG][ERR]` (desk copy `desk/print_config_target_613fb2c1.txt`).
  - Hash ≠ fe87abab or overlay ≠ 0 → stop, roll back (R).
- M5, the runtime (spec rev 12.3 N2):
  `ssh pi@bmcam002 'cd /home/pi/BM_Devel_Pi && python3 -c "import config_v2 as C; c = C.load_config(\"camera_config.yaml\"); print(c.base[\"commands.runtime\"], c.base[\"time.allow_system_fallback\"], C.config_hash(c.base))"'`
  - Want: `supervisor False fe87abab`.
  - Text `--print-config` doesn't print the runtime: `rc_progressive_jpeg.py:1833` skips the `[RUNTIME]` line during
    inspection, and `--json` doesn't carry it either (desk check Mon). So this one-liner reads the same v2 file with the
    loader the runtime uses.
  - K1's run log then shows the runtime actually resolved: `[RUNTIME] supervisor (source=…)`.

## K. Capture-only check (box 10 min; R12-1 checks 1, 2, 4)
- K1 (Nick): `ssh pi@bmcam002 'cd /home/pi/BM_Devel_Pi && touch NO_HALT && mkdir -p /home/pi/noaa_check && python3 rc_progressive_jpeg.py --skip-time-window --output-dir /home/pi/noaa_check > /home/pi/noaa_check/run.log 2>&1; echo exit $?; rm -f NO_HALT; tail -5 /home/pi/noaa_check/run.log'`
  - What it does:
    - no `--transmit`, so it never touches the BM bus;
    - `--skip-time-window` because there is no Spotter UTC on land;
    - `NO_HALT` is the halt escape hatch (`rc_power_halt.py` NO_HALT_FLAG), so the Pi stays up; it is removed in the same
      command.
  - Want: exit 0, `[RUNTIME] supervisor` in run.log (`grep RUNTIME /home/pi/noaa_check/run.log`), and a
    `[RC] send plan (NO transmit): … fmt=nrjxl` line.
  - The rehearsal confirms this command on the 613fb2c1 supervisor config (card S5).
- K2 (Nick, Mac): `scp -r pi@bmcam002:/home/pi/noaa_check ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/`
- K3 (TE, Mac): `/usr/bin/python3 -I tools/noaa_capture_check.py --nvd <nvd export> --log …/noaa_check/run.log …/noaa_check/<newest>.nrjxl`
  - Want `CHECK PASS`:
    - crop [1504, 846, 1600, 900], params[26] = 10000;
    - parses with nvd staging `nrjxl.py`, djxl decodes 1600×900;
    - fmt=nrjxl with no `rfb=`.
  - Then open the decoded image and look: in focus, exposed, the reef-housing view NOAA expects.
- K4 (Nick): `ssh pi@bmcam002 'test ! -e /home/pi/BM_Devel_Pi/NO_HALT && echo NO_HALT_ABSENT'`. Want `NO_HALT_ABSENT`.
- One failed check counts as a failed step. Retry once (abort rule).

## A. Re-arm and leave the backups (box 5 min)
- A1 (Nick): `ssh pi@bmcam002 'crontab /home/pi/crontab_backup_fieldupdate_<TS from C2>.txt && crontab -l'`
  - Want: the original `@reboot … rc_run_capture_cycle.sh` line, live (not commented).
- A2 (Nick): `ssh pi@bmcam002 'ls -l /home/pi/backups/ /home/pi/crontab_*fieldupdate* /home/pi/BM_Devel_Pi/camera_schedule.yaml* /home/pi/BM_Devel_Pi/camera_config.yaml*'`
  - Want: the deploy tar, both crontab copies, the shipped v1 YAML and the migrated copy, all left on the unit.
- **Pass line (handover-ready, spec rev 1 + R12-1), each with its log line:**
  - step 0 matched;
  - sha 613fb2c15037 + hash fe87abab (M4) + runtime supervisor (M5, K1);
  - capture check PASS (K3);
  - cron re-armed (A1);
  - backups on the unit (A2);
  - SD download verified (D2).
- Then NOAA may unplug it. The next power-on runs a normal cycle, and without a Spotter it skips and halts. That is
  expected.

## R. Rollback to 0d03a62 (box 10 min; F2, F4, F6)
Use on: any step failing twice, the 60-min box passing, or a "roll back" above.
- R1 (Nick): `ssh pi@bmcam002 'pkill -TERM -f "[r]c_progressive_jpeg.py|[r]c_run_capture_cycle.sh"; tar xzf /home/pi/backups/BM_Devel_Pi_before_rc_deploy_<host>_<TS>.tgz -C /home/pi'`
  - The tar name is the one U4 printed.
- R2 (Nick): `ssh pi@bmcam002 'cd /home/pi/BM_Devel_Pi && for f in camera_config.yaml bm_command_state_v2.json; do [ -e $f ] && mv $f $f.off_1027; done; rm -f NO_HALT; ls'`
  - 0d03a62 ignores the v2 files. They are moved aside so a later forward update re-migrates.
- R3 (Nick): `ssh pi@bmcam002 'cat /home/pi/BM_Devel_Pi/software_sha.txt; sha256sum /home/pi/BM_Devel_Pi/camera_schedule.yaml; cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config | tail -8'`
  - Want: `0d03a62cf565`, `117876ae0928…`, and a print-config that matches `desk/pc_old.txt`.
- R4 (Nick): `ssh pi@bmcam002 'crontab /home/pi/crontab_backup_fieldupdate_<TS from C2>.txt && crontab -l'` (re-armed, as found).
- **Stop (F4):** if R3 or R4 doesn't match, don't hand over. Leave it disarmed (the C2 crontab), tell NOAA to keep
  bmcam002 on land, backup day 10/29.

## H. Backend at handover (TE; after the pass line; SPOT-33361C = field unit, Nick's words in the TE chat)
Token: an admin token for nvd staging on the Mac (TE5 used `~/.config/nereus/staging.env` for writes, 10/23). Each
request and response goes in the log.
- H1 pre-checks (read-only; spec rev 9 N1, rev 10 must do 8):
  - `GET /systems/<SPOT-33361C system_id>/heal-events?hours=744`;
  - `GET /admin/devices/BMCAM_001/commands`, `GET /admin/devices/BMCAM_002/commands`;
  - `GET /admin/gateways/SPOT-33361C/rollout`.
  - Pass: no heal ask or command without a final status (else stop and ask Nick). Record `remote_commands`.
  - Read, don't change, `BM_COMMAND_SEND`, `BM_REMOTE_CONFIG` and the poll worker's `BM_HEAL_AUTOSEND`. How to read them
    on Render: **unknown**; the TE finds out from the nvd repo docs Mon evening, or asks the Lead.
- H2: the handover instant, `date -u +%Y-%m-%dT%H:%M:%SZ`, into the log first (rev 9 N2: no future `heal_since`).
- H3 must do 7: `PATCH /admin/gateways/SPOT-33361C/rollout {"self_heal": true, "heal_since": "<H2>", "heal_cap_per_day": 24}`.
- H4 must do 8: if H1 showed `remote_commands: false`, `PATCH … {"remote_commands": true}`. Else nothing (Nick 10/18: keep it
  on).
- H5: `GET /admin/gateways/SPOT-33361C/rollout`. Want all of:
  - `self_heal_active: true`;
  - `heal_since` = H2;
  - `heal_cap_effective: 24`;
  - `devices` non-empty;
  - `remote_commands: true` and `remote_commands_active: true`;
  - `link: cellular`.
  - Else F7/F8: don't call it handed over; tell Nick.
  - Revert, if Nick asks: `{"self_heal": false, "heal_since": null}` / `{"remote_commands": false}`.
- Nothing is sent to bmcam002 or SPOT-33361C on the day.

## F9 note (Spotter UTC)
Only if a Spotter is attached to bmcam002 on land (must do 1; spec says none): a `--transmit`-free capture can't show
"spotter UTC decoded". If one is attached, K1 is re-run without `--skip-time-window`. Want "spotter UTC decoded" + a media
key in run.log. Else one Spotter reset: that is a field-unit action and needs Nick's words in the executing chat.

## Time boxes (sum ≤ 60 min from T0; the rehearsal replaces these with measured times)
| Step | Box | Rehearsal measured |
|---|---|---|
| 0 Step 0 | 2 min | (rehearsal) |
| D SD download | 10 min | (rehearsal; size-dependent) |
| U Update | 20 min | (rehearsal) |
| M Migration + target | 10 min | (rehearsal) |
| K Capture check | 10 min | (rehearsal) |
| A Re-arm + pass line | 5 min | (rehearsal) |
| Slack | 3 min | |
| R Rollback (if used) | 10 min | (rehearsal) |
