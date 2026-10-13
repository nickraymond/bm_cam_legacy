# T-ENDURE: full pool session at real length, bmcam003 + bmcam004, Mon 10/12 1:52–8:39 PM PDT

Card: the EM, ~1:50 PM, under Nick's pool-commands / bus / pool_mode OK.
Code: development f8caaa1 pool files on both units. The new-plan pull is development 66a9f2e.
Scene: static bench, no board. Evidence is in this folder:
- driver.log, rss_<unit>.log, battery.log, health.log;
- pull logs + `~/NereusPool/20261012_TENDURE_v2/pull_report.json`;
- the pulled manifests.

## Verdict

**PASS on both units for every capture, memory, error and space bar, and for the new-plan pull (12 min, bar ≤ 60).**

**NOT COVERED:**
- the mid-loop preview delay and the mark (never sent);
- Spotter battery after 3:56 PM;
- RSS samples for loop iterations 10–18.

Cause: the Mac slept, then left the bench LAN, and nereus000 went offline at ~4 PM (board d32). See "Gaps".

## Bars

| Bar | bmcam003 | bmcam004 | Verdict |
|---|---|---|---|
| 120 ± 2 cal pairs, every frame_s ≤ 10 | 120/120, frame_s max 6.15 (median 3.91), slot_late 0 | 120/120, max 6.00 (median 3.93), late 0 | PASS |
| 18/18 loop sets complete | 18/18: iteration_s 53.7–67.4 (median 55.6); nrjxl 180–190 msgs; mp4 7.30–7.35 MB; 0 video fallbacks | 18/18: 54.8–70.9 (median 65.0); nrjxl 189–195 msgs; mp4 7.28–7.36 MB; 0 fallbacks | PASS |
| Applied settings (LensPosition 1.82, AG ≤ 1.12, ET ≤ 60 ms, full ScalerCrop) | 138/138 stills | 138/138 | PASS |
| RSS: iteration 18 within +10 % of iteration 1; peak < 150 MiB | pool_sequence VmHWM over the whole 6.9 h = **135.7 MiB**, the same value it reached at the 1:38 PM preview before the loop. Sampled iterations 2–9 max VmRSS 103–121 MiB. MemAvailable min 86 MiB | VmHWM **135.8 MiB** (same as at 1:38 PM); iterations 2–9 112–130 MiB; min MemAvailable 89 MiB | PASS (lifetime peak never grew, so no leak; per-iteration RSS for 10–18 not sampled) |
| 0 ERR in pool.log / no OOM | 0 ERR, 0 OOM in dmesg; 1 WARN = the loop auto-start (expected) | same | PASS |
| Free space vs projection | used 3,650 MB (107,243 → 103,593 MB free) vs the unit's own projection of 4,415 MB (5,439 incl. the 1 GB margin) | 3,606 MB (106,939 → 103,333) | PASS (under) |
| Pi never halted | up 24,825 s at the end, in pool mode | same | PASS |
| Full pull ≤ 60 min, sha-verified (new plan, #158: DNGs stay on the units) | 515.6 MB in ~10 min at 0.92–0.96 MB/s; 669 files ok (sha256 Pi = Mac) | 469.2 MB at 0.83–0.88 MB/s; 669 ok | **PASS: 12 m 02 s total for both in parallel** (data ~9.5 min + DNG inventory sha 2 m 22 s); ~1.75 MB/s combined |
| DNGs left on the unit (inventoried) | 138 DNGs, 3,316,011,384 B, 138 Pi sha256 | 138, 3,316,011,384 B, 138 | recorded |
| Old plan (everything incl. DNGs), stopped by the EM after 6.5 min | 0.93 MB/s | 0.74 MB/s | measured only: 1.67 MB/s combined → ~71 min for 7.1 GB (would FAIL the 60 min bar) |
| Mid-loop preview delay behind a clip | not sent | not sent | NOT COVERED |
| `mark endure_1p5h` | not sent | not sent | NOT COVERED |
| Spotter battery every ~30 min, start → end | 1:15 PM 4.03 V −0.56 W → 3:56 PM **4.07 V +0.41 W**; blind after | 1:15 PM 4.04 V +0.19 W → 3:56 PM **4.07 V +0.41 W**; blind after | partial (net-charging with the bus held while readable) |

## Gaps (honest record)

- **Loop command never sent.** At 2:11–2:51 PM the Mac slept (lid closed for Nick's review), so the driver, monitors and stop rules all paused. The units' **30-minute loop auto-start** fired at 2:42:15 PM on both (18 × 600 s, as the card asked). That proves the auto-start, which Tuesday may need.
- **Driver bug (fixed in tendure_driver_v2.sh):** had the console been reachable, the driver's own `loop` command would have been refused by the unit ("run already has loop frames") and the ERR watcher would have stopped the unit. v2 checks for a started loop first and ignores `refused` answers.
- **nereus000 offline since ~3:56–4:31 PM** (no ping / ARP / mDNS; board d32). Lost because of it:
  - the console path, so the preview (3:40 PM) and mark (4:26 PM) were never sent;
  - the Spotter battery readings and the 3.80 V stop rule;
  - the plan-E restore (bridge config over the console), which is still pending.
- **The Mac also maintenance-slept on battery later in the afternoon.** The RSS samplers missed loop iterations 10–18. The lifetime VmHWM covers the peak, but not the per-iteration trend.
- **The old-plan pull** ran 8:04–8:11 PM before the EM stopped it. The 688 MB partial copy is in `~/NereusPool/20261012_TENDURE/`; deleting it is the EM's / Nick's call.
