# Runbook: bmcam002 on-land update for NOAA, Tue 10/27 1:00 PM PT (backup day Thu 10/29)

TE 006, rev 2 (Mon 10/26 PM): the installer path. Spec: `~/NereusData/ops/pe_specs/SPEC_e4_noaa_release.md` rev 13.10
(sha256 21d3917ef58e): M13-1 to M13-6, M13-4b (rev 13.9–13.10), rev 13.3 N2–N5, rev 13.5 (continuous power, F14, F16), rev 13.7 (1:00 PM start),
R12-1, F1–F16. Rev 1 (the 613fb2c1 step-by-step path, #188 @ 56dd7ca3) is replaced: the release now carries its own
dependencies and a hard fail (Nick, c_noaa_update_plan_1027 decided_2). bmcam002 is a **field unit**: every on-unit step and
every SPOT-33361C step runs only on Nick's own words in the executing TE chat (TEAM_RULES §6). Rehearsed on bmcam004 first
(`runs/rehearsal_noaa_update_20261026/TEST_CARD.md` rev 5).

## What changes, in one line
bmcam002 goes from legacy **0d03a62** + the shipped v1 profile (sha256 117876ae0928…) to release tag
**bmcam-rel-20261027** on sha **`<REL_SHA>`** (the sha the Lead records when pushing the tag after QE PASS; rev 13.8 item 2)
+ the v2 target profile `device_profiles/bmcam002/noaa_20261027/camera_config.yaml` (sha256 **412bcafe00cd…**, config hash
**fe87abab**), with cjxl ≥ 0.11 installed from the release's offline .deb set (`releases/bmcam-rel-20261027/README.md`).

## Who does what
- **NOAA staff (on site):** SPOT-33361C in continuous mode for the whole window (theirs; Nick, decided_4 / rev 13.5); the
  phone hotspot; a camera or bus power-cycle only when Nick asks (section P, F14).
- **Nick (Mac, Tailscale SSH):** types every command in sections C–R, one at a time, and reads the result back.
- **Console test sender (T3):** who types the `trg` line at SPOT-33361C's console, and how they reach that console:
  **unknown**. Nick wrote "NOAA should be able to issue a command over the spotter console" (decided_2). Nick names the
  sender before the day (item 2 below).
- **TE (on the line):** says which command is next, checks each result against the "want" line, keeps the log and the
  clock, and calls a stop.
- **TE, backend only (section H, after the pass line):** must dos 7–8 for SPOT-33361C, on Nick's words in the TE chat.
- Log: the TE writes every command, its output and the time (`date`) to
  `~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/runbook.log`.

## Before the day (TE; none of this touches bmcam002)
1. The rehearsal card rev 5 has passed 5/5 on `<REL_SHA>` (QE, Nick's OK, run), and its timings are in the time boxes.
2. From Nick (must do 1 is checked off, rev 13.6; these items are still open):
   - the hotspot SSID and password bmcam002 already knows (the Pi joins only a network it knows; skill
     bmcam-hotspot-update, Phase 1);
   - who sends the T3 console line, and how they reach SPOT-33361C's console.
3. Staged on the Mac (TE), each with its sha256 written in the log, in `~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/stage/`:
   - `release/`: `install_release.sh` and `release_manifest.py` (from `tools/` at `<REL_SHA>`),
     `release_manifest.json` (from `releases/bmcam-rel-20261027/` at `<REL_SHA>`), and `debs/` (copied from
     `~/NereusData/bm_cam_legacy/2026-10-26_release_debs/trixie_arm64/debs/`, 55 files, ~20 MB on Mon 10:10 PM);
     `git archive <REL_SHA> tools/install_release.sh tools/release_manifest.py releases/bmcam-rel-20261027/release_manifest.json`;
   - `noaa_catch_disarm.sh` and `noaa_capture_check.py` (from `tools/` at `<REL_SHA>`);
   - an nvd export at the `staging` sha deployed Tue (`git archive <sha> backend/app/services/nrjxl.py`). How the TE
     reads the deployed sha: **unknown**; the TE asks the Lead by 11 AM Tue.
4. The envelope at `<REL_SHA>` is approved by Nick (the withdrawn team #51 is replaced; rev 13 §10).

## P. NOAA phone sheet (one page; read to NOAA staff on the call)
1. SPOT-33361C is in continuous mode before the call starts (NOAA's).
2. iPhone: Settings → Personal Hotspot → **Maximize Compatibility ON** (the Pi only sees 2.4 GHz).
3. The phone's name (= hotspot SSID) and the hotspot password must match what Nick gives, **letter for letter**. Turn off
   Settings → General → Keyboard → Smart Punctuation before typing an apostrophe.
4. Stay on the Personal Hotspot screen with the screen awake until the camera connects.
5. Only when Nick asks: power-cycle the camera the way NOAA staff power the Spotter's bus (theirs; rev 13.5 F14). Hot-plugging
   the camera cable is **not** on this sheet: it was not rehearsed (rev 13.3 N4).
6. Success = a blue "1 connection" banner on the phone within ~3–5 min of the camera powering up. Tell Nick when you see it.
7. Keep the phone within a few metres of the camera.

## C. Catch it awake (time box 20 min from the start of the window, F1, F16)
bmcam002 is armed with a real halt. 1:00 PM PT = 4:00 PM ET is outside its 10:00–15:00 ET window, so a boot runs a cycle
that logs `skip_win` and halts. That leaves ~1–2 min. Start the catch loop BEFORE the power-up.
- C1 (Nick, Mac): `/Applications/Tailscale.app/Contents/MacOS/Tailscale ping bmcam002`. Want: pong. Tailscale IP on
  8/18: 100.118.150.44 (aoml-production `docs/DEPLOYMENT_AOML_REEF_2026-08-18.md`).
- C2 (Nick, Mac):
  `until ssh -o ConnectTimeout=3 pi@bmcam002 'bash -s' < ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/stage/noaa_catch_disarm.sh; do sleep 2; done`
  - Want: `[CATCH] DISARMED`, then the `[SURVEY]` lines.
  - If ssh prints a "Tailscale SSH requires an additional check" URL, Nick opens it and approves.
- **Stop (F16, rev 13.5):** the Pi isn't reachable at the start of the window (the bus may not be in continuous mode) →
  stop and tell the Lead; Nick talks to NOAA. No retries that assume a power state.
- **Stop (F1):** nothing in 20 min → one power-cycle by NOAA staff on Nick's request (P5). Still nothing after another
  15 min → backup day 10/29.

## 0. Step 0: is it the unit we think? (T0 = the time C2 prints DISARMED; the 60-min box starts, F2)
Read from C2's survey (no new command):
- `software_sha.txt` = `0d03a62cf565`;
- `camera_schedule.yaml` sha256 = `117876ae0928b4946c511a8219071150beab6b6f646c9304dc2657b80b16f363` (md5 37fd4ffb905d…).
  The reference is aoml-production 6cb38dd `docs/aoml_reef_2026-08-18/shipped/bmcam002_camera_schedule.yaml`, NOT
  `device_profiles/bmcam002/camera_schedule.yaml` (that one has a provenance header).
- `camera_config.yaml`, `bm_command_state.json`, `bm_command_state_v2.json`, `FORCE_SHRINK_FALLBACK`, `NO_HALT` absent;
- repo checkout at `/home/pi/repos/bm_cam_legacy`; PyYAML present; > 500 MB free; serial0 → ttyAMA0.
- the saved armed crontab (`crontab_backup_fieldupdate_<TS>.txt`) has an `@reboot` line that runs
  `/home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh`, now shown commented (`# DISABLED noaa_catch …`).
  - Two forms are known, and either is fine: `@reboot /bin/bash …/rc_run_capture_cycle.sh` (bmcam002, 8/18 deployment
    doc) and `@reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock …/rc_run_capture_cycle.sh` (bench units).
  - No `@reboot` line running `rc_run_capture_cycle.sh` → stop at step 0, because A1 expects one.
  - If the survey says `already disarmed`, the re-arm file is the one it names (that earlier run's backup), not a new one.

**Stop (F3):** sha or yaml hash differ → stop, change nothing, ask Nick. Any other survey miss (no checkout, no PyYAML, a
v2 or state file present) → stop and ask Nick; the rehearsal didn't cover it. The OS, arch and Python are read by I3's
step 1 (F13), not here.

## D. SD download first (box 10 min; F5, §10)
- D1 (Nick, Mac), two commands, one directory each. The Mac's `/usr/bin/rsync` is 2.6.9, which rejects two remote
  sources in one command (QE 011 R1 on #188):
  - `rsync -a --stats pi@bmcam002:/home/pi/BM_Devel_Pi/images ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/sd/`
  - `rsync -a --stats pi@bmcam002:/home/pi/BM_Devel_Pi/cron_logs ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/sd/`
- D2 (Nick, Mac): `ssh pi@bmcam002 'find /home/pi/BM_Devel_Pi/images /home/pi/BM_Devel_Pi/cron_logs -type f | wc -l; du -sk /home/pi/BM_Devel_Pi/images /home/pi/BM_Devel_Pi/cron_logs'`
- Want: the file count and KB match the Mac copy (TE checks with `find … | wc -l` and `du -sk` locally).
- Over the hotspot + a Tailscale relay, this is ~10 s per MB (skill bmcam-hotspot-update). If C2's `du` is over ~50 MB,
  the TE says so before D1 and Nick decides: copy all (longer box) or `cron_logs` + the newest images only.

## I. Install the release (box 20 min; M13-2)
- I1 (Nick, Mac): `scp -r ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/stage/release pi@bmcam002:/tmp/`
  - ~20 MB of debs: ~200 s at ~10 s per MB.
- I2 (Nick): `ssh pi@bmcam002 'cd /tmp/release && sha256sum install_release.sh release_manifest.py release_manifest.json && ls debs | wc -l'`
  - Want: the three sha256 equal the staged ones in the log, and `55`. The installer checks each deb's sha256 itself
    (step 2).
- I3 (Nick): `ssh pi@bmcam002 'bash /tmp/release/install_release.sh --unit bmcam002 --tag bmcam-rel-20261027 --sha <REL_SHA> --manifest /tmp/release/release_manifest.json --debs /tmp/release/debs'`
  - The TE copies the command's **second line** (the log path, `/home/pi/release_logs/install_release_<UTC>.log`) into
    the runbook log at once.
  - Want, last line: `[INSTALL] PASS tag=bmcam-rel-20261027 sha=<REL_SHA 12> cfg=fe87abab runtime=supervisor …`, exit 0.
  - What it does (README; the TE doesn't re-type these): records OS / arch / Python; installs only missing packages from
    the offline debs (no network, no `apt-get update`); checks cjxl ≥ 0.11, the Python imports, rpicam-still and a
    1-image cjxl encode; checks the tree is clean (repairs only 0-byte power-cut damage, as #188); checks out the tag's
    sha (fetching the tag from GitHub if the unit lacks it); runs `rc_field_update.sh --skip-repo-sync
    --leave-disarmed`; migrates v1 → v2; installs the target profile; then post-checks sha, hash, runtime, clean tree,
    0-byte files and the dependencies again.
  - **SSH drop (QE 011 R2 on #189):** the installer runs detached, so a dropped session stops only the following, never
    the install. Reconnect and re-attach:
    `ssh pi@bmcam002 'tail -n +1 -f /home/pi/release_logs/install_release_<UTC>.log'` (the path from the second line)
    until the `[INSTALL] PASS / FAIL / STOP` line, then Ctrl-C and read the exit code:
    `ssh pi@bmcam002 'cat /home/pi/release_logs/install_release_<UTC>.log.rc'`.
    **Never start a second I3 while the first log has no `.rc` file.** The drop and the re-attach are not a retry.
  - Exit 1, `[INSTALL] FAIL step N`:
    - N ≤ 3 (or 3a): the runtime is untouched. Stop, change nothing more, re-arm (A1) and ask Nick (F12 / F13 class).
    - N ≥ 4: roll back (R).
    - A failed package install says whether to run `sudo dpkg --configure -a`; run it only if it says so, then ask Nick.
  - Exit 3, `[INSTALL] STOP` (wrong unit, an OS / arch / Python the release has no target for (F13: bmcam002's OS is
    unknown until this step), or a dirty tree that isn't power-cut damage): stop, re-arm (A1), ask Nick.
- I4 (Nick), an independent read-back, one command:
  `ssh pi@bmcam002 'cd /home/pi/BM_Devel_Pi && cat software_sha.txt; test ! -e FORCE_SHRINK_FALLBACK && echo FSF_ABSENT; command -v cjxl && cjxl --version 2>&1 | head -1; python3 rc_progressive_jpeg.py --print-config | grep -E "hash=|schedule:|crop_xywh|CFG.\[ERR"; python3 -c "import config_v2 as C; c = C.load_config(\"camera_config.yaml\"); print(c.base[\"commands.runtime\"], c.base[\"time.allow_system_fallback\"], C.config_hash(c.base))"'`
  - Want: `<REL_SHA 12>`, `FSF_ABSENT`, a cjxl path + `cjxl v0.11.x`, `[CFG] config v2 … hash=fe87abab … overlay=0 key(s)`,
    `schedule: window=10:00-15:00 tz=America/New_York`, `crop_xywh=(1504, 846, 1600, 900)`, no `[CFG][ERR]` line, and
    `supervisor False fe87abab`.
  - Any miss → roll back (R).
- The tar to roll back with: `ssh pi@bmcam002 'grep -o "/home/pi/backups/BM_Devel_Pi_before_rc_deploy_[^ ]*\.tgz" /home/pi/release_logs/install_release_<UTC>.log | head -1'`
  → into the runbook log (R1 uses it). The path appears twice in the log (deploy_rc_runtime.sh's "backing up" and
  "restore command" lines); both name the same tar. Use the log of the I3 run that did step 4: a re-run that skipped
  step 4 (unit already on the release) takes no tar and prints no such line.

## A. Re-arm (box 2 min)
- A1 (Nick): `ssh pi@bmcam002 'crontab /home/pi/crontab_backup_fieldupdate_<TS from C2>.txt && crontab -l'`
  - Want: the original `@reboot … rc_run_capture_cycle.sh` line, live (not commented).
  - Never the crontab line `rc_field_update.sh` printed: its backup was taken after C2 disarmed the unit.
- A2 (Nick): `ssh pi@bmcam002 'ls -l /home/pi/backups/ /home/pi/crontab_*fieldupdate* /home/pi/BM_Devel_Pi/camera_schedule.yaml* /home/pi/BM_Devel_Pi/camera_config.yaml*'`
  - Want: the deploy tar, the crontab backup, the shipped v1 YAML and the replaced migrated config, all left on the unit.

## T. Console test: one triggered image to the backend (box 20 min; M13-4, F14, F15)
- T1 (Nick): `ssh pi@bmcam002 'sudo reboot'`. The armed boot runs one cycle on the release runtime. At 1:00 PM PT it is out
  of the 10:00–15:00 ET window: a supervised per_boot wake sends `<WS a=skip_win>`, its off-window heals, then runs the
  `listen_tail_s` = 150 s listen tail (spec rev 13.9, `tests/test_heal_offwin.py:311`). The legacy runtime has no tail, so
  this needs `[RUNTIME] supervisor`.
- T2 (Nick, Mac): `until ssh -o ConnectTimeout=3 pi@bmcam002 'grep -h -E "RUNTIME|skip_win|listen|spotter UTC" $(ls -t /home/pi/BM_Devel_Pi/cron_logs/* | head -1)'; do sleep 2; done`
  - Want: `[RUNTIME] supervisor`, `skip_win`, then the listen-tail start line. The TE starts a 150 s clock at that line.
  - Exact listen line text: **from the rehearsal** (rev 5 card, the out-of-window run); until then, the TE reads
    the newest cron log.
- T3 (the console sender, within the 150 s tail): first `bm pub bmcam/cmd {"id":<N>,"c":"get","k":["mode"]} 1 1`
  (starts the 300 s keepalive), then `bm pub bmcam/cmd {"id":<N+1>,"c":"trg","v":2} 1 1`, with N in 1–99,999, never one
  of the last ~32 ids used on this unit (the TE picks N and logs it).
  - Want, at the console: the ack `{"id":<N+1>,"ok":1,"h":"fe87abab"}` and a triggered START with `tg=<N+1>`.
  - A missed tail (no ack; the Pi has halted) = F14: the Pi stays down on continuous power. Recovery is NOAA staff
    power-cycling on Nick's request (P5), then C → T1 again, or the backup day (rev 13.5).
- T4 (TE, backend; read-only), within 15 min of the ack: `GET /devices/BMCAM_002/media` (staging, admin token as in H).
  - Want: a new row captured after the T3 ack, complete, format nrjxl.
  - Then the bytes: `scp pi@bmcam002:'/home/pi/BM_Devel_Pi/images/<newest>.nrjxl' ~/NereusData/bm_cam_legacy/2026-10-27_noaa_update/trg/`
    while the keepalive holds the Pi up, and
    `/usr/bin/python3 -I tools/noaa_capture_check.py --nvd <nvd export> --log <the T2 cron log, copied> <the .nrjxl>`.
    Want `CHECK PASS`: crop [1504, 846, 1600, 900], params[26] = 10000, parses with nvd staging `nrjxl.py`, djxl decodes
    1600×900, fmt=nrjxl with no `rfb=` (R12-1). Then open the decoded image and look: in focus, exposed, the view NOAA
    expects.
- T5 (F15, report only): whether the cycle log shows "spotter UTC decoded" and a `[KEY] media key` (an indoor Spotter may
  have no GPS fix). The TE writes yes/no into the log; it doesn't fail T.
- After T: the Pi halts at the end of its keepalive. On continuous power it then stays halted (rev 13.6 N1: `per_boot`
  makes one wake per power-up; Nick passes this to NOAA).
  - If R is needed after T, catch it **before** the keepalive ends: `ssh pi@bmcam002 'touch /home/pi/BM_Devel_Pi/NO_HALT'`
    (the halt escape hatch, `rc_power_halt.py` NO_HALT_FLAG); R2 removes it.
- **Pass line (handover-ready), each with its log line:** step 0 matched; `[INSTALL] PASS` (I3); I4's read-back; cron
  re-armed (A1); backups on the unit (A2); the T3 ack; T4's backend row + `CHECK PASS`; SD download verified (D2).

## R. Rollback to 0d03a62 (box 10 min; F2, F4, F6)
Use on: an I3 FAIL at step ≥ 4, an I4 miss, a T failure, any step failing twice, or the 60-min box passing.
- R0: the Pi must be up and caught. If it halted, it comes back only by a power-cycle (P5, Nick's request to NOAA), then C2.
- R1 (Nick): `ssh pi@bmcam002 'pkill -TERM -f "[r]c_progressive_jpeg.py|[r]c_run_capture_cycle.sh"; tar xzf <the tar from I4> -C /home/pi && sync'`
- R2 (Nick): `ssh pi@bmcam002 'cd /home/pi/BM_Devel_Pi && for f in camera_config.yaml bm_command_state_v2.json camera_config.lkg.json config_journal.jsonl; do [ -e $f ] && mv $f $f.off_1027; done; rm -f NO_HALT; sync; ls; echo DIRTY=$(git -C /home/pi/repos/bm_cam_legacy status --porcelain | wc -l)'`
  - Want: `DIRTY=0` (must do 3a). Else F11: don't hand over a dirty tree.
  - 0d03a62 ignores the v2 files. They are moved aside so a later forward update re-migrates.
  - The packages the installer added stay installed: 0d03a62 doesn't use them, and removing packages from a field unit is
    not in this plan.
- R3 (Nick): `ssh pi@bmcam002 'cat /home/pi/BM_Devel_Pi/software_sha.txt; sha256sum /home/pi/BM_Devel_Pi/camera_schedule.yaml; cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config | tail -8'`
  - Want: `0d03a62cf565`, `117876ae0928…`, and a print-config that matches `desk/pc_old.txt` (rehearsal run folder).
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
    on Render: **unknown**; the TE asks the Lead by 11 AM Tue.
- H2: the handover instant, `date -u +%Y-%m-%dT%H:%M:%SZ`, into the log first (rev 9 N2: no future `heal_since`).
- H3 must do 7: `PATCH /admin/gateways/SPOT-33361C/rollout {"self_heal": true, "heal_since": "<H2>", "heal_cap_per_day": 24}`.
- H4 must do 8: if H1 showed `remote_commands: false`, `PATCH … {"remote_commands": true}`. Else nothing (Nick 10/18: keep it
  on).
- H5: `GET /admin/gateways/SPOT-33361C/rollout`. Want all of: `self_heal_active: true`; `heal_since` = H2;
  `heal_cap_effective: 24`; `devices` non-empty; `remote_commands: true` and `remote_commands_active: true`;
  `link: cellular`. Else F7/F8: don't call it handed over; tell Nick.
  - Revert, if Nick asks: `{"self_heal": false, "heal_since": null}` / `{"remote_commands": false}`.
- Nothing is sent to bmcam002 or SPOT-33361C from the backend on the day.

## F11 note (power lost mid-update; SPEC_e4 rev 12.8)
If bmcam002 goes dark during I3 or < 60 s after a write step: after it powers up, catch it (C), then read the I3 log's last
lines and `.rc` (if any), then
`ssh pi@bmcam002 'git -C /home/pi/repos/bm_cam_legacy status --porcelain | wc -l; cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config | grep -E "hash=|schedule:"; find /home/pi/BM_Devel_Pi -path "*/images" -prune -o -type f -size 0 -print'`.
- A cut during step 2 can leave dpkg half-configured: `sudo dpkg --audit` (read-only) → anything listed = stop, ask Nick.
- Any dirty file, any 0-byte runtime file, or a hash/schedule that isn't the step's want → rollback to 0d03a62 (R; F2
  counts it). Re-running I3 after a cut is allowed once only if its log shows it stopped before step 4. Never hand over a
  dirty tree.

## Time boxes (sum ≤ 60 min from T0 to the pass line, + R if used; rev 12.8)
| Step | Box | Rehearsal measured (rev 5 card, median / max of 5) |
|---|---|---|
| 0 Step 0 | 2 min | (rehearsal) |
| D SD download | 10 min | (rehearsal; size-dependent) |
| I Install | 20 min | (rehearsal) |
| A Re-arm | 2 min | (rehearsal) |
| T Console test | 20 min | (rehearsal; the backend row ≤ 15 min of it) |
| Slack | 6 min | |
| R Rollback (if used) | 10 min | (rehearsal) |
