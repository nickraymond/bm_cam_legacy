# Test card: NOAA release rehearsal on bmcam004 (no-JXL start → installer → console trg → rollback), × 5, Tue 10/27 AM

**Rev 5 (TE 006, Mon 10/26 PM): the release path (SPEC_e4 rev 13.9, M13-6 + M13-4b).** Nick re-planned NOAA Mon ~8:30 PM
(c_noaa_update_plan_1027 decided_2): bmcam002 on 0d03a62 has no JPEG XL dependencies, a JPEG fallback is not acceptable, and
the update is a pinned release with an automated installer and hard dependency checks. Rev 4's 5/5 (Mon 5:23–5:52 PM @
56dd7ca3) **does not count** here: bmcam004 already had cjxl and numpy, so it couldn't catch that gap. The count is **0/5**
on the release sha (rev 13 M13-6). Rev 4's text is in git at 56dd7ca3; its steps that still hold are repeated below.

This card runs only after (all three):
- QE PASS on bm #189 (installer, https://github.com/nickraymond/bm_cam_legacy/pull/189), its merge, and the Lead's tag
  `bmcam-rel-20261027` with the sha the Lead records (`<REL_SHA>` below; rev 13.8 item 2);
- QE PASS on this card + RUNBOOK rev 2 at this PR's sha;
- Nick's own line in the **TE 006 chat** citing this card's sha and `<REL_SHA>` (TEAM_RULES §6; bm_cam_legacy CLAUDE.md
  wins on hardware).

- **Spec:** `~/NereusData/ops/pe_specs/SPEC_e4_noaa_release.md` rev 13.10 (sha256 21d3917ef58e): M13-2 to M13-6, M13-4b (13.9–13.10),
  rev 13.2 (purge scope), 13.3 N2–N5, 13.5 (F14, F16), 13.7 (1:00 PM start); R12-1; F1–F16.
- **Runbook under test:** `runs/noaa_update_20261027/RUNBOOK.md` rev 2 at this card's sha.
- **Release:** tag `bmcam-rel-20261027` @ `<REL_SHA>`; manifest `releases/bmcam-rel-20261027/release_manifest.json`;
  offline debs `~/NereusData/bm_cam_legacy/2026-10-26_release_debs/trixie_arm64/debs/` (55 files). Profile
  `device_profiles/bmcam002/noaa_20261027/camera_config.yaml` (sha256 412bcafe00cd…, hash fe87abab).
- **Units:** bmcam004 + SPOT-33507C only (TE 006 owns them). bmcam002, SPOT-33361C, bmcam003 and SPOT-31593C are not
  touched.

