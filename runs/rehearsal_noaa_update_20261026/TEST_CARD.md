# Test card: NOAA on-land update rehearsal on bmcam004 (Spotter bus held on), then must do 9 on its Spotter bus (Mon 10/26 → Tue 10/27 AM)

**Rev 4 (TE 006, Mon 10/26 ~3 PM): power and physical steps.** Nick, in the TE 006 chat at 2:04 PM (verbatim): "bmcam004 is
plugged into a spotter, you need to turn on the configuration for always on power". So, from run 1 on this sha:
- bmcam004 runs on SPOT-33507C's bus, held on (`hil/tools/hil_bus_always_on.sh SPOT-33507C`, bridge c3c564b91856226c,
  bridgePowerControllerEnabled 0), not on a USB supply. Logged deviation by Nick's choice.
- Who does the physical steps (SPEC_e4 rev 12.6 N2): **nobody, per run.** TE 006 does every power-cycle from the console
  (a bridge commit power-cycles the bus) and every ssh step. Network: bench Wi-Fi (Nick, 12:00 PM). Nick has no per-run step.
- Step W1 changes: bmcam004 is already on its bus, so W1 is TE 006 restoring the bus schedule
  (`hil/tools/hil_restore_schedule.sh SPOT-33507C`) before the Tue ~7 AM wake, not a physical move.
- Retry rule (rev 12.6 N1): a step's own built-in wait (C2's `until ssh` loop) is part of that step. A second power-cycle,
  a re-run or a manual fix is a retry and resets the count.
- The USB wording below is rev 3's and is overridden by these lines.

**Rev 3 (TE 006, Mon 10/26 ~11:20 AM): n = 5.** Nick's acceptance bar, typed in Lead 004's chat before 10:57 AM (PE 009 read
it there; SPEC_e4 rev 12.5 must do 4), verbatim: "NOAA - prove to me that you can update the cameras with the latest software, run through the practice 5 times
without issue before telling me you are ready for a real world update tomorrow. That is the primary goal."
- TE 006 didn't read that line at its source (TEAM_RULES §0); it cites the spec. The bar is stricter than rev 2, and
  Nick's OK of this card at its sha confirms it.
- So: **5 clean runs in a row** of deploy + one rollback. Any issue resets the count to 0 (Bars). Nobody says "ready"
  before run 5 passes.

Rev 2 closed rev 1's stop points (S1–S5 table below) and added must do 9 + the restore. Rev 1 = TE5's 0cf70e9a (branch
feature/r1-hil-test-engineer). This card runs only after QE's PASS on this card's sha and Nick's own line in the
**TE 006 chat** citing that sha (TEAM_RULES §6; bm_cam_legacy CLAUDE.md wins on hardware).

- **Spec:** SPEC_e4_noaa_release rev 12.6 (sha256 ab7fd78b68de; rev 3 cited 12.5, 1629658f5f2a): must dos 3, 4, 9; R12-1, R12-2; rev 12.1 N1–N3; F1–F9.
- **Runbook under test:** `runs/noaa_update_20261027/RUNBOOK.md` at this card's sha. Profile:
  `device_profiles/bmcam002/noaa_20261027/camera_config.yaml` (sha256 412bcafe00cd…, hash fe87abab).
- **Units:** bmcam004 + SPOT-33507C only (TE 006 owns them; TE5 handed over 10:11 AM). bmcam002, SPOT-33361C and bmcam003
  are not touched.

## Question
1. **Must do 4, × 5 (Nick's bar):** does the runbook take bmcam004 from bmcam002's exact start state to the target and
   back (one rollback), verified by sha + config hash at each end, in ≤ 60 min, over a phone hotspot? It must do this
   **5 times in a row with no issue**.
2. **Must do 9:** does bmcam004 on the target build + profile, back on its Spotter bus, make a keyed in-window image with
   the shipped zone and clock path?

## Stop points from rev 1: how each is closed
| | Rev 1 open item | Rev 2 |
|---|---|---|
| S1 | Nick's S3/S2 call | **Closed:** "GO: S3 = development 613fb2c1 + NOAA profile." (Nick, TE5 chat, Mon 10:00 AM; c_rc_soak_s3 `decided`) |
| S2 | bmcam002's v2 target profile + hash | **Closed:** the profile file above, hash **fe87abab**. Built from d95aa41d (spec rev 12.2); `--print-config` on 613fb2c1 on the Mac (desk files below). This run re-checks it on the Pi (step U/M). |
| S3 | How to put bmcam004 on 0d03a62 + the shipped v1 | **Closed:** step S below. 0d03a62's own `deploy_rc_runtime.sh` (manifest copy, no parity gate) + the shipped YAML, after the v2 files are moved aside. |
| S4 | bmcam004 on a USB supply | **Open until checked:** Nick's move ~10:45 AM Mon (Lead 004's plan, c_noaa_update_plan_1027). Step B0 checks it; if the unit isn't on USB, stop. |
| S5 | Capture-only command with no Spotter | **Closed (to confirm here):** runbook K1: `rc_progressive_jpeg.py --skip-time-window` without `--transmit`, with `NO_HALT` (rc_power_halt.py NO_HALT_FLAG) for that one run. |

