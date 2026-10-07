# Test Engineer STATE — READ FIRST (handover-ready; updated 2026-10-07 ~15:30Z / 08:30 PDT)

**UPDATE 15:30Z: R2-DELAY STOPPED by Nick (verdict in RESULTS: inconclusive, n=4–5/arm). ACTIVE CARD NOW = R3-PACE**
(`hil/gates/R3_PACE.md`, `runs/r3_pace_20261007/`): pacing A 1.0 s vs B 1.3 s, delay 0 (r2_start_delay_s=0, patch
no-op), ABBA from 16Z, switch `runs/r3_pace_20261007/scripts/r3_switch_at.sh HH` at HH:01:30Z (`hil/tools/hil_r3_switch.py`,
edits base YAML `uplink.msg_interval_s`), score with `hil/tools/hil_r2_score.py`; interim at 6/arm, decision at 12/arm
(~24 h). Everything else below (bmcam003 restore, self_heal OFF + restore, bmcam004 as is, R2-L1 staged) still holds.


Worktree `bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c`, branch `feature/r1-hil-test-engineer`. Times UTC
(PDT = UTC − 7). EM = "Bristlemouth camera program EM handover" (local_7c78dab7…): one line per step, Nick via the EM.
Rules that bit tonight: `hil/procedures/BENCH_GOTCHAS.md` (bounded ssh everywhere, bracket pkill patterns, never edit a
running script, rehearse with the real arguments). Session crons die with the session: re-create from §5.

## 1. Active card: R2-DELAY on bmcam003 / SPOT-33507C — `hil/gates/R2_DELAY.md` (rules + EM amendments)
Q: does holding the first uplink send until Pi uptime ≥ 230 s change first-send loss vs no hold (reef config)?
Arms A = 0 / B = 230, ABBA; EM 10/7: rules 1–3 don't cover "A holes from a Notecard stall, B holes from the :05 HDR"
→ **keep ABBA running until Nick picks the next delay value (~07 PDT); no stop, no R2-L1, delay unchanged.**
Evidence: `runs/r2_delay_20261006/` — RESULTS.md (table + notes), gate.log, analysis/wake_HH.json, console/, schedule.json.
So far: 07Z A 12.75 % (Notecard hand-off stall 07:01:45, not an HDR); 08Z A (extra) 0 %; 09Z B (applied) 7.27 %
(burst moved onto the 09:05:01 HDR); 10Z = B (switch written). SPOT-33507C adds a 6129 B HDR every 5 min (:x4:59/:x9:59).

### How it runs
- **Patch** (bench only): branch `hil/r2-delay-prototype` 80c3662 (= development a50636e + 32 lines in
  `rc_progressive_jpeg.py`): before the first uplink send, sleep until /proc/uptime ≥ `<app>/r2_start_delay_s`; budget
  check like the C2 lane (skipped=True → "B not applied", EM rule: excluded, extra B follows, 2 in a row → STOP); logs
  `[R2DELAY] start_delay_s=… uptime=… wait=… burst_est=… heal_msgs=… budget_left=… skipped=…`. Installed on bmcam003 as a
  single-file copy (sha256 de34a3f1…; copy at `runs/r2_delay_20261006/scripts/rc_progressive_jpeg.r2.py`).
- **Unit config** (local base YAML): uplink.msg_interval_s 1.0 (reef); everything else as deployed (pjpg, crop →1000 px,
  cap 195, 384 chars, cellular-only, lane off, per_boot). Overlay: mode.media still (cid 1000159).
- **Arm switch**: `hil/tools/hil_r2_switch.py` (state `schedule.json`: seq ABBA×3, pointer, counts, history with the
  [R2DELAY] line per wake) run by `runs/r2_delay_20261006/scripts/r2_switch_at.sh HH` at HH:01:30Z (inside the wake,
  after the decision at uptime ~35 s, before an A wake halts ~:05) — launched in the background at :35 the hour before.
  Off-sequence wakes count for their arm without advancing the sequence.
