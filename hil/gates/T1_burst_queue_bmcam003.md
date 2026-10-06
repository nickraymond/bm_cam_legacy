# T1 — Spotter queue burst test on bmcam003 / SPOT-33507C (transmission epic, bm #129)

Question: what drives `MS_Q_CELLULAR_ONLY is full` rejects at the Pi → Spotter hand-off? X1 (2026-10-05 forensic,
`~/Downloads/x1_spotter_incident_20261005/X1_VERDICT.md`) located all loss there and predicts more rejects when the
burst overlaps the Spotter's report / health-check sync. C1 phase 1 (5+5 wakes on SPOT-31593C, `runs/c1_comms_20261003`)
found no significant :00 vs :30 effect, so this test pre-registers its bars before the first burst.
Owner: Test Engineer. GO: Nick via the EM, 2026-10-06 ~00:15Z (bus held on + restore, bench rigs only; never
SPOT-33361C). Slot: **Tue 10/6 16:00–19:00Z** (after bmcam004's sunrise run is restored: one Spotter on bus-on load
through nereus000's adapter at a time). Evidence: `runs/t1_burst_<date>/`. Times UTC.

## Method

- Bus held on (`hil_bus_always_on.sh SPOT-33507C`), cron disarmed, capture cycle stopped: the Pi is a pure sender.
- Sender: the Sprint09 UART tool (`sprints/Sprint09_mote_throughput/test_UART_throughput.py --phase tx`, real
  `bm_serial` COBS + spotter_tx path, cellular-only), staged in /tmp with the deployed `bm_serial.py`; a thin wrapper
  adds the burst-shape option and logs each send's UTC time. Payload: 120 messages × 384 B (a pjpg chunk's size) per
  burst; synthetic payloads (the backend ignores them; the queue does not care what is inside).
  Deviation from "pjpg bursts", stated: synthetic chunks give second-exact start times and the burst-shape arm with
  no production code change. Cellular quota: ≈ 18 × 120 messages.
- Read-out per burst, live from the nereus000 console capture: accepted (`Added message … to queue`) vs rejected
  (`MS_Q_CELLULAR_ONLY is full`), Notecard %, and the Spotter's sync / health-check lines with their times.
- Before the first burst: read SPOT-33507C's report minute (≈ :10 today) and health-check minute from the console.

## Arms (each hour 16, 17, 18Z; start times are pre-set; S = the Spotter's hourly sync start, ≈ :10)

| start | arm | shape |
|---|---|---|
| S − 30 s | **a-overlap**: burst runs through the sync | steady 1.3 s |
| sync end + 45 s | **b-settle**: #127's 45 s settle, emulated by timing (fix/sync-settle-126 is a 1-commit branch on development, but it is not deployed for this test, to keep the unit's code fixed) | steady 1.3 s |
| :25, :45 | **clear-steady** | steady 1.3 s |
| :35, :55 | **clear-pair** | 2 messages then a pause (2 × 0.1 s + 2.5 s ≈ the same average rate) |
n per arm over 3 h: a = 3, b = 3, clear-steady = 6, clear-pair = 6. The health-check minute, if it falls inside a clear
slot, moves that slot by 5 min (logged).

## Pre-registered bars (fixed before the first burst)

| claim | supported when | X1 prediction |
|---|---|---|
| T1.a overlap drives rejects | median(a-overlap) ≥ 28 AND median(clear-steady) ≤ 21 AND every a-overlap burst > the clear-steady median | overlapping 28–49, clear 8–21 |
| T1.b a 45 s settle avoids it | median(b-settle) ≤ 21 (inside the clear range) | clear-like |
| T1.c burst shape matters | median(clear-pair) ≤ 0.5 × median(clear-steady) | not predicted |
Anything else = "not supported" (no significance claimed at n = 3–6). Each arm reports median, min, max.

## Safety and restore

- VBAT watch (`hil_rig_health` bus_on_watch for SPOT-33507C): a sustained VBAT decline = STOP and restore.
- Restore: `hil/tools/hil_restore_schedule.sh SPOT-33507C` (1 / 3600000 / 600000), catch + halt the stub-window boot,
  re-arm the cron from the ARMED backup, `hil_bridge_phase.sh SPOT-33507C utc` only if the windows moved; the
  `hil-r1-cmdres` driver stays stopped unless the EM restarts it for the RC. Snapshot before/after.
- A permission prompt on any bus-on / restore console step = STOP and tell the EM.
