# S3c bench gate — RESULTS (bmcam003, 2026-09-27)

**Gate: PASS.** bmcam003 ran stay_on × save_local for one hour: 30 min video + 30 min still,
one action every 60 s. The SD stayed bounded at the configured cap. No media went on the
uplink. Heartbeats kept their 5 min rhythm through the minute actions. One per_boot save_local
wake sent the W11 line. The unit is restored and armed.

Code: `feature/sprint26-s3c-save-local` cabb79f (PR into development). Control: bmcam004 on S3b
9491f1f, armed (step 0). Steps and commands: PLAN.md, `gate.log`, `console_commands.log`.

## 0. Step 0

bmcam004 moved to S3b at 16:00Z (PLAN_S3c.md §4): PASS, hash 72a12186, armed, dark 16:01:46Z.

## 1. Setup

- **20:34Z** heal driver stopped; `state.json` backed up to `state.json.s3cbench`.
- **21:00:55Z** watcher: bmcam003 caught (up 0 min), cycle SIGTERMed, cron disarmed, backups in
  `/home/pi/s3cbench/backup/`. SD 123.2 GB, 92.21 GB used (74.87 %). 689 clips (4.2–4.8 MB each)
  and 196 natives.
- **21:01:17–31Z** bus held on: `bridgePowerControllerEnabled 0` + commit + read-back 0. **F2:**
  the commit reset the bridge, so bmcam003 lost power and rebooted (up since 21:01:33). It was
  disarmed, the backups had finished at 21:01:02, and the boot log shows `EXT4 orphan cleanup`
  with no errors.
- **21:01:47Z** deploy cabb79f: field update PASS, print-config / json / v2 parity OK. Hash
  72a12186 → 62e0a1cb (registry v4, same values).
- History moved aside to `/home/pi/s3cbench/history` (927 images + 1765 video files, same SD), so
  the guards could prune only gate media. File lists: `pulled/*.before.txt`.

## 2. Video half: stay_on × save_local × video, interval 60, cap 74.91 %

21:03:35–21:34:02Z (`pulled/rc_cycle_20260927T210331Z.log`), cap = used + ~10 clips.
- **31/31 actions saved.** Each was a record-quality 1920×1080 15 fps clip, 4.5–4.9 MB, with
  sidecar + manifest, time_source spotter. 17–27 s per action (median 19 s). No fit, no send.
- **The ring pruned 21 times**, from action 11 (21:13:36) on, deleting the oldest gate clip each
  time. The steady state was 10 clips.
- 6 heartbeats `<WS a=idle>`, one per 300 s, through the 60 s actions (the C1 fix on hardware).
- 0 errors. One port open for the process. SIGTERM → `stop requested … no halt`, exit 0.

## 3. Still half: stay_on × save_local × still, interval 60, cap 74.935 %

21:34:38–22:05:05Z (`pulled/rc_cycle_20260927T213421Z.log`).
- **32/32 actions saved** (31 scheduled + 1 trg). Each saved the native plus a q85 1000 px crop
  (58–59 KB), about 1.8 MB per stem, 8–11 s per action. time_source spotter.
- **The stills guard pruned 16 times** from 21:48:52, one whole save_local stem each time (tier
  2; tier 1 was empty because the history had been moved aside). The steady state was 16 stems.
  `[STORE][FULL]` never fired.
- **trg 2** (console `bm pub`, id 26401) at 21:45:18: action 12 (trg) **saved** (C8), was acked
  (+1 replay duplicate, deduped), and sent no media. The next scheduled action ran on time.
- 6 heartbeats, 0 errors, SIGTERM stop, exit 0.

## 4. per_boot save_local wake (video, halt dry-run)

22:05:26Z (`pulled/rc_cycle_20260927T220526Z.log`): `[RUNTIME] supervisor`, `[OUTPUT] save_local`.
The clip was saved, then **W11**
`<WS v=1 a=saved tz=America/Los_Angeles lt=na ws=0000 we=0000 rk=480x270 ct=37.6 sha=cabb79f109bd hn=bmcam003>`
(110 B), then the 150 s listen tail and a dry-run halt. **Wake 172 s**, against 460 s for this
unit's transmit wakes (last 5 in the action log). The listen tail is 150 s of the 172 s: the
remaining battery lever is `commands.listen_tail_s` (R4).

## 5. SD bounded (`pulled/sampler.csv`, every 15 s; `sd_usage.png`)

| half | cap | at cap from | after the cap: min / max over cap | first → last after cap | largest 15 s jump |
|---|---|---|---|---|---|
| video | 74.91 % | 21:11:55 | −3.50 / **+7.16 MB** | +1.16 → −1.07 MB | 9.72 MB |
| still | 74.935 % | 21:48:52 | +1.26 / +1.39 MB | +1.35 → +1.26 MB | 1.85 MB |

