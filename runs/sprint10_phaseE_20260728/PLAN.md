# Phase E run plan — 2026-07-28/29 bench

Deviation record + exact commands for this execution of `PHASE_E.md`.
Written BEFORE the run so the split is not retro-fitted to the results.

## Bench state at start

| Item | Value |
|---|---|
| Start (UTC) | 2026-07-29 ~00:28Z |
| bmcam003 | `pi@100.103.35.24`, SPOT-33507C (bridge node `c3c564b91856226c`) |
| bmcam000 | `pi@100.119.14.92`, SPOT-31593C (bridge node `0e582dd12c1e1480`) |
| Spotter FW | v2.16.6 (both, from console banner) |
| Code | `development@9330779`; branch sits on `origin/development` after PR #17 merged 00:23Z |
| Console capture | `tools/spotter_serial_monitor.py` → `~/spotter_logs/`, both ports OPEN 00:28:21Z |
| Notecard fill at start | 5 % (SPOT-33507C, console 00:40:00Z) |

## Deviations from PHASE_E.md (and why)

1. **Spotters forced continuously-on before the natural power window**
   (Nick, 00:38Z). PHASE_E §3.4 anticipated this; Nick chose to issue it
   immediately rather than wait ~30 min for the next 20/40 window.
   *Consequence handled:* a forced boot races the `@reboot` cycle's
   `power_halt` (a halt onto a permanently-powered bus is one-way,
   finding 004). Mitigations applied to `catch_awake_disarm.sh`: poll
   tightened 10 s → 3 s, and all three disarm steps collapsed into ONE
   ssh round trip with `pkill -TERM` FIRST (inverting §3.1/§3.2 order —
   the running cycle's halt is the imminent threat and `@reboot` cron has
   already fired by definition, so it cannot start a second cycle).
   Units powered one at a time, bmcam003 first, so a flaw in the catch
   costs the bench mule and not both units.

2. **Matrix split rebalanced.** PHASE_E's suggested split is
   time-lopsided: FULLA ≈ 58 min of wall clock against FULLB ≈ 122 min,
   so the bench sits idle on one unit for an hour. Re-split to finish
   both units at ≈ 115 min, and DISC (37 min, bmcam003) runs concurrently
   with FULLB on bmcam000 rather than in series with everything.

3. **One deliberate overlap cell added: `200@2000` runs on BOTH units.**
   The headline deliverable is a loss-vs-delay curve, but the approved
   split already stitches that curve across two Spotters (e.g. at count
   300, delays 1.0/1.5 on one unit and 2.0–5.0 on the other), so a
   per-unit difference in signal or Notecard sync cadence would show up
   as a step in the curve and be indistinguishable from real physics.
   The overlap cell measures that unit offset directly. Cost: +6.7 min.
   DISC adds two more free overlap points — `200@1000` and `200@3000`
   run on bmcam003 in DISC and on bmcam000 in FULLB.

Nothing else changes: size fixed at 384 chars, `--drain-s 300`, all 14
cells of the approved matrix present.

## Coverage check (14 required cells, all present)

| delay ↓ / count → | 100 | 200 | 300 |
|---|---|---|---|
| 1.0 s | FULLB | FULLB + **DISC** | FULLA |
| 1.5 s | FULLA | FULLA | FULLA |
| 2.0 s | FULLB | **FULLA + FULLB** | FULLB |
| 3.0 s | FULLB | FULLB + **DISC** | FULLB |
| 4.0 s | — | **DISC** | FULLB |
| 5.0 s | — | — | FULLA (historical control) |

Bold = cell measured on both units (cross-unit calibration).

## Commands

```bash
# Step 1 — discriminator, bmcam003 (~37 min)
ssh pi@100.103.35.24 "cd /home/pi/BM_Devel_Pi && nohup python3 -u test_queue_drain.py \
  --run DISC --matrix '200@1000,200@3000,200@4000' --drain-s 300 \
  --out-dir /home/pi/phaseE > /home/pi/phaseE_disc.log 2>&1 &"

# Step 2a — FULLB, bmcam000, starts immediately (9 bursts, ~115 min)
ssh pi@100.119.14.92 "cd /home/pi/BM_Devel_Pi && nohup python3 -u test_queue_drain.py \
  --run FULLB --matrix '300@2000,300@4000,100@1000,200@3000,300@3000,200@2000,100@3000,200@1000,100@2000' \
  --drain-s 300 --out-dir /home/pi/phaseE > /home/pi/phaseE_fullb.log 2>&1 &"

# Step 2b — FULLA, bmcam003, after DISC finishes (6 bursts, ~77 min)
ssh pi@100.103.35.24 "cd /home/pi/BM_Devel_Pi && nohup python3 -u test_queue_drain.py \
  --run FULLA --matrix '300@1000,300@5000,200@1500,100@1500,300@1500,200@2000' \
  --drain-s 300 --out-dir /home/pi/phaseE > /home/pi/phaseE_fulla.log 2>&1 &"
```

Burst order within each run interleaves long and short bursts so pacing
is not confounded with elapsed time into the session.

## Expected wall clock

| Unit | Work | Send | Drains | Total |
|---|---|---|---|---|
| bmcam003 | DISC | 26.7 min | 10 min | 36.7 min |
| bmcam003 | FULLA | 51.7 min | 25 min | 76.7 min |
| bmcam003 | **both, serial** | | | **~113 min** |
| bmcam000 | FULLB | 75.0 min | 40 min | **~115 min** |

Plus 30 min backend lag before the final analyzer pass. Analyzer is
idempotent — re-run it to catch stragglers before concluding loss.
