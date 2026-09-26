# Sprint26 S2 bench gate — bmcam003 (runbook, awaiting Nick's OK)

Gate (DESIGN_supervisor.md §8.3 S2): on bmcam003, dry-run, real migration,
rehearsed rollback (the old SHA reads the untouched v1 files). bmcam004 follows
after 24 h, when the mote session releases it.

Off-device evidence already in the branch (feature/sprint26-s2-settings):
- every golden re-run through migrate → v2 → render: wire byte-identical, settings
  equal per key (tests/test_config_v2_parity.py), incl. bmcam003's LIVE config;
- `tests/test_deploy_rc_runtime.py::test_bench_rehearsal_over_bmcam003s_runtime`:
  this exact sequence on a71b6c7 + the live YAML, off-device: PASS.

## Needs from Nick (in chat)
1. OK to push `feature/sprint26-s2-settings` (the unit deploys from a ref).
2. OK to hold SPOT-33507C's bus on for the session (`bridgePowerControllerEnabled 0`
   + commit via nereus000's console), restored to `1` at the end (S1 recipe).
3. nereus000 is shared: the mote session ("Bristlemouth camera message buffering")
   is messaged before any console command.

## Steps (UTC; each logged in this folder)
0. Survey + catch awake: `watcher.sh` (S1's, with the `[r]c_` pkill fix) at the
   next :00 window: disarm cron, stop the boot cycle before it can halt; backups:
   code tarball, camera_schedule.yaml, bm_command_state.json, crontab (ARMED),
   bm_media_key_last.txt, repo branch/HEAD → `/home/pi/s2bench/backup/`.
1. Hold the bus on (step 2 above), read back.
2. Deploy S2 (unit still on v1 files):
   `rc_field_update.sh --ref feature/sprint26-s2-settings --profile bmcam003/live_20260925 --leave-disarmed`
   Expect: print-config parity OK (a71b6c7 vs S2), no stage-4 patch needed
   (profile == unit values).
3. Service key: `deploy_rc_runtime.sh --create-service-key`; copy to
   `~/.config/nereus/unit_keys/bmcam003.key` on the Mac (mode 600, never in git).
4. Migration DRY-RUN on the unit (its own current state file):
   `python3 tools/config_migrate_v1_v2.py --app /home/pi/BM_Devel_Pi` → report saved.
5. Real migration: `--write`. Then `deploy_rc_runtime.sh` again from the same
   checkout (cron is still disarmed): v2 strict load + v1-vs-v2 parity must pass
   on the unit. `--print-config` shows `[CFG] config v2 … hash=`.
6. Live cycles over the BM bus, as in S1: bench COPIES in `/home/pi/s2bench/cfg_*`
   (the unit's migrated camera_schedule.yaml + camera_config.yaml + a copy of its
   v2 state; in the copy only: power.halt.dry_run true, commands.state_path → the
   copy, mode.media still|video). `rc_progressive_jpeg.py --transmit
   --skip-time-window --config-path <copy>/camera_schedule.yaml` picks the copy's
   v2 file. One video + one stills cycle; compare START/END/chunks, boot-to-transmit
   and peak RSS with the S1 run. The live state/config files are never used.
7. Rollback rehearsal: redeploy a71b6c7 (`rc_field_update.sh --ref a71b6c7 …`
   — its old deploy script); `--print-config` must equal the step-0 capture
   byte-for-byte (untouched v1 files); v2 files present but ignored.
8. Forward again: redeploy S2 → parity passes → `[CFG] config v2` level=v2.
9. Decide end state with Nick: leave bmcam003 on S2 + v2 (the gate's purpose,
   bmcam004 stays the control) — or restore a71b6c7. Either way: armed crontab
   restored inside the stub window after the Spotter restore
   (`bridgePowerControllerEnabled 1`, S1 `rearm_watcher.sh`), verify halted.

## Restore (whatever happens)
- runtime: `tar xzf /home/pi/s2bench/backup/BM_Devel_Pi_code_before_s2bench.tgz -C /home/pi`
- v1 config/state: untouched by design; `cmp` against the backups.
- v2 files: `mv camera_config.yaml camera_config.yaml.off_<TS>` (only read if present).
- crontab: `crontab /home/pi/s2bench/backup/crontab_ARMED.txt` (inside the stub window).
- Spotter: `bridge cfg set c3c564b91856226c s u bridgePowerControllerEnabled 1` + commit + read-back.