## Question
1. **M13-6, × 5 (Nick's bar, rev 12.5):** from bmcam002's start state with **no cjxl**, does RUNBOOK rev 2 install the
   release (dependencies included), get one console-triggered nrjxl image to the backend, and roll back to 0d03a62, with
   every want line met on the first try? **5 times in a row with no issue.**
2. **M13-6 negative checks (once per series):** a bad offline package set → installer FAIL with the runtime untouched; cjxl
   removed after an install → a trg gives a loud error and no JPEG (M13-3).
3. **QE 011 R2 (#189):** an SSH drop mid-install doesn't stop the install, and the runbook's re-attach line finds its result.
4. **F15 (report only):** does an indoor triggered image get Spotter UTC ("spotter UTC decoded" + a media key)?

## Open before run 1
- **Q1, out-of-window boot: answered by spec rev 13.9 / 13.10 (M13-4b).** bmcam002's window is 10:00–15:00 ET =
  07:00–12:00 PT, so the 1:00 PM PT update boots out of window. From code (PE 010, development @ 56dd7ca3): a supervised
  per_boot wake out of window sends `<WS a=skip_win>`, then heals, then runs the listen tail
  (`tests/test_heal_offwin.py:311`); a trg capture bypasses the window. Not yet seen on hardware, so one counted run boots
  out of window (Steps, "The out-of-window run").
- **Q2, network.** M13-6 says "indoors, phone hotspot". bmcam004 knows the bench Wi-Fi; a phone hotspot needs Nick's
  phone and bmcam004 knowing that SSID. Nick picks at his OK: (a) his hotspot, or (b) bench Wi-Fi as a logged deviation (as
  rev 4, his 12:00 PM choice Mon).

## Conditions
- **Power:** bmcam004 on SPOT-33507C's bus, held on since Mon 4:50 PM (Nick, TE 006 chat 2:04 PM: "bmcam004 is plugged into
  a spotter, you need to turn on the configuration for always on power"; `bus_on/run/gate.log`). No bridge-config change in
  this card (rev 13.5).
- **Console:** T3's lines go to SPOT-33507C's console through nereus000's USB console:
  `hil/tools/hil_console.sh SPOT-33507C '<line>' <timeout s>` (run from the run folder's `bus_on/` copy, which has the
  swapped rig map). That's a console command, not a bridge config change.
- **Who does the physical steps:** nobody, per run. Step C's restart is `sudo reboot` over ssh from the armed start state
  (rev 4 method). Logged deviation: a reboot isn't a power cut. If C2 or a post-T catch loses the race, the unit halts on a
  held bus and only a bus power-cycle brings it back: that is a bridge commit (a config change), so TE 006 stops and asks
  Nick, and the run is an issue.
- **Before each run (R0, not timed), all three, or the run doesn't start** (rev 4, unchanged):
  - nereus000: `ssh pi@100.82.38.22 'systemctl is-active spotter-monitor && ls /dev/serial/by-id/ | grep -c SPOT-33507C'`
    → `active` and `1`;
  - SPOT-33507C `batteryVoltage` (Sofar `GET /api/latest-data?spotterId=SPOT-33507C`, one read) ≥ 3.80 V;
  - re-arm only from the catch's `/home/pi/crontab_backup_fieldupdate_<TS>.txt` (armed, sha256 6e7a4080…).
- **Clock:** runs from QE PASS + Nick's OK until ~12:30 PM PT; 5/5 + Nick's go before 1:00 PM, else the backup day 10/29
  (rev 13.7, Nick's call).

## Steps
**B. Baseline (once, not timed).** Mon's B2 tar stays the restore point
(`/home/pi/backups/rehearsal_baseline_bmcam004_<TS>.tgz`, sha256 in the run folder). Record bmcam004's packages as found:
`dpkg-query -W -f='${Package} ${Version}\n' libjxl-tools libgif7 libjpeg-turbo-progs libtcmalloc-minimal4t64 python3-numpy python3-picamera2 python3-av python3-simplejpeg python3-pidng > /home/pi/rehearsal_pkgs_asfound.txt`
(Mon 8:37 PM read: libjxl-tools 0.11.2-0.1~deb13u2, python3-numpy 1:2.2.4+ds-1, python3-picamera2 0.3.36-1).

**S. The no-JXL start (before each run, not timed).** bmcam004 is in bmcam002's start state (0d03a62 + shipped YAML
117876ae…, armed) after Mon's Z. Then the purge (rev 13.2):
- Runs 1, 2, 4, 5, **cjxl-only:** `sudo apt-get -s purge libjxl-tools libgif7 libjpeg-turbo-progs libtcmalloc-minimal4t64 | grep -E "^(Purg|Remv)"`.
  Want exactly those 4 `Purg` lines. Then the same without `-s`, `-y`.
- Run 3, **full no-numpy:** the same with `python3-numpy python3-picamera2 python3-av python3-simplejpeg python3-pidng`
  added. Want exactly those 9 `Purg` lines (Mon's dry run listed the 6 from `libjxl-tools python3-numpy`; the 3 helper
  packages go by name).
- S-check, logged: `command -v cjxl; echo cjxl_rc=$?; python3 -c "import numpy" 2>&1 | tail -1; echo numpy_rc=$?`.
  Want `cjxl_rc=1` every run; `numpy_rc=1` on run 3, `numpy_rc=0` on the others.
- Then the start state again: `software_sha.txt` 0d03a62cf565, YAML 117876ae…, crontab armed (6e7a4080), repo clean.

**Runs 1–5 (each timed; T0 = DISARMED in C; per-step times into RUNS.md).** RUNBOOK rev 2 as written, `bmcam002` →
`bmcam004` in host names and `--unit bmcam004`; each run's output in `run<N>/` of the run folder.
- C → 0 → D → I → A → T → R, then Z.
- **T on the bench:** T1 `sudo reboot`; T2 as written; T3 by TE 006 through `hil_console.sh SPOT-33507C` (Conditions),
  ids from 1001 upward, +2 per run, logged; T4's backend read is
  `ssh pi@100.82.38.22 'python3 /home/pi/hil_g4/hil_media_table.py --api https://nereus-vision-staging.onrender.com --devices BMCAM_004 --since <T3 UTC>'`
  (read-only `GET /devices/BMCAM_004/media`) plus the `.nrjxl` pulled from bmcam004 and `noaa_capture_check.py`.
  Then the post-T catch (`touch NO_HALT` before the keepalive ends), because every run rolls back.
- **The out-of-window run (M13-4b, spec rev 13.10).**
  - **(a), preferred: run 5's T1 boots after 12:00 PM PT** with the target profile unchanged (C at ~11:40 AM). Want in
    T2's cron log: `<WS a=skip_win>`, then the listen tail, then T3's trg lands and T4 passes. Any earlier run whose T1
    falls after 12:00 PM PT covers it too.
  - **(b), only if the clock can't place a T1 after 12:00 PM PT before 1:00 PM:** in that run, after I3–I4 pass as usual
    and in T3's tail (in window), one logged console set, then a reboot that boots out of window:
    - `bm pub bmcam/cmd {"id":<N>,"c":"set","kv":{"schedule.window.start":"03:00","schedule.window.end":"04:00"}} 1 1`;
      want the ack with a new `h` (settings apply from the next boot; 03:00–04:00 ET = 00:00–01:00 AM PT).
    - `sudo reboot` before the keepalive ends; T2 wants `<WS a=skip_win>` then the tail; T3's trg (ack `h` = the set's
      hash); T4.
    - In that tail: `bm pub bmcam/cmd {"id":<N+2>,"c":"reset","k":["schedule.window.start","schedule.window.end"]} 1 1`;
      want the ack `h` = fe87abab. Then R as written. Each line, ack and hash goes in the log.
- **Run 2 adds the dropped-session check (QE 011 R2):** while I3 prints step 2 or step 4, TE 006 kills the local ssh
  (`kill` of the ssh pid on the Mac). Then the RUNBOOK I3 re-attach line. Want: the log reaches `[INSTALL] PASS`, `.rc` = 0,
  and `ssh pi@bmcam004 'pgrep -fc "[i]nstall_release.sh"'` = 0 after it (one installer, finished). Planned, so it isn't a
  retry.
- **The stopwatch:** T0 → the pass line ≤ 60 min; R ≤ 10 min more (rev 12.8 "60-min box + rollback").
- **Z, the reset before the next run (not timed).** R4 leaves 0d03a62 + shipped YAML, armed, except for the repo checkout
  (the installer moved it to `<REL_SHA>`), the release's packages, `/tmp/release`, `/home/pi/release_logs` and the
  trg/check outputs. So:
  - `git -C /home/pi/repos/bm_cam_legacy checkout --detach 0d03a62cf565 && git -C /home/pi/repos/bm_cam_legacy tag -d bmcam-rel-20261027 && rm -rf /tmp/release && sync`;
    want `git status --porcelain` empty and `git tag -l bmcam-rel-20261027` empty. The tag is deleted so every counted
    run's I3 fetches it from GitHub, as bmcam002's will (QE 011 N1 on #190; `install_release.sh:313–314`).
  - copy `/home/pi/release_logs/` to `run<N>/` on the Mac, then leave it on the unit;
  - after run 3: `sudo apt-get install -y --no-download /var/cache/apt/archives/{python3-picamera2,python3-av,python3-simplejpeg,python3-pidng}_*.deb`
    (numpy is back from the release's debs), then `dpkg -l python3-picamera2 python3-av python3-simplejpeg python3-pidng python3-numpy | grep -c ^ii`
    = 5 (rev 13.2: checked after).
  - then S for the next run. Any difference from the start state is an issue: fix it and reset the count.

**N. Negative checks (once in the series, not counted; rev 13 M13-6).**
- N1, before run 1, from the no-JXL start: a copy of the staged debs with one byte of one `.deb` changed
  (`/tmp/release_bad/debs`), then RUNBOOK I3 with `--debs /tmp/release_bad/debs`. Want: exit 1, `[INSTALL] FAIL step 2`
  naming that deb's sha256, and the runtime untouched (`software_sha.txt` 0d03a62cf565, YAML 117876ae…, `command -v cjxl`
  empty, repo HEAD 0d03a62). The offline install (rev 13.3 N2) replaces rev 13's "apt blocked" case.
- N2, after run 5 (or after run 4 if the clock is short): a forward install (C → 0 → I → A), then
  `sudo apt-get purge -y libjxl-tools`, then T1–T3. Want: a console answer and a backend-visible flag naming the missing
  cjxl (M13-3 b), **no** new image row (no pjpg) at the backend 15 min after T3, and `[INSTALL]`-free boot-log ERROR
  naming cjxl. Then R and Z, and bmcam004 gets libjxl-tools back in X.

**X. Restore bmcam004 (after the series; not before the NOAA update unless Nick says).**
- Packages as found (B): `sudo apt-get install -y --no-download /var/cache/apt/archives/{libjxl-tools,libgif7,libjpeg-turbo-progs,libtcmalloc-minimal4t64}_*.deb`
  (the cached libjxl-tools is deb13u2; the release's is deb13u3: if apt refuses the downgrade, keep deb13u3 and log it),
  then diff `dpkg-query` against `rehearsal_pkgs_asfound.txt`.
- Then Mon's rev 4 X: the B2 baseline tar, leftovers moved aside, crontab 6e7a4080; verify `software_sha.txt`
  613fb2c15037, `--print-config` hash d95aa41d, crontab sha256 6e7a4080.
- The bus stays held on. Putting it back on its schedule is a bridge config change: Nick's OK first.

## Not in this card
- **Must do 9** (a keyed in-window wake on a scheduled bus): it needs the held bus released, which is a bridge config change
  (rev 13.5 rules out bus-config trials here). It stands unless Nick drops it (rev 13); his (a)/(b) call (R12-2) on the
  go/no-go card. T5's "spotter UTC decoded" + media key is the nearest evidence this card gives.
- **The camera-cable hot-plug (rev 13.3 N4):** not rehearsed, so it's off NOAA's phone sheet (RUNBOOK P5).
- **A cold boot:** `sudo reboot` isn't a power cut (deviation above).

## Bars
- **5 clean runs in a row on `<REL_SHA>`.** A run is clean when every want line in S-check and C → R4 (and Z) matches on
  the first try, with no extra command, no workaround and no retry, and the stopwatch bars hold.
  - Any issue resets the count to 0: a retried step, a stop, a lost catch race, a want-line miss, a timeout or a manual
    fix. A step's own built-in wait (C2's and T2's `until` loops) and run 2's planned drop + re-attach are not retries.
  - A fix to the runbook, the installer, a tool or the profile means a new sha (a new tag `bmcam-rel-20261027.1` for the
    release, rev 13.3 N5), QE, Nick's new line, and the count restarts there.
  - "Ready for the real update" is said only after run 5 is clean, by TE 006, on the board card with RUNS.md linked.
- **Each run's ends:** the target = `[INSTALL] PASS` + I4's read-back (`<REL_SHA 12>`, fe87abab, overlay 0, supervisor,
  cjxl 0.11.x, FSF absent) + T3's ack and `tg=` START + T4's backend row and `CHECK PASS` with no `rfb=`; the rollback =
  `0d03a62cf565` + `117876ae0928…` + pc_old.txt + crontab re-armed + `DIRTY=0`.
- **N1 and N2:** all their wants. A miss is a release defect (back to the builder; a new tag), not a run issue.
- **Dropped session:** run 2's wants.
- n = 5 in a row. Times are reported per run (median and max), not from one run (§9).

## Stop rules
- A rollback fails (F4) → stop, leave bmcam004 disarmed, tell Nick + Lead 006.
- An installer `STOP` (exit 3) on bmcam004 → stop the series, tell the builder + Lead 006: it means the release can't read
  a known trixie arm64 unit.
- Power lost during a write step or < 60 s after one (F11) → an issue; RUNBOOK F11 note, then restore before the next run.
- Network lost mid-step → wait, or re-attach (I3). Never power-cycle during I3, `rc_field_update` or `deploy_rc_runtime`.
- The unit halts on the held bus (a lost race) → stop; a bus power-cycle is a bridge commit: Nick's OK first.
- Any write to bmcam002, SPOT-33361C, bmcam003 or SPOT-31593C → never. Any bridge config change → never in this card.
- Spotter battery < 3.80 V at R0 → no run; tell Nick on a NEEDED card.

## Outputs
- Run folder `~/NereusData/bm_cam_legacy/2026-10-27_rehearsal_release/`:
  - RUNS.md (one row per run: start, T0 → pass line and R minutes, per-step times, issue, clean count, F15 yes/no);
  - run<N>/: rehearsal.log (each command, output, `date`), sd/, release_logs/, trg/ (the `.nrjxl`, CHECK output, backend
    rows, console lines), S-check lines;
  - N1/ and N2/; the package lists (B, X);
  - the timing table (median and max) that fills RUNBOOK rev 2's time boxes.
- In git: this card, RUNBOOK rev 2's measured times, and RESULTS.md.
