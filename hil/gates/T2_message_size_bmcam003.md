# T2 — message-size × pace sweep on bmcam003 / SPOT-33507C (transmission epic, bm #129)

Question (Nick, 2026-10-06 ~13:55 PDT, via the EM): which message size and pace deliver the most bytes per second
through the Pi → Spotter → Notecard path? Sweep BOTH directions around today's chunk (384 chars ≈ 392–401 B wire)
— "let's not assume we know what the global optimal message size is yet".
Owner: Test Engineer. GO: Nick via the EM 10/6 20:55Z (bus held on ~3 h, bench only, never SPOT-33361C; VBAT stop
rule). Evidence: `runs/t2_msgsize_20261006/`. Times UTC. **Bars and predictions fixed before the first burst.**

## Method

- Same rig and tools as T1 (`hil/gates/T1_burst_queue_bmcam003.md`): bus held on, cron disarmed, capture cycle
  stopped; sender `hil/tools/hil_t1_burst.py` (per-burst `size`, `gap_s`, `count` columns); synthetic payloads
  `TST,<run>,<seq>,<A–Z0–9 pad>*<crc8>` (non-ingestable, see T1); scorer `hil/tools/hil_t1_score.py`.
- **Sizes (payload bytes per message):** 96, 160, 224, 288, 384, 450, 600, 900, 1000. 1000 B is the ceiling
  (bm_core `spotter_tx_max_cellular_payload_bytes 1000`; Sprint09 B1: 1001 wire len accepted, 1100/1200 refused), so
  1200 is not run. 384 ≈ today's production chunk.
- **Same total bytes per arm:** ~35 kB → count = 35000 / size (96 B → 365 msgs … 1000 B → 35 msgs).
- **Paces:** sweep A = every size at today's 1.3 s; sweep F = sizes ≤ 384 B at 0.6 s; sweep S = sizes > 384 B at
  6.0 s (Sprint09's zero-loss point for 1000 B); sweep R = sweep A again in reverse order (n = 2 at 1.3 s).
  "Fastest tolerated pace" is bracketed by these two points per size class plus the measured drain time.
- **Slots:** bursts only in the clear part of each hour, never inside the Spotter's syncs (health check ~:02:30–:05:30,
  report :09:30–:11:30); 90 s between bursts. Deviation from "every burst 45 s after a sync": with 2 syncs/hour and 27
  bursts that is impossible; T1 showed clear slots (median 5 rejects, from the Spotter's own HDR message taking a
  queue slot) and post-sync slots (median 1) are both clean vs overlap (39). Plan: `t2_plan.csv` (27 bursts,
  21:30:00 → 23:46Z).
- **Measures per burst:** accepted (`Submitted … cell-only queue`) vs rejected (`MS_Q_CELLULAR_ONLY is full`) →
  accept %; drain per accepted message = Spotter time `Added message(id N)` → `Queuing message N` (to the Notecard),
  p50/p90; accepted bytes per second of burst (`acc × size / send span`); Notecard % first/max/last.

## Pre-registered predictions and bars (Sprint09 2026-07-27: payloads ≤ ~400 B take a fast drain path, larger ones
wait for a ~10.8 s batch cycle with 2 queue slots)

| id | claim | supported when |
|---|---|---|
| T2.1 | small messages drain fast | every size ≤ 384 B at 1.3 s: drain p50 < 1 s AND accept ≥ 93 % (T1 clear-steady 95.8 %) |
| T2.2 | a size cliff exists above ~400 B | every size ≥ 450 B at 1.3 s: accept ≤ 50 % OR drain p50 ≥ 5 s |
| T2.3 | big messages need slow pacing | every size ≥ 450 B at 6.0 s: accept ≥ 90 % |
| T2.4 | small-fast beats big-slow | the arm with the most accepted B/s among arms with accept ≥ 95 % has size ≤ 400 B |
| T2.5 | today's setting is not the optimum | some arm with accept ≥ 95 % beats 384 B @ 1.3 s on accepted B/s by ≥ 25 % |
Predicted best: 384 B @ 0.6 s (~640 B/s, Sprint09 400 B @ 625 ms zero loss) vs today 384 B @ 1.3 s (~295 B/s).
Anything else = "not supported"; n = 1 per arm at 0.6 / 6.0 s and n = 2 at 1.3 s (no significance claimed).

## Safety and restore

VBAT watch (bus_on_watch for SPOT-33507C); a sustained decline = STOP and restore. Restore: re-arm from the ARMED
backup, snapshot, halt, `hil_restore_schedule.sh SPOT-33507C` (1/3600000/600000), bus_on_watch off, restart
`hil-r1-cmdres.timer`. A permission prompt on a console step = STOP and tell the EM.
