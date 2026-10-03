# C1 — comms reliability sprint (bmcam004 / SPOT-31593C)

Owner: Test Engineer (sole bench owner). Rig: **bmcam004 / SPOT-31593C only** (Nick 2026-10-03 via the EM; bmcam003 is
the repair rig, `R1F_cmd_resilience.md`). Code: R1 (development 34a6222) unless a phase says otherwise.
Evidence: `runs/c1_comms_<YYYYMMDD>/`. Tools: `hil_bridge_phase.sh`, `hil_wake_report.sh`, `hil_media_table.py`,
`hil_cmd_ledger.py`, `hil_rig_health.py` (heat/power guard runs throughout).

## Fixed config for every arm (one variable at a time)

production bus (1 h window, 10 min), per_boot + real halt, `mode.media` **video** fixed (no alternation),
`video.send.message_cap` 126, pacing 1.3 s/msg, `uplink.chunk_chars` 384 (phase 1), heal auto-send on (cap
24/day), **no trg, no other commands** during an arm. Console observe-only.

## Phase 1 (now): stable, repeatable baseline incl. the :15 vs :00 A/B

### Why :15
The Spotter's hourly LEGACY report (+ hub.sync, cellular queue busy ~40–50 s) and its boot-anchored health check
land inside today's :01–:06 burst on SPOT-31593C (report :05:00, health check ~:03, read 2026-10-03). A window
opening at :15 keeps the burst (≈ :16–:21) clear of both. The 5-min grid blackout (HDR at each :x0/:x5) still
applies in both arms.

### How (bridge firmware fact, bm_protocol `bridgePowerController.cpp`)
No offset key: UTC hourly windows always open at :00 (seeded at now − now % interval; `alignmentInterval5Min`
only rounds up). The only route is `ticksSamplingEnabled 1` (uptime timebase) + a commit timed at hh:15 − lead
(`hil/tools/hil_bridge_phase.sh SPOT-31593C ticks 15`); the first :15 window is one hour after the commit. Any
bridge reset (Spotter reboot / rebootctl / power cycle) re-phases it → detected on the console → re-run the tool.
Back to :00 = `hil_bridge_phase.sh SPOT-31593C utc` (also the restore). **Each use needs Nick's bridge OK in the
Test Engineer chat.**

### Arms
| arm | window | wakes | when |
|---|---|---|---|
| B | :15 (uptime timebase) | 12 consecutive | first |
| A | :00 (UTC, production) | 12 consecutive | after B (the switch back is the restore) |
The G4 hourly wakes of 10/3 04–15Z on this rig are an extra :00 reference (mixed media, so not pooled).

### Measured per wake
| metric | source |
|---|---|
| queue-full count (`Queue MS_Q_CELLULAR_ONLY is full`, 2 lines per lost msg) | console (`hil_wake_report.sh`) |
| first-send loss: chunks + bytes missing at window+30 min, and WHERE in the clip | backend heal-candidates / media row |
| first-send complete (y/n), D1 minutes | `hil_media_table.py` |
| report minute + health-check minute, and whether either overlapped the burst | console (LEGACY len 50, `[ORC] Running health check!`) |
| command arrival minute vs report minute (hub.sync hypothesis) | `hil_cmd_ledger.py` (only the heals: no test commands) |
| heal commands + chunks; wake→halt; Spotter events (reset, charger, BusV) | console, backend, `hil_rig_health` |

### "Stable" (the gate to phase 2)
An arm is **stable** when 12 consecutive wakes satisfy all of:
1. 12/12 wakes happened (bus on, Pi up, START seen), 0 Spotter resets, 0 charger/thermal CRIT in `hil_rig_health`;
2. queue-full per wake within **median ± max(5, 50 %)** of that arm's own median (no wake an outlier beyond it);
3. first-send loss ≤ 10 % of chunks on ≥ 10/12 wakes;
4. wake→halt ≤ 540 s on 12/12.
A wake with an external event (reset, power move, backend outage) is logged as an event and the 12-count restarts.
Phase 1 is done when arm A (:00) is stable — that arm is the reference for phase 2. Arm B's result (stable or
not, and the A-vs-B difference in queue-full, first-send loss and D1) is reported, not required.

## Phase 2 (GATED: only after phase 1 is stable, and after the wire-contract items below are cleared with Nick)

