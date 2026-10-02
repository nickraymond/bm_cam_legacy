# Bench takeover after G1: deploy, #97 host fix, P0 probe

Owner: Test Engineer. Runs once, right after the S6b HIL session's handoff message (expected
~00:30 PDT Fri 10/2). Evidence: `runs/r1_takeover_<YYYYMMDD>/` (`hil/tools/hil_new_run.sh
r1_takeover TAKEOVER bmcam003 bmcam004`). One unit at a time: **bmcam003 first**, and
bmcam004 starts only after bmcam003 has passed its §5 verification.

Sources: `tools/rc_field_update.sh` and the bmcam-field-update skill (deploy),
`runs/render_dir_vanish_20261001/README.md` (host fix runbook, approved by Nick 2026-10-01),
`sprints/Sprint27_remote_config/LADDER.md` PART 1 (P0).

## Criteria

| id | criterion | PASS when | evidence |
|---|---|---|---|
| T1 | root cause of the render wipe confirmed (or not) before any change | runbook step 1 table filled per unit: supervisor running, `/dev/shm/bmcam` present?, RemoveIPC, Linger, error count | `snapshots/<host>_takeover_*.txt` |
| T2 | new runtime deployed | `software_sha.txt` == the origin/development sha recorded at start; `KEY fix97 yes`, `KEY rule98 yes`; rc_field_update SUMMARY PASS | `snapshots/<host>_after_*.txt`, `pulled/<host>_field_update.log` |
| T3 | config untouched by the deploy | `cfg_hash` after == before; `camera_config.yaml` sha256 after == before | snapshots |
| T4 | host fix in place | `systemd-analyze cat-config` shows `RemoveIPC=no` from `90-bmcam-removeipc.conf`; `logind.conf` byte-identical to its backup | `gate.log` |
| T5 | render survives an ssh logout | after a reboot: `/dev/shm/bmcam/camera_schedule.yaml` and `/dev/shm/bmcam_stay_on` present after closing every session + 15 s wait, twice; `settings re-resolve failed` count 0 | snapshots `<host>_logout1/2_*` |
| T6 | unit back in service | cron armed (== the takeover backup), stay_on running, a `<WS>` (or heartbeat) seen on the console after the reboot | `console/<host>_after_reboot.txt` |
| T7 | P0 outputs complete | `p0_rpicam_limits/` has 00_env, 01_help ×2, 02_picamera2_controls.json, probes.csv (> 40 rows), SUMMARY.md | `runs/s27_ladder_<date>/p0_rpicam_limits/` |

Any FAIL on T2/T3/T6 → roll that unit back (§7) before touching the next one.

## 0. Before anything

```bash
source hil/hil.env; RUN=$(hil/tools/hil_new_run.sh r1_takeover TAKEOVER bmcam003 bmcam004); export HIL_RUN_DIR=$RUN
git fetch origin && git rev-parse origin/development | tee -a $RUN/gate.log    # the sha T2 checks
ssh $HIL_MONITOR 'systemctl is-active spotter-monitor; systemctl is-active bm-heal-driver; systemctl is-active bm-bench-conductor' # want: active, inactive, inactive
```

Read the handoff message for each unit's state (stay_on or per_boot, bus held or scheduled, cron).
**This procedure assumes stay_on + bus held + cron armed** (the G1 setup). If a unit is per_boot on
a scheduled bus, hold the bus first (bench-gotchas memory) or use the bmcam-field-update skill's
catch-it-awake path instead.

## 1. Confirm the root cause (read-only; runbook step 1) — T1

```bash
hil/tools/hil_unit_snapshot.sh bmcam003 takeover
```

Copy the `KEY procs / shm_render / removeipc / linger / reresolve_fail` lines into RESULTS.md T1.
Confirmed = supervisor running AND `shm_render missing` AND RemoveIPC unset/yes AND Linger=no AND
`reresolve_fail` > 0. Note: this snapshot's own ssh logout can wipe the render again (that is the bug).

## 2. Back up and stop (per unit, H=bmcam003)

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ); echo "$TS backup+stop $H" >> $RUN/gate.log
ssh pi@$H "set -u; B=/home/pi/hil_backup/$TS; A=/home/pi/BM_Devel_Pi; mkdir -p \$B
crontab -l > \$B/crontab_ARMED.txt
for f in camera_config.yaml camera_schedule.yaml bm_command_state_v2.json config_journal.jsonl software_sha.txt; do [ -f \$A/\$f ] && cp -p \$A/\$f \$B/; done
sudo cp -p /etc/systemd/logind.conf \$B/logind.conf
crontab -l | sed 's|^@reboot \(.*rc_run_capture_cycle\.sh\)\$|# DISABLED hil $TS: @reboot \1|' | crontab -
crontab -l | grep -n reboot; ls -la \$B" < /dev/null | tee -a $RUN/gate.log
```

Stop stay_on (it finishes an in-flight burst first, up to ~5 min; never a halt):

```bash
ssh pi@$H "pkill -TERM -f '[r]c_progressive_jp[e]g.py'; for i in \$(seq 1 72); do pgrep -f '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle' >/dev/null || break; sleep 5; done; pgrep -af '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle|[r]picam|[f]fmpeg' || echo STOPPED" < /dev/null | tee -a $RUN/gate.log
```

Only if still running after 6 min: `pkill -KILL -f '[r]c_progressive_jp[e]g|[r]c_run_capture_cycle'`
(costs the in-flight clip, swept at the next boot).

Restore command (write it in gate.log now): `crontab /home/pi/hil_backup/$TS/crontab_ARMED.txt && sudo reboot`.

## 3. Deploy origin/development — T2, T3

```bash
hil/tools/hil_unit_snapshot.sh $H before_deploy
scp tools/rc_field_update.sh pi@$H:/tmp/
ssh pi@$H "bash /tmp/rc_field_update.sh --repo /home/pi/repos/bm_cam_legacy --ref development \
  --profile $H/live_20260925 --leave-disarmed 2>&1 | tee /home/pi/hil_backup/$TS/field_update.log" < /dev/null
