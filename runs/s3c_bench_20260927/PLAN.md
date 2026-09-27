# S3c bench gate — bmcam003 stay_on × save_local, SD bounded (2026-09-27)

Spec: `sprints/Sprint26_remote_command_loop/PLAN_S3c.md` §2 + §5 "Revised gate". Code:
`feature/sprint26-s3c-save-local` cabb79f (pushed). Nick: "You own both rigs and cameras"
(2026-09-27): bus hold, deploys and arming are this session's call; everything is recorded here.

Control: bmcam004 = development 9491f1f (S3b), supervisor per_boot, armed hourly (step 0 above).

## Steps (commands from this directory)

| # | when | what | command |
|---|---|---|---|
| 0 | 20:34Z | heal driver stopped (state.json → state.json.s3cbench) | `./gate.sh heal stop` |
| 1 | 21:00Z window | catch bmcam003, disarm, back up to /home/pi/s3cbench/backup, SD baseline | `watcher_s3c.sh bmcam003` |
| 2 | < 21:10Z | hold SPOT-33507C's bus on (set 0, commit, read-back) | `console.sh SPOT-33507C "bridge cfg …"` |
| 3 | | deploy the S3c branch, unit left disarmed | `./gate.sh deploy` |
| 4 | | history out of images/ + videos/ (same SD, guards cannot touch it) | `./gate.sh hist-out` |
| 5 | | video half config: stay_on, save_local, interval 60, heartbeat 300, `video.storage.max_used_pct` = used % + ~10 clips, `min_free_gb` 1 | `./gate.sh set …` |
| 6 | | start sampler + wrapper (cron line) | `./gate.sh start` |
| 7 | +30 min | stop; still half: `mode.media still`, limit = used % + ~10 stills | `./gate.sh stop; ./gate.sh set …; ./gate.sh start` |
| 8 | still half | one `trg 2` over the console: it SAVES (C8), no media on the wire | `console.sh SPOT-33507C "bm pub bmcam/cmd {…} 1 1"` |
| 9 | +30 min | stop; per_boot save_local video wake, halt dry-run: `<WS a=saved>` (W11), tail, duration | `./gate.sh set mode.run=per_boot …; ./gate.sh once` |
| 10 | | restore: config, history back, bench media kept in /home/pi/s3cbench/bench_media | `./gate.sh restore-cfg; ./gate.sh hist-back` |
| 11 | | Pi halted disarmed → bus schedule back (set 1, commit, read-back) → re-arm in the stub window | `console.sh …; rearm_s3c.sh` |
| 12 | | heal driver back (state.json compared) | `./gate.sh heal start` |
| 13 | | pull artifacts, plot, RESULTS.md, run_manifest.json | `./gate.sh pull; plot_sampler.py` |

## PASS

- Both halves ran as one stay_on process each, no reboot, one port OPEN (log), no action error
  except an intended `storage_full`.
- SD used % ≤ the configured cap + one action's bytes on every 15 s sample after the cap was
  first reached (`pulled/sampler.csv`, `sd_usage.png`); media counts plateau.
- No START/chunk on the console for the whole gate; heartbeats every 300 s through the 60 s
  actions (C1); the trg 2 action saved (C8).
- per_boot wake: `<WS a=saved>` on the console, the listen tail, halt dry-run.
- History restored byte-for-byte by file list; bmcam003 re-armed; bus schedule read back 1;
  heal driver active.