### Hypothesis (Nick)
The Spotter's cellular queue drains **per message**, not per byte. If so, bigger messages move more bytes per hour
and see fewer queue-full drops. If it drains per byte, bigger messages don't help and each loss costs more
(one lost 1200 B message = ~3× the bytes to heal of a 402 B one).

### Prior evidence (to re-read before running)
- Sprint10 Phase E (2026-07-29): loss is a 5-min grid blackout event; size 300 vs 384 chars made no significant
  difference (implied blackout D 9.8 s vs 9.0 s); "message_cap is not the lever".
- Sprint09: a ~400 B cliff measured on 10-message bursts (not reproduced at 200-message scale in Sprint10).
- `BM_Devel_Pi/bm_port.py` comment: `image_buffer_size: 960` was once the "production large-message cellular-only"
  setting — find out why it went to 384.

### Design: step test
| step | `uplink.chunk_chars` | wire message (≈ chars + `<I{key}.{i}/{M}>` ~18 B) | data per msg |
|---|---|---|---|
| S1 (= phase-1 arm A) | 384 | 402 B | 288 B |
| S2 | 580 | ≈ 600 B | 435 B |
| S3 | 880 | ≈ 900 B | 660 B |
| S4 | 1180 | ≈ 1200 B (Nick's cellular-only max) | 885 B |
chunk_chars stays a multiple of 4 (whole base64 quanta). **Same clip bytes per step** (fix the video payload in
bytes, e.g. ≈ 36 kB = today's cap-126 clip, so messages per clip = 126 / 84 / 55 / 41), same 1.3 s pacing, same
window, same :00 phase. ≥ 6 wakes per step (12 if the variance in phase 1 demands it), order S1→S4→S2→S3 to break
time-of-day trends, S1 repeated at the end.

### Measured per wake (per step)
bytes delivered complete at first send, messages accepted per minute (console `Submitted … cell-only queue` vs
queue-full), queue-full events, first-send loss in messages AND bytes, heal commands + heal bytes, D1 minutes,
wake→halt. Decision rule: per-message drain is supported if queue-full per wake stays flat (± phase-1 band) while
bytes per message grow, and bytes delivered per wake rise with size.

### What has to change before phase 2 (none of it done; each item is a check or a change)
| # | item | status / what to do |
|---|---|---|
| 1 | camera chunk-size config | `uplink.chunk_chars` exists (INT 1–1200, guard SERVICE, wire_visible) and the RC encoder sizes messages from it. It is a **service key: the backend refuses it** (needs Nick's signature key; `command_outbound.SERVICE_KEYS`). → set it per step by Nick's signed command, or on the bench over the console/ssh with Nick's OK. Check `message_cap` semantics (messages, not bytes): add a byte-equivalent cap per step or fix the clip bytes. |
| 2 | keyed chunk format `<I{key}.{i}/{M}>` | self-describing (index / total), size-independent in principle. Check: the decimal width of `i`/`M` (fewer messages → fine), the header length budget at 1180 chars (≤ 1200 B total) |
| 3 | backend reassembly | registry text says "the backend decoder assumes it" (chunk_chars). Verify in nvd ingest that reassembly concatenates by index without a fixed size, and that a media's chunks are all one size (no mixed-size rows) |
| 4 | heals | an rsd names chunk INDICES of the original send, so the resend must use the **same chunk size as the original clip** (the sent record must carry it; check `rc_heal` / sent.json). The heal command JSON limit (234 B / 256 B console line) is about ranges, not chunk size: fewer, larger chunks → shorter ranges (helps). Heal cap 40 chunks/wake becomes 40 × 885 B at S4: re-check the 480 s budget (1.2 s per heal chunk measured at 384) |
| 5 | Pi → mote → Spotter path | the mote firmware (nereus_cam, bm #76) and BM pub max payload: confirm ≥ 1200 B frames pass the mote unsplit (bm_mote_custom_apps source not in the local checkout) |
| 6 | Spotter / Sofar | Spotter `spotter/transmit-data` cell-only max length (Nick: 1200 B), Notecard note size, Sofar API exposure of > 402 B messages; 2-slot cellular queue unchanged |
| 7 | wire contract | items 1–6 touch the wire (chunk size on air). **Any wire-contract change needs Nick's OK** before the first S2 wake |

## Restore (end of the sprint)
`hil_bridge_phase.sh SPOT-31593C utc` (if B was last), `uplink.chunk_chars` back to 384 (phase 2), `mode.media` /
cap as before the sprint, read-back + `hil_unit_snapshot.sh` equal to the pre-sprint snapshot.
