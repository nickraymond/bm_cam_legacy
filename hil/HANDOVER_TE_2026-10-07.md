# Test Engineer handover — 2026-10-07 ~20:30Z (13:30 PDT)

Outgoing TE session: "Test Engineer: own the bench and run R1 gates" (context ~80 %). Check out branch
`feature/r1-hil-test-engineer` (PR #112 into development; sha in the PR / the EM's message) in a worktree of
`bm_cam_legacy`. Times UTC (PDT = UTC − 7). Report to the EM "Bristlemouth camera program EM handover" — one line per
step; Nick decides via the EM; permission prompts are never relayed.

## 0. Read first
- `hil/procedures/BENCH_GOTCHAS.md` (every failure of the last two days and the rules that follow).
- Detailed state: `runs/r2_delay_20261006/STATE.md` (R2 + bench), this file supersedes it for "what is running now".
- Gates: `hil/gates/R3_PACE.md` (active), `hil/gates/R2_DELAY.md` (done), `hil/gates/R2_L1.md` (staged), remote reset =
  EM card (below), `hil/gates/R1RC_release_candidate.md` (RC DELAYED by Nick; config still open).

## 1. Active work and the jobs that drive it (session crons die with the session — re-create them)

### R3-PACE on bmcam003 / SPOT-33507C (`hil/gates/R3_PACE.md`, `runs/r3_pace_20261007/`)
Pacing A = 1.0 s vs B = 1.5 s per message, delay 0, reef config, ABBA × 7 + AB (15 per arm), interim at 6/arm
(B median ≥ A median → STOP), decision at 15/arm (PASS = B median < A median AND one-sided Mann-Whitney p < 0.10).
B bursts crossing :05:00 are flagged "crossed HDR". State: `runs/r3_pace_20261007/schedule.json` (b_value 1.5).
Results so far: RESULTS.md (A 18.82 / 11.35 / 20Z pending; B 12.77 / 16.93).
- **Switch launcher** (every hour, twice for safety; the tool is idempotent): at :35 and :50 PDT launch in the
  background (timeout 1 h) `bash runs/r3_pace_20261007/scripts/r3_switch_at.sh NEXT_HH` (NEXT_HH = PDT hour + 8,
  mod 24). It waits for NEXT_HH:01:30Z (inside the wake, after its boot config read, before an A wake halts ~:05), reads
  the wake's `[RC] pacing … delay_s=` line, writes the next wake's `uplink.msg_interval_s` in the base YAML.
  **If the launcher fires late (busy session):** run `python3 hil/tools/hil_r3_switch.py --state
  runs/r3_pace_20261007/schedule.json --wake HH` by hand before HH:05Z.
- **Scoring** at :16 PDT each hour (wake HH = PDT hour + 7): `HIL_RUN_DIR=runs/r3_pace_20261007
  hil/tools/hil_wake_report.sh SPOT-33507C <date>THH:00` then `python3 hil/tools/hil_r2_score.py --console
  runs/r3_pace_20261007/console/wake_SPOT-33507C_<date>THH00.txt --arm A|B --out-json
  runs/r3_pace_20261007/analysis/wake_HH.json`; append a RESULTS row; one line to the EM (Pacific time).
- Restore after the card: pacing per the EM (pre-R2 = 1.3 s); R2 restore (§2); self_heal ON (Nick).

### REMOTE-RESET on SPOT-31593C / bmcam004 (EM card, Nick-approved 12:30 PDT; evidence `runs/remote_reset_20261007/`)
Q: does a cloud `reset` (Sofar Command API, sent by the EM) reboot the Spotter, and when? n = 1, then 2 more ≥ 1 h
apart. PASS = console `Remote message received … reset` → boot banner → uptime ≈ 0; log send → execute time.
STOP = no execution after 2 syncs (~2 h) → ask Sofar; any repeat reboot → --clear-queue + stop.
- Reset #1 sent by the EM 20:08:15Z (HTTP 202, clear-queue). Expected execution at the ~21:05–21:06Z sync.
- **Watcher**: `bash runs/remote_reset_20261007/watch_reset.sh <sinceZ> <deadlineZ> <tag>` (background; polls the
  nereus000 console every 20 s; saves `console_reset_<tag>.txt`; exit 0 SEEN / 2 NO EXECUTION). Running for #1 until
  21:20Z. Send the EM the confirmation lines + execute time, or "no execution" at 14:20 PDT.
- **bmcam004 safe mode** (installed 20:03:02Z): crontab = cycle commented (`# DISARMED_FOR_REMOTE_RESET …`) +
  `@reboot sleep 45 && sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh # REMOTE_RESET_SAFE_MODE` → every power-up
  halts the Pi ~75 s in. **ARMED backup: `~/hil_backup/20261007T200300Z/crontab_ARMED.txt`** (restore =
  `crontab` that file, after the last reset + the bridge restore).
- **Bridge restore (Nick typed OK in the TE chat 2026-10-07: "OK to restore SPOT-31593C bridge to utc after the
  reset")**: after the card's LAST reset: rehearse `HIL_PHASE_DRY=1 hil/tools/hil_bridge_phase.sh SPOT-31593C utc`,
  then the real run with the Pi halted, outside a window, safe-mode cron still in place; then restore the ARMED crontab.

### Other jobs
- **57914 → QC**: check `hil_media_table.py --devices BMCAM_004 --since 2026-10-06T19:55 --until 2026-10-06T20:05`
  on nereus000 every ~2 h; when 57914 is complete, send its id to "Run frontend QC for Nereus Vision web app [a53739]"
  (size line, Linear DNG, hq, stored sha vs `runs/s28_ladder_20261004/pulled/B1_containers/` 7f3e624c…c8ba).

## 2. Bench state

| unit | state | restore |
|---|---|---|
| bmcam003 / SPOT-33507C | development a50636e + R2 patch (single-file copy of `hil/r2-delay-prototype` 80c3662, sha de34a3f1…) with `r2_start_delay_s` = 0 (no-op); base YAML `uplink.msg_interval_s` = R3 arm value; production hourly bus (:00); cron ARMED; `hil-r1-cmdres.timer` STOPPED (nereus000) | `cp ~/hil_backup/r2_20261007T050052Z/rc_progressive_jpeg.py ~/hil_backup/r2_20261007T050052Z/camera_config.yaml /home/pi/BM_Devel_Pi/ && rm -f /home/pi/BM_Devel_Pi/r2_start_delay_s`; restart the cmdres timer only if the EM wants |
| bmcam004 / SPOT-31593C | runtime 104ee3c (B3a fix); local YAML `still: raw: layout: rgb` + `uplink: lane:` ON 3600/375/20/180; bridge ticks mode, windows ~:02:25–:02:33; **safe-mode crontab** (above) | `hil_b3a_layout.sh bmcam004 bayer4`; `hil_lane_block.sh bmcam004 off` (or the YAML backups `camera_config.yaml.bak_b1_*` / `.bak_lane_*`); bridge utc (Nick's OK, after the last reset); ARMED crontab back |
| backend | **self_heal OFF for SPOT-33507C since 05:15:04Z** (Nick; R2/R3 need it off) | Nick runs `PATCH /admin/gateways/SPOT-33507C/rollout {"self_heal": true}` after R3 |
| nereus000 | spotter-monitor, rig health (bus_on_watch off), dashboard :8095 | — |

## 3. Staged (approved, not installed)
R2-L1 forced heals: branch `hil/r2-l1-forced-heals` f393fb3 (stacked on 80c3662), module copy
`runs/r2_delay_20261006/scripts/rc_progressive_jpeg.r2l1.py`, gate `hil/gates/R2_L1.md` (PASS = hold applied 6/6
heal wakes AND median loss ≤ baseline B + 1 pp). Waits for Nick/the EM.

## 4. Rules and pre-approvals relied on
- Pre-approved (`.claude/settings.json`, Nick 2026-10-02): the HIL wrappers in `hil/tools/` and ssh to bmcam003,
  bmcam004, nereus000 (192.168.1.45). The wrappers refuse anything but the two bench rigs; **never SPOT-33361C / field
  units**. NOT pre-approved: staging pushes, Render env, merges, field units.
- Console (cmd.txt) writes and bridge changes need Nick's OK typed in the TE chat or relayed by the EM as Nick's GO;
  a permission prompt can never be satisfied by a relay. Cloud sends (Sofar Command API) happen in the EM chat.
- Admin/heal tokens stay on nereus000 (`~/.config/nereus/heal_driver.env`); never read or copy credentials; strip
  `*_url` and GPS fields from saved media JSON.
- Evidence: every step in the run folder's gate.log; never PASS on an exit code; commit as you go (`git add -f` for
  *.log / *.png / pulled files; never commit *.dng / *.pgm / *.jpg).

## 5. Session crons to re-create (exact)
1. `35 * * * *` and `50 * * * *` (PDT): R3 switch launcher (§1), until schedule.json says stopped/complete.
2. `16 * * * *` (PDT): R3 scoring (§1).
3. One-off per reset: the reset watcher (§1) right after the EM sends each reset.
4. Every ~2 h: 57914 → QC check (§1).