Desk evidence (Mac, no hardware): `~/NereusData/bm_cam_legacy/2026-10-26_rehearsal_noaa_update/desk/` (MANIFEST.md):
- expected print-config diff 0d03a62 → 613fb2c1 on the shipped YAML = text only (pc_diff.txt);
- migration dry-run of the shipped YAML = exit 0, no v2 state (migrate_dry.txt);
- target hash fe87abab, overlay 0 (print_config_target_613fb2c1.txt).

## Conditions
- bmcam004: Pi on a USB supply (no Spotter, no BM bus) for steps B–R2, then back on SPOT-33507C's bus for step W.
- **Network:** Nick's phone hotspot, with the SSID + password bmcam004 already knows. Which network that is: **unknown**;
  Nick names it in his OK line. If he picks bench Wi-Fi instead, that is a logged deviation by his choice.
- **Who does the physical steps:**
  - Nick plays the NOAA role: USB power-cycles and the hotspot.
  - TE 006 types the runbook's SSH commands (C–R) on the bench unit, unless Nick says in his OK that he types run 1.
    Five runs is ~4–5 h.
  - **The power-cycle in step C:**
    - Run 1: Nick unplugs and replugs the USB supply, if he is at the bench. Otherwise run 1 uses `sudo reboot`, like
      runs 2–5.
    - Runs 2–5: TE 006 sends `sudo reboot` over ssh from the armed start state. The unit then boots, cron starts a fresh
      `@reboot` cycle and C2 has to win the same race.
    - Logged deviation: a reboot isn't a power cut, so it doesn't test a cold boot or the hotspot rejoin after power loss.
      Nick may ask for a USB cycle on every run instead (he must be at the bench).
  - If C2 loses the race, the unit halts. Rev 4: a bridge commit (TE 006) brings it back, and that run counts as an issue.
  - **Nick moves bmcam004 back to the Spotter bus (step W1).** It is a physical change, so TE 006 announces it on the board
    first (§10, rev 12.1 N1).
- Timing:
  - after QE's PASS (~4 PM) and Nick's OK (~6 PM Mon); steps B–R2 Mon evening;
  - must do 9 needs the first in-window wake, 07:00–12:00 PT. Monday's window is over by then, so that is **Tue 10/27,
    the ~7 AM wake**.

