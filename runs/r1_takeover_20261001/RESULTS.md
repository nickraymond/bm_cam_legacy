# TAKEOVER — RESULTS (`runs/r1_takeover_20261001`)

**TAKEOVER: PASS (T1–T7, both units).** bmcam003 and bmcam004 now run development 7d47ff9
(#97 + #98), with the RemoveIPC=no host fix; the render-wipe root cause is confirmed on the units;
P0 is done.

Spec: `hil/procedures/TAKEOVER.md`. Timeline: `gate.log`. Times PDT (UTC in the logs). Bench handed
over by the S6b HIL session at 05:30Z = 22:30 PDT Thu 10/1.

## Setup

| unit | Spotter | before | after | config hash | bus | cron |
|---|---|---|---|---|---|---|
| bmcam003 | SPOT-33507C | 3c1801d, stay_on, up since 10/1 05:22Z | **7d47ff956c4b**, stay_on | f7c9194f → f7c9194f | HELD (unchanged) | armed → armed (== backup) |
| bmcam004 | SPOT-31593C | 3c1801d, stay_on, up since 10/2 01:27Z | **7d47ff956c4b**, stay_on | f7c9194f → f7c9194f | HELD (unchanged) | armed → armed (== backup) |

Other sessions on the hardware: none. BM_HEAL_AUTOSEND stayed live on staging (backend heals may
have hit a unit while it was down for ~2 min / ~12 min: the backend re-asks).

## Criteria

| id | criterion | measured | verdict | evidence |
|---|---|---|---|---|
| T1 | root cause confirmed before any change | both: supervisor running; `bmcam_stay_on` / `_sched` markers MISSING; RemoveIPC default (`#RemoveIPC=yes`); Linger=no; `settings re-resolve failed` 284 (003) / 38 (004). **Reproduced:** `/dev/shm/bmcam` (recreated 05:31:4x by a snapshot) was gone at 05:32:20 after that ssh session (pi's only one) closed, on both units | PASS (confirmed) | `snapshots/*_takeover_*`, `gate.log` 05:32 |
| T2 | new runtime deployed | `software_sha` 7d47ff956c4b == origin/development; fix97 yes, rule98 yes; rc_field_update SUMMARY PASS, print-config + v2 parity OK | PASS ×2 | `snapshots/*_after_deploy_*`, `pulled/*_field_update.log` |
| T3 | config untouched | hash f7c9194f before/after; `camera_config.yaml` sha256 c8a9c0c989ab6e7e unchanged; journal +1 line = the deploy record (`src: deploy`, by design) | PASS ×2 | snapshots |
| T4 | host fix in place | `systemd-analyze cat-config`: line 59 `RemoveIPC=no` from `90-bmcam-removeipc.conf`; `logind.conf` byte-identical to its backup | PASS ×2 | `gate.log` |
| T5 | render survives ssh logout | after reboot, 2 rounds each: render + `bmcam_stay_on` present, 0 errors; journal proves pi's user manager fully stopped between rounds (003 05:36:23 / 05:36:48; 004 05:52:38 / 05:53:15) | PASS ×2 | `snapshots/*_logout{1,2}_*`, `gate.log` |
| T6 | unit back in service | `<WS v=1 a=idle cfg=f7c9194f up=301 … sha=7d47ff956c4b hn=bmcam003>` 05:40:05Z; `… up=302 … hn=bmcam004>` 05:57:08Z; cron == backup | PASS ×2 | `console/*_after_reboot.txt` |
| T7 | P0 outputs complete | 77 probes on bmcam004, all files; summary in the folder README | PASS | `runs/s27_ladder_20261001/p0_rpicam_limits/` |

## Steps (UTC)

| unit | backup + disarm | stop | deploy | host fix | reboot | stay_on up | WS |
|---|---|---|---|---|---|---|---|
| bmcam003 | 05:32:51 | 05:32:58 → 05:33:16 (exit 0) | 05:33:30–05:34:10 | 05:34:2x | 05:34:30 | 05:35:06 | 05:40:05 |
| bmcam004 | 05:40:34 | 05:40:36 (exit 0) | 05:46:5x–05:47:3x | 05:47:4x | 05:51:xx (after P0) | 05:52:08 | 05:57:08 |

Backups on each unit: `/home/pi/hil_backup/<TS>/` (003: 20261002T053251Z, 004: 20261002T054034Z) +
runtime tars `/home/pi/backups/BM_Devel_Pi_before_rc_deploy_<host>_20261002T05*.tgz`.

## Findings

| id | seen | effect | owner | link |
|---|---|---|---|---|
| F1 | `hil_unit_snapshot.sh` ran `--print-config` before checking /dev/shm; that recreates `/dev/shm/bmcam`, so the first snapshot read "render present" on a wiped unit | false evidence; caught by the timestamps | Test Engineer | fixed 56796fc |
| F2 | the stop wait-loop matched its own shell when combined with the `sed` disarm in one ssh call (004: waited 6 min; the runtime had exited at once) | lost time only | Test Engineer | TAKEOVER.md gotcha |
| F3 | P0: rpicam accepts out-of-range image-processing floats (exit 0) and refuses duplicate options (exit 255) | range checks must be backend + check_value; duplicates = IP6 trigger | Sprint27 session | P0 README |
| F4 | bmcam004 CmaFree 74 MB of 256 MB with no camera process (before P0) | none seen (P0 ran fine); watch if a still at full res fails | — | `gate.log` |

## Restore

Nothing to restore: the units are left in the G3 state the handoff asked for (stay_on, bus held,
cron armed), on the new runtime. Undo per unit: TAKEOVER.md §8.

## Not tested

- A remote `set` taking effect in stay_on with #97 (that's G3 L2+).
- New units do not get the RemoveIPC drop-in yet (provisioning follow-up from #97).