- **Scorer**: `hil/tools/hil_r2_score.py --console …/wake_SPOT-33507C_<ts>.txt --arm A|B --out-json …` (loss on the
  wake's key from the Spotter console, gaps, queue_full, hand-off stalls >1 s, HDR marks crossed, rejects HDR vs other).
  Console excerpt per wake: `HIL_RUN_DIR=runs/r2_delay_20261006 hil/tools/hil_wake_report.sh SPOT-33507C <date>THH:00`.
- **Backend**: self_heal **OFF** for SPOT-33507C since 05:15:04Z (Nick). Restore after the card (Nick runs it):
  `PATCH /admin/gateways/SPOT-33507C/rollout {"self_heal": true}`. `hil-r1-cmdres.timer` **stopped** (nereus000); no
  commands are being sent.
- **Restore bmcam003** (after the card, in a window, bounded ssh): `cp ~/hil_backup/r2_20261007T050052Z/rc_progressive_jpeg.py
  ~/hil_backup/r2_20261007T050052Z/camera_config.yaml /home/pi/BM_Devel_Pi/ && rm -f /home/pi/BM_Devel_Pi/r2_start_delay_s`
  (pacing back to 1.3 s); restart the cmdres timer only if the EM wants it.

## 2. Next card (approved, NOT installed): R2-L1 — `hil/gates/R2_L1.md`
Forced heals (`r2_force_skip_n` = 40, branch `hil/r2-l1-forced-heals` f393fb3 stacked on 80c3662; module copy
`runs/r2_delay_20261006/scripts/rc_progressive_jpeg.r2l1.py`). PASS = hold applied 6/6 heal wakes AND L1 median loss
(non-forced indices) ≤ baseline-B median + 1 pp; STOP = 2 budget skips. Needs self_heal ON (EM flips). Waits for Nick.

## 3. bmcam004 / SPOT-31593C — leave AS IS (EM: no bridge/lane changes until told)
Runtime 104ee3c (B3a fix); local YAML `still: raw: layout: rgb` (backup …bak_b1_20261006T180142Z) + `uplink: lane:` ON
3600/375/20/180 (backup …bak_lane_20261007T0000*Z); cron armed; bridge **ticks mode, windows ~:02:25–:02:33** (commit
02:02:22Z 10/7, early by a leading-zero bug); one hard power cut 02:04:25Z (health check after: clean). Lane findings:
`runs/s28_ladder_20261004/RESULTS.md` (LANE-W1..3: the :05 sync blocks the queue ≥ ~60 s; the lane wait is dropped by
the budget check on wakes with heals). Restore when told: `hil_b3a_layout.sh bmcam004 bayer4`, `hil_lane_block.sh bmcam004
off` (or the YAML backups), `hil_bridge_phase.sh SPOT-31593C utc` (bridge reset → needs the EM's OK; rehearse with
HIL_PHASE_DRY=1 first).

## 4. Open items
- B1 2nd sample 57914 (BMCAM_004, 178/187): send to frontend QC [a53739] when complete (57901 = QC PASS).
- RC (R1RC) deploy: DELAYED by Nick; config still open (exposure auto vs low-gain mismatch with the gate doc).
- Message-size result T2: 288 B @ 0.6 s best (needs repeats); T1: overlap drives rejects, 45 s settle clean.
- Tools added tonight (all in hil/tools): hil_deploy_window.sh, hil_b0_b3a_run.sh, hil_b3a_layout.sh, hil_lane_block.sh,
  hil_t1_burst.py/hil_t1_score.py, hil_sync_profile.py, hil_r2_score.py, hil_r2_switch.py; hil_bridge_phase.sh (DRY mode,
  bounded calls, int MM fix).

## 5. Session crons to re-create (session-only)
- `:35` PDT each hour 01–10: launch `r2_switch_at.sh NEXT_HH` (NEXT_HH = PDT hour + 8) in the background, timeout 1 h.
- `:16` PDT each hour 03–11: score the HH = PDT hour + 7 wake (wake report + scorer + RESULTS row + one line to the EM).
- 57914 → QC watch (19:50/21:50/23:50 PDT rhythm or as the EM likes).