## Steps
**B. Baseline (bmcam004's own restore point; not timed)**
- B0: bmcam004 answers on Tailscale/ssh (rev 4: on SPOT-33507C's held-on bus).
  - Its cron is armed, so it runs one cycle per power-on and halts. Catch it with `tools/noaa_catch_disarm.sh` (runbook C2,
    `pi@bmcam004`).
  - Want: DISARMED, `software_sha.txt` = 613fb2c15037.
- B1: `cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config | grep hash=`. Want: **d95aa41d**. Record the overlay
  key count as found: the soak's logs show 13 (`steps/bmcam004_b08.txt:9`), and the pre-soak backup shows 12.
- B2: `tar czf /home/pi/backups/rehearsal_baseline_bmcam004_<TS>.tgz --exclude images --exclude cron_logs --exclude videos --exclude buffer -C /home/pi BM_Devel_Pi && sha256sum` it.
  - Copy it to the run folder.
  - Also record the armed crontab's sha256. It is 6e7a4080 + the TE5_RC_SAMPLER line (handoff); the B0 backup has it.

**S. Put bmcam004 in bmcam002's start state (not timed)**
- S1: `cd /home/pi/BM_Devel_Pi && for f in camera_config.yaml bm_command_state_v2.json bm_command_state.json camera_config.lkg.json; do [ -e $f ] && mv $f $f.off_rehearsal; done`
- S2: `git -C /home/pi/repos/bm_cam_legacy fetch origin && git -C /home/pi/repos/bm_cam_legacy checkout --detach 0d03a62cf565`
  - The checkout path is from the B0 survey. If it differs, use that path and log it.
- S3: `bash /home/pi/repos/bm_cam_legacy/tools/deploy_rc_runtime.sh --repo /home/pi/repos/bm_cam_legacy --dst /home/pi/BM_Devel_Pi`
  (0d03a62's script: manifest files only, crontab untouched).
- S4: scp the shipped YAML from Mon's export (sha256 117876ae0928…) to `/tmp/`, then `cp /tmp/bmcam002_camera_schedule.yaml /home/pi/BM_Devel_Pi/camera_schedule.yaml`.
- S5: verify:
  - `software_sha.txt` = `0d03a62cf565`;
  - `sha256sum camera_schedule.yaml` = 117876ae0928…;
  - `--print-config` = desk `pc_old.txt` (modulo paths).
  - Files that 613fb2c1 added and 0d03a62's manifest doesn't list stay in the dir. List them (`git ls-tree` diff) as a
    logged deviation: 0d03a62 never imports them, and the forward update overwrites the runtime.
- S6: re-arm with `crontab /home/pi/rc_soak_mem/crontab_before_sampler.txt`. bmcam002 is armed, so the start state is too.
  This file is the pre-soak armed crontab (sha256 6e7a4080), so the TE5_RC_SAMPLER line is dropped here and stays dropped.
  Then `sudo halt`.

**Runbook runs 1–5 (each timed; T0 = DISARMED in step C; stopwatch per runbook step into that run's time-box row)**
- Each run's output goes to `run<N>/` in the run folder, e.g. `rehearsal.log`, `sd/` and `noaa_check/`.
- After each run: a row in `RUNS.md` (run, start, T0 → R4 minutes, per-step times, issue: none / what), and the board card
  gets "clean runs in a row: k/5".
- C: the power-cycle (Conditions), hotspot up; runbook C1–C2 on `bmcam004`.
- 0 → D → U → M → K → A: the runbook as written, `bmcam002` → `bmcam004` in host names.
  - U4's tar name, M4's hash, K3's output and the A pass line go in the log.
- R: one rollback, runbook R1–R4.
  - Want: `0d03a62cf565`, `117876ae0928…`, print-config = pc_old.txt, crontab re-armed as found.
- **The stopwatch stops at R4.** Must do 4's bar: T0 → R4 ≤ 60 min.
- Z, the reset before the next run (not timed). R4 leaves the start state (0d03a62 + shipped YAML, armed), except for:
  - the repo checkout, which U3 moved to 613fb2c1;
  - the K1 output.
  - So: `git -C /home/pi/repos/bm_cam_legacy checkout --detach 0d03a62cf565 && rm -rf /home/pi/noaa_check`.
  - Then check S5's wants again (sha, YAML sha256, the step-0 "absent" list) and the crontab (armed).
  - Any difference is an issue: fix it and reset the count.
  - Then the next run starts at C.
- After run 5 passes: W.

**W. Forward again + must do 9 on the Spotter bus**
- W0: catch it again (C2; R4 re-armed it). Then the runbook's U3–U5, M1–M4 and A1 again (the second forward also proves
  re-forward after a rollback). Want: sha 613fb2c15037, hash fe87abab. Then `sudo halt`.
- W1: TE 006 posts the move on the board, then Nick moves bmcam004 off USB and back onto SPOT-33507C's bus. The Spotter
  stays on its production schedule: hourly, 600 s window, not held (restore_schedule.out :12, :25).
- W2: off-window wakes Mon night (America/New_York 10:00–15:00 = 07:00–12:00 PT) must each log a `skip_win` and halt.
  - TE 006 reads the first one's cycle log over ssh at the wake (the pull_logs2.sh pattern).
  - Want "spotter UTC decoded" (F9 early warning) and 0 `[CFG][ERR]`.
- W3, **must do 9 (rev 10 bars), the first in-window wake Tue ~07:00–07:10 PT.** All of:
  - boot log with 0 `[CFG][ERR]`;
  - `<WS>` with `tz=America/New_York` and cfg `fe87abab` (backend config-history / Sofar);
  - "spotter UTC decoded" + `[KEY] media key` in the cycle log;
  - an image captured (1600×900 nrjxl, 0 rfb) and no `skip_win` at that in-window hour.
  - The wake minute is **unknown**. TE 006 reads bmcam004's last_seen minute from the backend Mon evening (rev 12.1 N3).
- W4: Nick's (a)/(b) choice, stated up front (R12-2):
  - (a) hold the 9:30 AM update until W3 passes. REC (a): the 7 AM wake should be decided by ~7:40 AM, given Sofar's
    11–30 min lag.
  - (b) go without must do 9; recorded on the go/no-go card as "must do 9 not run by Nick's choice".

**X. Restore bmcam004 (after W3; not before Tue's update unless Nick says)**
- Catch it at a wake: its bus is on ~600 s and ssh is up ~35–57 s after bus-on. Use C2.
- Then:
  - `tar xzf` the B2 baseline;
  - move `camera_config.yaml.migrated_*` and any `*.off_rehearsal` aside into `/home/pi/rehearsal_leftovers_<TS>/`;
  - crontab = `/home/pi/rc_soak_mem/crontab_before_sampler.txt` (sha256 6e7a4080, TE5 handoff: the soak is over, so
    the sampler line goes).
- Verify:
  - `software_sha.txt` 613fb2c15037;
  - `--print-config` hash d95aa41d;
  - crontab sha256 6e7a4080;
  - the next production wake's `<WS>` at the backend shows d95aa41d.

## Bars
- **Nick's bar: 5 clean runs in a row.**
  - A run is clean when every want line in C → R4 matches on the first try, with no extra command, no workaround and no
    retry, and T0 → R4 ≤ 60 min.
  - Any issue resets the count to 0: a retried step, a stop, a lost catch race, a want-line miss, a timeout or a manual
    fix. The issue is logged with its cause, and the next run starts from Z. A step's own built-in wait (C2's `until ssh`
    loop) is not a retry (rev 12.6 N1).
  - A fix to the runbook, a tool or the profile means a new PR sha and QE, and the count restarts on that sha. Nick's OK
    covers this card's sha; a new sha needs his new line.
  - "Ready for the real update" is said only after run 5 is clean, by TE 006, on the board card with RUNS.md linked.
- **Must do 4 (each run):** T0 → R4 ≤ 60 min with one rollback. Each end is verified:
  - target: sha 613fb2c15037 + `--print-config` fe87abab + runtime supervisor (runbook M5 + K1's `[RUNTIME]` line) +
    FORCE_SHRINK_FALLBACK absent + capture check PASS;
  - rollback: sha 0d03a62cf565 + yaml 117876ae0928… + pc_old.txt.
- The print-config diff in U4 equals the desk diff. M1 exits 0.
- **Must do 9:** all of W3. Any `spotter UTC` timeout = F9 = must do 9 fails.
  - F9's bench reset path (rev 12.1 N2): **none confirmed.** nereus000's console has been out since Fri; a Mac USB console
    to SPOT-33507C is untested; a Spotter power-cycle is physical (Nick).
  - So F9 here → must do 9 fails, and Nick picks (a)/(b) or a physical Spotter reset + the next wake.
- **Restore:** all X checks.
- A step that fails twice, or 60 min passing → rollback (R) and a recorded must do 4 FAIL.
- n = 5 clean runs in a row (Nick's bar). Run times are reported per run (median and max), not from one run (§9).

## Stop rules
- A rollback fails (F4) → stop, leave bmcam004 disarmed and halted, tell Nick + Lead 004.
- USB power or network lost mid-step → wait. Never power-cycle during `rc_field_update` or `deploy_rc_runtime`. Resume
  from the last verified step.
- Any write to bmcam002, SPOT-33361C, bmcam003 or SPOT-31593C → never.
- Spotter battery (Sofar latest-data) < 3.80 V on SPOT-33507C before W1 → don't move to the bus. Tell Nick; must do 9 is
  his (a)/(b). Spotters were not charging Mon AM (TE5 addendum: 3.87 V, −0.3 W).

## Outputs
- Run folder `~/NereusData/bm_cam_legacy/2026-10-26_rehearsal_noaa_update/`:
  - RUNS.md (one row per run: times, issue, clean count);
  - run<N>/rehearsal.log (each command, output, `date`), run<N>/sd/, run<N>/noaa_check/;
  - B2 tar + sha256;
  - the U4 rollback lines;
  - SD listing;
  - migration output;
  - --print-config before/after;
  - noaa_check/;
  - the timing table (per run; median and max);
  - W2/W3 cycle logs + backend GETs;
  - restore checks.
- In git: this card + RESULTS.md. The runbook's time boxes are updated from the timing table.
