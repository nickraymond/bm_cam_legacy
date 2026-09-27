# Sprint26 S3b bench gate — bmcam003 under stay_on (bmcam004 control): **PASS**

- **Date:** 2026-09-27 (UTC).
- **Code:** `feature/sprint26-s3b-stay-on` 71e3642 for the gate; 3e01284 at close (RSS ceiling + stills media key in the action log).
- **Authorized by Nick in chat:**
  - SPOT-33507C bus hold and restore;
  - 4 console `trg`;
  - about 800 cellular messages;
  - re-enabling the heal driver after the run.
- **Plan:** `PLAN.md`. **Console commands:** `console_commands.log`.

## 1. Steps (UTC)

| time | step | result | evidence |
|---|---|---|---|
| 05:17 | hold the bus on: `bridgePowerControllerEnabled 0`, commit, read-back 0 | ok | `console_commands.log` |
| 05:18 | `watcher_s3b.sh` caught bmcam003 at boot: disarmed, cycle stopped, backups taken | ok | `bmcam003_watcher.log` |
| 05:19 | deploy S3b 71e3642 (`rc_field_update.sh --leave-disarmed`) | **PASS**. Parity OK. Hash 5e679ef9 → 72a12186 (registry v3). | `bmcam003_deploy_s3b.log` |
| 05:20 | config: `mode.run: stay_on`, `interval_s 0`, `heartbeat_s 300`, `video.send.message_cap 40` (`set_stay_on.py`). Wrapper started by hand with the cron line. | Stay_on is up: 1 UART open, boot time read stepped the clock, idle | log `rc_cycle_20260927T052008Z.log` |
| 05:21 | `trg` 26310: clip failed at fit (x264 pass 2 rc=187; cap 40 is too low a bitrate for x264) | Loop survived (same PID). An action failure does not stop the loop. | action log |
| 05:21 | **SIGTERM stop** (`pkill -TERM` wrapper + runtime) | exit 0 within 1 s, no restart, no halt, marker cleared | log tail |
| 05:22 | cap 80, wrapper restarted | ok | |
| 05:22–05:38 | **4 triggered clips**: `trg` 26311–26314. `<WS a=idle>` heartbeat at 05:35 between them. | **4/4 complete over UART** (80+20, 79+20, 74+19, 76+19). Same PID 2433, 1 UART open, no reboot (uptime 19 min). | `status.sh` output, action log |
| 05:38 | **crash restart**: `kill -9` of the runtime | wrapper: exit 137 with the marker present → restart in 10 s, new PID, 1 UART open, boot time read | log |
| 05:39–06:19 | **RSS run**: stills, `still.message_cap 25`, `interval_s 120`, heartbeat off | **21/21 complete** (19–20 msgs each), 1 UART open, no reboot | `rss_actions.jsonl` |
| 06:20 | SIGTERM stop, restore `camera_config.yaml` (per_boot, video, supervisor), deploy 3e01284 | **PASS** (hash 72a12186) | `bmcam003_deploy_final.log` |
| 06:21–06:23 | halt, restore the controller (`1`, commit, read-back 1), catch the stub-window boot, restore the ARMED crontab, halt | armed, dark 06:23:34 | `rearm.log` |
| 06:24 | nereus000: `systemctl enable --now bm-heal-driver` (Nick). The deployed driver is newer than this branch's copy and was left in place. `state.json` backed up. | active + enabled, both rigs | §4 |

## 2. RSS (the H9 decision)

- **Current RSS** (VmRSS after each action): 32 MB for actions 1–6. One step to 78.6 MB at action 7. Then flat: +0.4 MB over the next 14 actions.
- **Encode peak** (ru_maxrss): 137 MB, transient.
- **Ceiling:** `RSS_CEILING_KB` = 150 MB (≈ 1.9× the plateau; the Pi has 415 MB). Commit 3e01284.
- A slow leak would show as growth after the step; none was seen in 14 actions.

## 3. Findings

- **F1 (bench config, not stay_on):** `video.send.message_cap 40` is below what x264 pass 2 can encode (rc=187 "Could not open encoder"). 80 works. The deploy/validator does not catch this; note for S4 `set` validation (a floor for `video.send.message_cap`).
- **F2 (Spotter, known):** clips 2–4 each lost 8 messages to queue-full (16/9/8 rejections on the console). These were back-to-back bursts hitting the known Notecard hand-off stall (S3a F2). The chunks are heal candidates (§4).
- **F3 (data loss, H10 as designed):** the first stay_on start pruned bmcam003's `cron_logs` from 455 to 200 `rc_cycle_*.log` files. The older per_boot history on the unit is gone (it was not in the backup tarball). The review flagged the prune as a NIT; prune only rotated stay_on logs? Nick to rule.
- **F4 (fixed):** the action log had `media_key: null` for stills. Fixed in ba4f8ec.
- **Heartbeat on hardware:** `<WS v=1 a=idle tz=America/Los_Angeles lt=2235 ws=0000 we=0000 rk=480x270 ct=31.1 sha=71e3642af0ff hn=bmcam003>`, 300 s after the last uplink.
- **Duplicates (S3a F1):** the mote forwards each console `bm pub` more than once. Dedupe absorbs it (`dup=1` on the first trg).

## 4. Heal path after the code changes

The heal driver was re-enabled at 06:24. Candidates at 06:25:
- **BMCAM_003:** the 3 queue-full clips (8 chunks each), 4 partial stills from the RSS run, and older per_boot clips.
- **BMCAM_004:** 3 older clips.

Result of the 07:00 wake: see §4a (filled in after the check).

## 5. State at close

- **bmcam003:** 3e01284, config v2 per_boot + `commands.runtime: supervisor` (hash 72a12186), ARMED, real halt, hourly.
- **SPOT-33507C:** controller on (read-back 1).
- **bmcam004:** development 1636c8b, supervisor per_boot (5e679ef9), armed hourly, untouched (control).
- **nereus000:** console monitor running; **bm-heal-driver ENABLED and running** (both rigs).
- **Backups on bmcam003:** `/home/pi/s3bbench/backup/` (S3a-code tarball, config, state, `crontab_ARMED.txt`); `/home/pi/s3bbench/camera_config.stay_on_rss.yaml` (the RSS-run config).
- **Rollback:** redeploy development 1636c8b. The config needs no change (a registry-v3 file without the S3b keys used here loads on v2 code).
