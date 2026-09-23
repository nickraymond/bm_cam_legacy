# Kickoff — outdoor 15-minute test analysis (for a NEW session)

Written 2026-09-23 02:40Z by the Sprint25 session. Owner: Nick. Question to answer: **how did the
outdoor run differ from the indoor runs, and what would reduce dropped messages?**

## REQUIRED READING, in order
1. `CLAUDE.md`; skill `nereus-spotter-sd-analysis` §10 (SD `log/` files, parse by pattern).
2. `sprints/Sprint25_transmit_timing_resend/RESULTS.md` — parts 1 and 1b: the method, the numbers
   to compare against, and the mechanism (Spotter→Notecard hand-off stalls of 4–13 s cost one
   chunk each; ≤ 3 s are free; hourly report = post-boot report rounded up to the next 5-min
   boundary; health check at boot-minute + 1 can queue a LEGACY alert; resets/power cuts erase
   unsynced clips; NOTE-WBNA-500 syncs every ~30 min).
3. `tools/bm_video_soak_report.py` (docstring: `--sd-log`, `--sofar-json`, columns, `loss_stage`,
   SVG cut sheet) and the two run folders `runs/sprint25_sd_20260922T1640Z/` (overnight indoor,
   20-min cycles) and `runs/sprint25_indoor15_20260922T1840Z/` (indoor 15-min cycles) — reproduce
   their `report_*/` tables first to learn the tool.
4. The scratch scripts the session used (recreate them; they are not in git): a Sofar dumper that
   fetches `sensor-data` in 4 h slices and writes JSON to the session scratchpad (raw hex is bench
   footage — never commit it), and the stall analysis: hand-off spacing from MS.log `Sending
   Cellular message to Notecard` lines vs `Queue MS_Q_CELLULAR_ONLY is full`.

## THE RUN
- Started 2026-09-22 ~22:35Z when Nick moved the ebox outdoors; cameras SWAPPED: rig A =
  bmcam003 (`0x53171fa3d81a8e6f`, BMCAM_003) on SPOT-33507C (NOTE-WBGLW fw 6.2.5, self-resets
  ~hourly after boot, "Reboot Controller"); rig B = bmcam004 (`0xe6fe83ea6b4a2b7f`, BMCAM_004) on
  SPOT-31593C (NOTE-WBNA-500 fw 4.2.1). Both bridges: `sampleIntervalMs 840000`, duration 600 s,
  `alignmentInterval5Min 1` → 15-min period; windows re-anchored at the 22:20Z SD-insertion resets
  (bus on at the next 5-min boundary, then every 15 min); post-boot reports rounded to **:25** so
  the hourly report minute is :25:00 on both (verify on the SD). Health check at boot-minute + 1.
  Camera settings unchanged: 1 msg/s, keyframe repeat ≤ 30, cap 126, `transmit_phase` off.
- No consoles outdoors: the SD cards are the only Spotter-side record. Nick pulls them and puts
  the zips in `~/Downloads` (ask for the names). Cards were inserted at 22:20Z → log index 4
  (SPOT-31593C) and 7 (SPOT-33507C); the outdoor sets are `0004_*` and `0007_*` (+ later indexes if
  a Spotter reset). Unzip into `runs/sprint25_outdoor_<ts>/<SPOT>/` (card contents git-ignored:
  add `runs/sprint25_outdoor_*/SPOT-*/` to `.gitignore`), keep only `report_*/` md/svg/json.
- As of 02:31Z: 16 clips per rig at Sofar, 9/16 (B) and 5/16 (A) complete, every partial short by
  1–3 chunks, no cut cycles, no resets yet.
- Restore after the test (Nick or you via the USB console, `bridge cfg set <bridge> s u
  sampleIntervalMs 960000` + `bridge cfg commit <bridge> s`; bridges: SPOT-31593C
  `0e582dd12c1e1480`, SPOT-33507C `c3c564b91856226c`). A commit forces the bus on 120 s and cuts
  one camera cycle. The console monitor `spotter_serial_monitor.py --only … ` PID 26536 under
  `caffeinate` may still be alive on the Mac and re-grabs the ports when consoles are plugged in —
  `pgrep -fl spotter_serial_monitor`; never open a port twice.

## WHAT TO PRODUCE
1. Per-rig `report_*/cycles.csv|md`, `loss_by_stage.md`, `timeline_*.svg` for the outdoor window.
2. One comparison table: indoor overnight (20-min) vs indoor 15-min vs outdoor 15-min, per rig:
   clips, complete %, chunks lost per clip, rejections per clip, stall counts by length (≥ 4 s),
   report/health-check collisions, HDR collisions, clips lost to resets, Notecard drain delay.
3. Answer: what changed outdoors (GPS steady → fewer health-check alerts? stall rate different?
   cellular signal effect on the hand-off stall length? temperature?) with evidence per claim.
4. A ranked list of what would reduce dropped messages, each with the measured effect it targets:
   e.g. the resend design (already approved, `SPEC_resend_heal.md` v3), pacing 1.0 → 1.2 s/msg
   (converts some 4 s stalls into free ones; burst +186 s, still 57 s clear of the boundary),
   Sofar-side queue depth (ask Sofar), the no-power-cut rule, the bus-grid re-commit after resets.
   One variable per proposed test; do not change the running rigs without Nick.

## STANDING RULES
- Cellular only; no `note sync`; no commits or reboots while the run is on; `log flush` before a
  card pull; format cards from the Spotter; never fsck a card.
- Credentials by name only: `SOFAR_API_TOKEN_BM_REEF`, `STAGING_DATABASE_URL`, `NEREUS_ADMIN_TOKEN`.
- Report to Nick: What happened / What I learned / What's next, two bullets each, tables.