scp pi@$H:/home/pi/hil_backup/$TS/field_update.log $RUN/pulled/${H}_field_update.log
```

`--leave-disarmed`: its own crontab backup was taken after step 2 disarmed; re-arm from step 2's
backup. A print-config parity diff stops the deploy: accept it (`--accept-print-config-diff`) ONLY
if every line of the diff is explained by #97/#98 (record the diff + the reason in gate.log).
On a config-v2 unit stage 4 patches nothing (expected).

## 4. Host fix (runbook steps 2–3) — T4

```bash
ssh pi@$H 'sudo mkdir -p /etc/systemd/logind.conf.d && \
  printf "[Login]\nRemoveIPC=no\n" | sudo tee /etc/systemd/logind.conf.d/90-bmcam-removeipc.conf && \
  systemd-analyze cat-config systemd/logind.conf | grep -n RemoveIPC; \
  sudo cmp /etc/systemd/logind.conf /home/pi/hil_backup/'$TS'/logind.conf && echo LOGIND_CONF_UNCHANGED' < /dev/null | tee -a $RUN/gate.log
```

Do not restart systemd-logind; the reboot in step 6 loads it.

## 5. P0 probe — T7 (bmcam004 only, while its runtime is stopped)

The camera is idle and cron disarmed here, which is P0's precondition, so P0 costs no extra
stop/restart. bmcam003 skips this step.

```bash
P0=runs/s27_ladder_$(TZ=America/Los_Angeles date +%Y%m%d)/p0_rpicam_limits
hil/tools/hil_p0_probe.sh bmcam004 $P0
```

Then one line to the EM, and the folder path to the Sprint27 session.

## 6. Re-arm and reboot — T6

```bash
ssh pi@$H "crontab /home/pi/hil_backup/$TS/crontab_ARMED.txt && crontab -l | grep reboot && sudo reboot" < /dev/null
```

The bus is held, so the Pi boots → cron `@reboot` → the runtime reads `mode.run` from the overlay →
stay_on. Wait for the console:

```bash
ssh $HIL_MONITOR "tail -n 400 /home/pi/spotter_logs/<SPOT>/console_\$(date -u +%Y%m%d).log | grep -E '\[$H\]|<WS' | tail -5" > $RUN/console/${H}_after_reboot.txt
```

## 7. Verify (runbook step 4) — T2, T3, T5

```bash
hil/tools/hil_unit_snapshot.sh $H after          # sha, fix97, rule98, cfg_hash, shm_render, removeipc
ssh pi@$H loginctl list-sessions                  # yours must be the only one
# close every session, wait >= 15 s, then:
sleep 20; hil/tools/hil_unit_snapshot.sh $H logout1
sleep 20; hil/tools/hil_unit_snapshot.sh $H logout2
```

T5 PASS: `KEY shm_render present` and `/dev/shm/bmcam_stay_on` listed in both logout snapshots,
`KEY reresolve_fail 0`. Record step 1 + step 4 output on bm #97 (comment).

## 8. Rollback (per unit, any T2/T3/T6 FAIL)

| what | command |
|---|---|
| runtime | `tar xzf /home/pi/backups/BM_Devel_Pi_before_rc_deploy_<host>_<TS>.tgz -C /home/pi` (path printed by the deploy) |
| config | `cp -p /home/pi/hil_backup/<TS>/{camera_config.yaml,bm_command_state_v2.json,config_journal.jsonl} /home/pi/BM_Devel_Pi/` |
| host fix | `sudo rm -f /etc/systemd/logind.conf.d/90-bmcam-removeipc.conf` |
| cron + start | `crontab /home/pi/hil_backup/<TS>/crontab_ARMED.txt && sudo reboot` |

Then snapshot `rolled_back` and confirm `software_sha` == the before snapshot.

## Gotchas

- `ssh '… nohup … &'` needs `< /dev/null`; `cd …; nohup …` (not `&&`) or ssh never returns.
- pgrep/pkill: bracket patterns, and call the script by a glob (`rc_progressive_jp[e]g.py`). Keep the
  stop loop in its OWN ssh call: in the same command as step 2's `sed`, `[r]c_run_capture_cycle`
  matches the sed text and the wait loop idles its full 6 min on itself (bmcam004, 2026-10-02).
- `hil_unit_snapshot.sh` before 56796fc faked `shm_render present` (`--print-config` recreated the dir).
- A Pi reboot clears `/tmp`: re-stage `rc_field_update.sh` if you retry after a reboot.
- bmcam004 lost bus power 3× on 2026-09-30 (cause unknown): if it goes dark mid-step, check the
  Spotter console (`hil_console.sh SPOT-31593C 'bridge cfg get 0e582dd12c1e1480 s bridgePowerControllerEnabled'`)
  before assuming the deploy broke it.