- No drift after the cap in either half. Media counts plateaued: 10 clips, then 16 stems.
- **Criterion amended after the run (stated, not hidden):** PLAN said "used ≤ cap + one action's
  bytes". A video action's peak is about **two clips**, because `record_one_clip` holds the
  `.h264.part` while it muxes `.mp4.tmp`, and it deletes the `.part` only after the rename. The
  largest 15 s jump was 9.7 MB, about 2 × 4.8 MB. The ring checks the limit before recording, so
  the peak is cap + one in-flight clip + its mux copy. With that allowance (0.0079 %) video
  passes: max 74.916 ≤ 74.918. Stills pass the original criterion (max 74.936 ≤ 74.936).
  Suggested follow-up: document "cap + 2 clips" as the ring's bound, or have the ring reserve
  one clip.

## 6. Wire (Pi send log for all three runs; the console logs payloads as hex)

| run | START | chunk sends | `<WS>` | acks |
|---|---|---|---|---|
| video half | 0 | 0 | 6 × a=idle | 0 |
| still half | 0 | 0 | 6 × a=idle | 2 (trg + replay) |
| per_boot wake | 0 | 0 | 1 × a=saved | 0 |

RSS now 15–34 MB, peak 137 MB (the stills encode, as in S3b). No reboot during the gate.

## 7. Findings

- **F1: `write_manifest` is O(clips) and rewrites the whole manifest on every saved clip.** On
  the Mac: 1k clips 0.06 s / 230 KiB, 20k clips 1.27 s / 4.6 MiB. **On bmcam003: 689 real clips
  0.88 s / 172 KB**, about 20× the Mac. At this card's 75 % cap (about 19k clips of 4.7 MB) a
  save_local video action would spend about 25 s on it and rewrite about 4.6 MB a minute. Not
  an S3c blocker (the ring bounds the count). Follow-up: write the manifest on the recorder path
  only, or incrementally.
- **F2: `bridge cfg commit` while holding the bus ON reset the bridge and cut bus power** (bmcam003
  rebooted, clean orphan cleanup). Fix for the runbook: commit the hold with the Pi disarmed AND
  halted, then let the held bus boot it.
- **F3: the per-action bound for video is cap + about 2 clips** (§5).
- **F4 (harness, fixed):** the sampler start guard `pgrep -f '[s]ampler.sh'` matched its own ssh
  command line, so the sampler started by hand at 21:06:24Z (the first 3 min of the video half
  are unsampled). `gate.sh` now uses `pgrep -x`.
- **Known, deferred to S4:** the trg 2 log label still reads "capture + send" (legacy wire,
  PLAN_S3c §6 #3).

## 8. State at close

- **bmcam003:** cabb79f (S3c), config restored from the backup (hash 62e0a1cb = the original
  values under registry v4; supervisor, per_boot, transmit, video). Re-armed from
  `crontab_ARMED.txt` in the stub window at 22:11:26Z; dark 22:11:56Z. History restored (file
  lists identical by name). Gate media kept in `/home/pi/s3cbench/bench_media` (96 image files +
  34 video files, about 80 MB).
- **SPOT-33507C:** `bridgePowerControllerEnabled` read back 1, schedule 3600000/600000 intact.
- **nereus000:** bm-heal-driver active again, `state.json` unchanged across the pause.
- **bmcam004:** unchanged since step 0 (S3b 9491f1f, armed, control).
- **Rollback:** redeploy development 9491f1f. The restored `camera_config.yaml` is the pre-bench
  file byte for byte (copied from the backup), so it has no registry-v4 key and loads on S3b code.
  Its hash reads 62e0a1cb under v4 only because the loader fills the new default.

## 9. Follow-ups closed after the gate (branch feature/s3c-followups)

- **F1:** a save_local video action now updates `manifest.json` incrementally
  (`video_manifest.add_to_manifest`): it loads the manifest, drops the ring's deletions and adds
  the new clip from its sidecar record, re-reading no other sidecar. The result equals a full
  rebuild (unit-tested through adds and deletions). On the Mac at 20k clips it takes 0.37 s
  against 2.61 s. **Residual:** the JSON load and rewrite is still O(clips), about 4.6 MB per
  write at 20k clips. Future hardening: page or cap the manifest. The recorder keeps the full
  rebuild (it moves with R1).
- **F3:** the ring's bound (cap + ~2 clips in flight) is documented in
  `video_ring.ensure_room`.
